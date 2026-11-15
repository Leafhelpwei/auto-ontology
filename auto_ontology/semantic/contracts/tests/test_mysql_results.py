# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independent result fixtures on actual MySQL 5.7 with ONLY_FULL_GROUP_BY."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
import os
from typing import Any
from uuid import uuid4

import mysql.connector
import pytest

from auto_ontology.connectors.mysql import _parse_connection_string
from auto_ontology.semantic.contracts.compiler import compile_query, quote
from auto_ontology.semantic.contracts.models import (
    GovernedModel,
    MetricQueryRequest,
    Plan,
)
from auto_ontology.semantic.contracts.tests.test_mysql_compiler import (
    mysql_model,
    time_request,
)


@pytest.fixture
def mysql_fixture() -> Iterator[tuple[Any, Any, GovernedModel]]:
    reader_dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_TEST_DSN")
    admin_dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN")
    if not reader_dsn or not admin_dsn:
        pytest.skip("Actual MySQL 5.7 reader and fixture-admin DSNs are required")
    settings = {
        "autocommit": True,
        "use_pure": True,
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
        "time_zone": "+00:00",
        "sql_mode": "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION",
    }
    admin = mysql.connector.connect(**_parse_connection_string(admin_dsn), **settings)
    reader = mysql.connector.connect(**_parse_connection_string(reader_dsn), **settings)
    cursor = admin.cursor()
    prefix = f"core_{uuid4().hex[:16]}_"
    names = {
        name: prefix + name
        for name in ("customer", "product", "sales_order", "order_line")
    }
    physical = {name: quote(value, "mysql") for name, value in names.items()}
    created: list[str] = []
    try:
        cursor.execute("SELECT VERSION()")
        assert str(cursor.fetchone()[0]).startswith("5.7.")
        with reader.cursor() as read:
            read.execute("SET SESSION div_precision_increment = 30")
        definitions = {
            "customer": "customer_id bigint PRIMARY KEY, customer_name varchar(100)",
            "product": "product_id bigint PRIMARY KEY, product_name varchar(100) NOT NULL",
            "sales_order": "order_id bigint PRIMARY KEY, customer_id bigint NOT NULL, status_code varchar(20) NOT NULL, currency_code varchar(10) NOT NULL, created_at datetime(6) NOT NULL, confirmed_at datetime(6)",
            "order_line": "order_line_id bigint PRIMARY KEY, order_id bigint NOT NULL, product_id bigint NOT NULL, quantity decimal(30,6) NOT NULL, unit_price decimal(30,6) NOT NULL",
        }
        for name, definition in definitions.items():
            cursor.execute(
                f"CREATE TABLE {physical[name]} ({definition}) ENGINE=InnoDB"
            )
            created.append(name)
        cursor.execute(
            f"INSERT INTO {physical['customer']} VALUES (1,NULL),(2,'乙客户')"
        )
        cursor.execute(
            f"INSERT INTO {physical['product']} VALUES (101,'零件甲'),(102,'零件乙')"
        )
        cursor.execute(
            f"INSERT INTO {physical['sales_order']} VALUES (1001,1,'CONFIRMED','CNY','2026-11-01 08:00','2026-11-01 09:00'),(1002,2,'CONFIRMED','USD','2026-11-02 08:00','2026-11-02 09:00'),(1003,1,'CANCELLED','CNY','2026-11-03 08:00','2026-11-03 09:00')"
        )
        cursor.execute(
            f"INSERT INTO {physical['order_line']} VALUES (1,1001,101,2,100),(2,1001,102,1,50),(3,1002,101,1,100),(4,1003,102,1,50)"
        )
        data = mysql_model().model_dump(mode="json")
        for source in data["sources"]:
            source["schema_name"] = _parse_connection_string(reader_dsn)["database"]
            source["table_name"] = names[source["table_name"]]
        yield admin, reader, GovernedModel.model_validate(data)
    finally:
        for name in reversed(created):
            cursor.execute(f"DROP TABLE {physical[name]}")
        cursor.close()
        reader.close()
        admin.close()


def rows(reader: Any, plan: Plan) -> list[dict[str, Any]]:
    with reader.cursor(dictionary=True) as cursor:
        cursor.execute(plan.sql, plan.params)
        return cursor.fetchall()


def request(**updates: Any) -> MetricQueryRequest:
    return MetricQueryRequest.model_validate(
        {
            "metric_ids": [
                "confirmed_amount",
                "cancelled_amount",
                "confirmed_order_count",
            ],
            "dimensions": ["currency_code", "customer_name"],
            "all_time": True,
            **updates,
        }
    )


def test_mysql_distinct_filters_and_null_group_alignment(
    mysql_fixture: tuple[Any, Any, GovernedModel],
) -> None:
    _, reader, model = mysql_fixture
    assert rows(reader, compile_query(model, request())) == [
        {
            "currency_code": "CNY",
            "customer_name": None,
            "confirmed_amount": Decimal(250),
            "cancelled_amount": Decimal(50),
            "confirmed_order_count": 1,
        },
        {
            "currency_code": "USD",
            "customer_name": "乙客户",
            "confirmed_amount": Decimal(100),
            "cancelled_amount": None,
            "confirmed_order_count": 1,
        },
    ]


def test_mysql_left_join_target_filter_stays_in_on(
    mysql_fixture: tuple[Any, Any, GovernedModel],
) -> None:
    _, reader, model = mysql_fixture
    data = model.model_dump(mode="json")
    data["sources"][2]["required_filters"] = [
        {
            "field": {"source_id": "customers", "field_id": "customer_name"},
            "operator": "eq",
            "value": "absent",
        }
    ]
    result = rows(reader, compile_query(GovernedModel.model_validate(data), request()))
    assert [
        (row["currency_code"], row["customer_name"], row["confirmed_amount"])
        for row in result
    ] == [("CNY", None, Decimal(250)), ("USD", None, Decimal(100))]


def test_mysql_local_datetime_half_open_range_and_grouping(
    mysql_fixture: tuple[Any, Any, GovernedModel],
) -> None:
    admin, reader, model = mysql_fixture
    order = quote(model.sources[1].table_name, "mysql")
    line = quote(model.sources[0].table_name, "mysql")
    with admin.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {order} VALUES (1005,2,'CONFIRMED','USD','2026-11-03 00:00',NULL)"
        )
        cursor.execute(f"INSERT INTO {line} VALUES (5,1005,101,1,999)")
    model = model.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(
                    update={
                        "time_field_id": "created_at",
                        "time_role": "created",
                        "supported_granularities": ("day",),
                    }
                )
                for metric in model.metrics
            )
        }
    )
    query = time_request(
        "2026-11-01T00:00:00+08:00", "2026-11-03T00:00:00+08:00", "Asia/Shanghai"
    )
    assert rows(reader, compile_query(model, query)) == [
        {
            "currency_code": "CNY",
            "__time": datetime(2026, 11, 1),
            "confirmed_amount": Decimal(250),
        },
        {
            "currency_code": "USD",
            "__time": datetime(2026, 11, 2),
            "confirmed_amount": Decimal(100),
        },
    ]


def test_mysql_percent_identifier_and_parameter_value_are_literal(
    mysql_fixture: tuple[Any, Any, GovernedModel],
) -> None:
    admin, reader, model = mysql_fixture
    data = model.model_dump(mode="json")
    renamed = "unit%`price"
    with admin.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {quote(model.sources[0].table_name, 'mysql')} CHANGE unit_price {quote(renamed, 'mysql')} decimal(30,6) NOT NULL"
        )
    data["sources"][0]["fields"][-1]["column_name"] = renamed
    model = GovernedModel.model_validate(data)
    assert rows(reader, compile_query(model, request(dimensions=["currency_code"])))[0][
        "confirmed_amount"
    ] == Decimal(250)
    injected = "乙客户' OR 1=1 -- %(p0)s %"
    query = request(
        filters=[
            {
                "field": {"source_id": "customers", "field_id": "customer_name"},
                "operator": "eq",
                "value": injected,
            }
        ]
    )
    assert rows(reader, compile_query(model, query)) == []


def test_mysql_empty_ungrouped_and_null_division(
    mysql_fixture: tuple[Any, Any, GovernedModel],
) -> None:
    _, reader, model = mysql_fixture
    query = request(
        metric_ids=["confirmed_order_count"],
        dimensions=[],
        filters=[
            {
                "field": {"source_id": "orders", "field_id": "order_id"},
                "operator": "eq",
                "value": -1,
            }
        ],
    )
    assert rows(reader, compile_query(model, query)) == [{"confirmed_order_count": 0}]
    data = model.model_dump(mode="json")
    data["metrics"][0]["expression"] = {
        "kind": "binary",
        "operator": "divide",
        "left": {"kind": "field", "field_id": "unit_price"},
        "right": {"kind": "number", "value": "0"},
    }
    result = rows(
        reader,
        compile_query(
            GovernedModel.model_validate(data),
            request(metric_ids=["confirmed_amount"], dimensions=["currency_code"]),
        ),
    )
    assert all(row["confirmed_amount"] is None for row in result)
