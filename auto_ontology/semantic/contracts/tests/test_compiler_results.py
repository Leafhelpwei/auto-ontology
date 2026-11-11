# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Result-level checks against independent PostgreSQL fixtures.

Set AUTO_ONTOLOGY_GOVERNED_TEST_DSN to run database checks. An absent DSN is
reported as a skip, never represented as evidence of PostgreSQL execution.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal

import psycopg
import pytest
from pydantic import ValidationError

from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
    Plan,
)
from auto_ontology.semantic.contracts.tests.fixtures import (
    F0_SQL,
    f0_model,
    field,
    ref,
    source,
)


@pytest.fixture
def postgres() -> Iterator[tuple[psycopg.Connection, str]]:
    dsn = os.environ.get("AUTO_ONTOLOGY_GOVERNED_TEST_DSN")
    if not dsn:
        pytest.skip("AUTO_ONTOLOGY_GOVERNED_TEST_DSN is not configured")
    schema = f"governed_test_{uuid.uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
        )
        connection.execute(
            psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier(schema)
            )
        )
        try:
            yield connection, schema
        finally:
            connection.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    psycopg.sql.Identifier(schema)
                )
            )


@pytest.fixture
def f0(
    postgres: tuple[psycopg.Connection, str],
) -> tuple[psycopg.Connection, GovernedModel]:
    connection, schema = postgres
    connection.execute(F0_SQL)
    return connection, f0_model(schema)


def request(**updates: object) -> MetricQueryRequest:
    values = {
        "metric_ids": ["confirmed_amount"],
        "dimensions": ["currency_code"],
        "all_time": True,
    }
    values.update(updates)
    return MetricQueryRequest.model_validate(values)


def rows(connection: psycopg.Connection, plan: Plan) -> list[dict]:
    cursor = connection.execute(plan.sql, plan.params)
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def rebuild(model: GovernedModel, change: Callable[[dict], None]) -> GovernedModel:
    data = model.model_dump(mode="json")
    change(data)
    return GovernedModel.model_validate(data)


def test_unknown_metric_is_not_dropped() -> None:
    with pytest.raises(ContractError):
        compile_query(f0_model(), request(metric_ids=["confirmed_amount", "missing"]))


def test_unknown_dimension_is_not_dropped() -> None:
    with pytest.raises(ContractError):
        compile_query(f0_model(), request(dimensions=["currency_code", "missing"]))


def test_currency_aggregation_without_currency_scope_is_rejected() -> None:
    with pytest.raises(ContractError):
        compile_query(f0_model(), request(dimensions=[]))


def test_unknown_filter_is_not_dropped() -> None:
    with pytest.raises(ContractError):
        compile_query(
            f0_model(),
            request(
                filters=[
                    {"field": ref("lines", "missing"), "operator": "eq", "value": 1}
                ]
            ),
        )


def test_extra_query_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        request(having="confirmed_amount > 1000")


def test_fanout_path_is_rejected() -> None:
    model = rebuild(
        f0_model(),
        lambda data: data["relationships"][0].update(cardinality="many_to_many"),
    )
    with pytest.raises(ContractError):
        compile_query(model, request())


def test_multi_fact_metrics_are_rejected() -> None:
    def change(data: dict) -> None:
        payment_source = source(
            "payments",
            "payment",
            ["payment_id"],
            [field("payment_id"), field("amount")],
            "ontology_fixture",
        )
        data["sources"].append(payment_source)
        data["metrics"].append(
            {
                "metric_id": "payment_amount",
                "name": "付款额",
                "description": "独立付款事实",
                "owner_term_id": "payments",
                "source_ids": ["payments"],
                "aggregation": "sum",
                "expression": {"kind": "field", "field_id": "amount"},
                "unit": "currency",
                "fixed_currency": "CNY",
            }
        )

    with pytest.raises(ContractError):
        compile_query(
            rebuild(f0_model(), change),
            request(metric_ids=["confirmed_amount", "payment_amount"]),
        )


def test_source_without_compatible_fields_is_not_silently_used() -> None:
    def change(data: dict) -> None:
        data["sources"][0]["pre_aggregated"] = True

    with pytest.raises(ContractError):
        compile_query(rebuild(f0_model(), change), request())


def test_source_priority_cannot_override_dimension_compatibility() -> None:
    def change(data: dict) -> None:
        original = data["sources"][0]
        alternative = {
            **original,
            "source_id": "limited_lines",
            "catalog_table_id": "fixture-table-limited-lines",
            "priority": 0,
            "dimensions": [
                {
                    "dimension_id": "currency_code",
                    "field": ref("orders", "currency_code"),
                    "relationship_path": ["limited_line_order"],
                }
            ],
        }
        data["sources"].append(alternative)
        data["relationships"].append(
            {
                **data["relationships"][0],
                "relationship_id": "limited_line_order",
                "source_id": "limited_lines",
            }
        )
        for metric in data["metrics"]:
            metric["source_ids"] = ["limited_lines", "lines"]

    plan = compile_query(
        rebuild(f0_model(), change),
        request(dimensions=["currency_code", "customer_name"]),
    )
    assert "lines" in plan.source_ids
    assert "limited_lines" not in plan.source_ids


def test_sql_values_remain_parameters() -> None:
    hostile = "anything' OR 1=1 --"
    plan = compile_query(
        f0_model(),
        request(
            filters=[
                {
                    "field": ref("customers", "customer_name"),
                    "operator": "eq",
                    "value": hostile,
                }
            ]
        ),
    )
    assert hostile not in plan.sql
    assert hostile in plan.params.values()
    assert plan.datasource_id == "fixture-postgres"
    assert plan.model_fingerprint == f0_model().fingerprint()


def test_same_request_produces_same_plan() -> None:
    model, query = f0_model(), request()
    assert compile_query(model, query) == compile_query(model, query)


def test_each_metric_retains_its_filter(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    result = rows(
        connection,
        compile_query(
            model, request(metric_ids=["confirmed_amount", "cancelled_amount"])
        ),
    )
    actual = {
        row["currency_code"]: (row["confirmed_amount"], row["cancelled_amount"])
        for row in result
    }
    assert actual == {
        "CNY": (Decimal("250"), Decimal("50")),
        "USD": (Decimal("100"), None),
    }


def test_distinct_order_count_is_not_joined_row_count(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    result = rows(
        connection,
        compile_query(
            model, request(metric_ids=["confirmed_order_count"], dimensions=[])
        ),
    )
    assert result == [{"confirmed_order_count": 2}]
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sales_order o JOIN order_line l USING(order_id) WHERE status_code='CONFIRMED'"
        ).fetchone()[0]
        == 3
    )


def test_null_group_keys_align_across_metrics(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    connection.execute("UPDATE customer SET customer_name=NULL")
    result = rows(
        connection,
        compile_query(
            model,
            request(
                metric_ids=["confirmed_amount", "cancelled_amount"],
                dimensions=["currency_code", "customer_name"],
            ),
        ),
    )
    actual = {
        (row["currency_code"], row["customer_name"]): (
            row["confirmed_amount"],
            row["cancelled_amount"],
        )
        for row in result
    }
    assert len(result) == 2
    assert actual == {
        ("CNY", None): (Decimal("250"), Decimal("50")),
        ("USD", None): (Decimal("100"), None),
    }


def test_left_dimension_required_filter_preserves_fact_rows(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0

    def change(data: dict) -> None:
        data["sources"][2]["required_filters"] = [
            {"field": ref("customers", "customer_id"), "operator": "ne", "value": 1}
        ]

    model = rebuild(model, change)
    result = rows(
        connection,
        compile_query(model, request(dimensions=["currency_code", "customer_name"])),
    )
    actual = {
        (row["currency_code"], row["customer_name"]): row["confirmed_amount"]
        for row in result
    }
    assert actual == {("CNY", None): Decimal("250"), ("USD", "乙客户"): Decimal("100")}


def test_sort_and_limit_apply_after_aggregation(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    result = rows(
        connection,
        compile_query(
            model,
            request(
                order_by=[{"field_id": "confirmed_amount", "direction": "desc"}],
                limit=1,
            ),
        ),
    )
    assert result == [{"currency_code": "CNY", "confirmed_amount": Decimal("250")}]


def test_composite_preserves_each_dependency_filter(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0

    def change(data: dict) -> None:
        data["metrics"].append(
            {
                "metric_id": "confirmation_ratio",
                "name": "确认金额与取消金额之比",
                "description": "在同币种内比较两个不同状态的商品金额。",
                "owner_term_id": "lines",
                "source_ids": ["lines"],
                "kind": "composite",
                "expression": {
                    "kind": "binary",
                    "operator": "divide",
                    "left": {"kind": "metric", "metric_id": "confirmed_amount"},
                    "right": {"kind": "metric", "metric_id": "cancelled_amount"},
                },
                "supported_dimensions": ["currency_code"],
                "unit": "number",
                "zero_division": "null",
            }
        )

    result = rows(
        connection,
        compile_query(
            rebuild(model, change), request(metric_ids=["confirmation_ratio"])
        ),
    )
    assert {row["currency_code"]: row["confirmation_ratio"] for row in result} == {
        "CNY": Decimal("5"),
        "USD": None,
    }


def test_empty_sum_and_count_follow_contract(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    query = request(
        metric_ids=["confirmed_order_count"],
        dimensions=[],
        filters=[
            {"field": ref("orders", "status_code"), "operator": "eq", "value": "NONE"}
        ],
    )
    assert rows(connection, compile_query(model, query)) == [
        {"confirmed_order_count": 0}
    ]
    grouped = request(
        filters=[
            {"field": ref("orders", "status_code"), "operator": "eq", "value": "NONE"}
        ]
    )
    assert rows(connection, compile_query(model, grouped)) == []


def test_composite_metrics_divide_aggregates_not_row_ratios(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute(
        "CREATE TABLE ratio_rows(id bigint PRIMARY KEY, numerator numeric, denominator numeric); INSERT INTO ratio_rows VALUES (1,1,2),(2,9,10)"
    )
    model = ratio_model(schema)
    result = rows(
        connection,
        compile_query(model, MetricQueryRequest(metric_ids=("ratio",), all_time=True)),
    )
    assert result[0]["ratio"] == pytest.approx(Decimal(10) / Decimal(12))
    assert result[0]["ratio"] != Decimal("0.7")
    connection.execute("UPDATE ratio_rows SET denominator=0")
    assert rows(
        connection,
        compile_query(model, MetricQueryRequest(metric_ids=("ratio",), all_time=True)),
    ) == [{"ratio": None}]


def ratio_model(schema: str) -> GovernedModel:
    base = source(
        "ratios",
        "ratio_rows",
        ["id"],
        [field("id"), field("numerator"), field("denominator")],
        schema,
    )
    metrics = [
        {
            "metric_id": name,
            "name": name,
            "description": name,
            "owner_term_id": "ratios",
            "source_ids": ["ratios"],
            "aggregation": "sum",
            "expression": {"kind": "field", "field_id": name},
            "unit": "number",
        }
        for name in ["numerator", "denominator"]
    ]
    metrics.append(
        {
            "metric_id": "ratio",
            "name": "ratio",
            "description": "总分子除以总分母",
            "owner_term_id": "ratios",
            "source_ids": ["ratios"],
            "kind": "composite",
            "expression": {
                "kind": "binary",
                "operator": "divide",
                "left": {"kind": "metric", "metric_id": "numerator"},
                "right": {"kind": "metric", "metric_id": "denominator"},
            },
            "unit": "number",
            "zero_division": "null",
        }
    )
    return GovernedModel.model_validate(
        {
            "model_id": "fixture-ratio",
            "name": "ratio",
            "sources": [base],
            "metrics": metrics,
        }
    )


def test_time_range_uses_explicit_role_timezone_and_bounds(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute("""
    CREATE TABLE events(id bigint PRIMARY KEY, amount numeric, confirmed_at timestamptz, created_at timestamptz);
    INSERT INTO events VALUES
    (1,10,'2026-10-31 23:59:59+08','2026-11-01 00:00:00+08'),
    (2,20,'2026-11-01 00:00:00+08','2026-10-01 00:00:00+08'),
    (3,30,'2026-11-30 23:59:59+08','2026-11-01 00:00:00+08'),
    (4,40,'2026-12-01 00:00:00+08','2026-11-01 00:00:00+08');
    """)
    base = source(
        "events",
        "events",
        ["id"],
        [
            field("id"),
            field("amount"),
            field("confirmed_at", "datetime"),
            field("created_at", "datetime"),
        ],
        schema,
    )
    metric = {
        "metric_id": "amount",
        "name": "amount",
        "description": "按确认时间金额",
        "owner_term_id": "events",
        "source_ids": ["events"],
        "aggregation": "sum",
        "expression": {"kind": "field", "field_id": "amount"},
        "unit": "currency",
        "fixed_currency": "CNY",
        "time_field_id": "confirmed_at",
        "time_role": "confirmed",
        "supported_granularities": ["day", "month"],
    }
    model = GovernedModel.model_validate(
        {
            "model_id": "fixture-time",
            "name": "time",
            "sources": [base],
            "metrics": [metric],
        }
    )
    query = MetricQueryRequest.model_validate(
        {
            "metric_ids": ["amount"],
            "time": {
                "start": "2026-11-01T00:00:00+08:00",
                "end_exclusive": "2026-12-01T00:00:00+08:00",
                "timezone": "Asia/Shanghai",
                "time_role": "confirmed",
            },
        }
    )
    plan = compile_query(model, query)
    assert rows(connection, plan) == [{"amount": Decimal("50")}]
    assert any(isinstance(value, datetime) for value in plan.params.values())
    assert "now()" not in plan.sql.lower()
    assert "current_date" not in plan.sql.lower()


def test_all_composite_join_keys_are_applied(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute("""
    CREATE TABLE tenant_orders(tenant text, order_id bigint, status text, PRIMARY KEY(tenant,order_id));
    CREATE TABLE tenant_lines(line_id bigint PRIMARY KEY, tenant text, order_id bigint, amount numeric);
    INSERT INTO tenant_orders VALUES ('A',1,'CONFIRMED'),('B',1,'CONFIRMED');
    INSERT INTO tenant_lines VALUES (1,'A',1,10),(2,'B',1,20);
    """)
    lines = source(
        "tenant_lines",
        "tenant_lines",
        ["line_id"],
        [
            field("line_id"),
            field("tenant", "string"),
            field("order_id"),
            field("amount"),
        ],
        schema,
    )
    orders = source(
        "tenant_orders",
        "tenant_orders",
        ["tenant", "order_id"],
        [field("tenant", "string"), field("order_id"), field("status", "string")],
        schema,
    )
    lines["dimensions"] = [
        {
            "dimension_id": "tenant",
            "field": ref("tenant_orders", "tenant"),
            "relationship_path": ["tenant_order"],
        }
    ]
    relation = {
        "relationship_id": "tenant_order",
        "source_id": "tenant_lines",
        "target_id": "tenant_orders",
        "role": "order",
        "keys": [
            {"source_field_id": "tenant", "target_field_id": "tenant"},
            {"source_field_id": "order_id", "target_field_id": "order_id"},
        ],
        "cardinality": "many_to_one",
        "join_type": "inner",
    }
    metric = {
        "metric_id": "amount",
        "name": "amount",
        "description": "租户金额",
        "owner_term_id": "tenant_lines",
        "source_ids": ["tenant_lines"],
        "aggregation": "sum",
        "expression": {"kind": "field", "field_id": "amount"},
        "supported_dimensions": ["tenant"],
        "unit": "currency",
        "fixed_currency": "CNY",
    }
    model = GovernedModel.model_validate(
        {
            "model_id": "fixture-tenants",
            "name": "tenants",
            "sources": [lines, orders],
            "relationships": [relation],
            "metrics": [metric],
        }
    )
    query = MetricQueryRequest(
        metric_ids=("amount",), dimensions=("tenant",), all_time=True
    )
    actual = {
        row["tenant"]: row["amount"]
        for row in rows(connection, compile_query(model, query))
    }
    assert actual == {"A": Decimal("10"), "B": Decimal("20")}


def test_percent_in_postgres_identifiers_survives_dbapi_binding(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute(
        'CREATE TABLE "facts%data" (id bigint PRIMARY KEY, "amount%" numeric, "kind%" text); '
        "INSERT INTO \"facts%data\" VALUES (1,5,'A'),(2,7,'A')"
    )
    base = source(
        "percent_source",
        "facts%data",
        ["id"],
        [
            field("id"),
            {**field("amount"), "column_name": "amount%"},
            {**field("kind", "string"), "column_name": "kind%"},
        ],
        schema,
    )
    base["dimensions"] = [
        {"dimension_id": "group%", "field": ref("percent_source", "kind")}
    ]
    metric = {
        "metric_id": "amount%",
        "name": "amount",
        "description": "Literal percent identifiers",
        "owner_term_id": "percent_source",
        "source_ids": ["percent_source"],
        "aggregation": "sum",
        "expression": {"kind": "field", "field_id": "amount"},
        "supported_dimensions": ["group%"],
    }
    model = GovernedModel.model_validate(
        {
            "model_id": "fixture-percent",
            "name": "percent",
            "sources": [base],
            "metrics": [metric],
        }
    )
    query = MetricQueryRequest(
        metric_ids=("amount%",), dimensions=("group%",), all_time=True
    )
    assert rows(connection, compile_query(model, query)) == [
        {"group%": "A", "amount%": Decimal("12")}
    ]


@pytest.mark.parametrize(
    "literal", ["1.000000000000000000000001", Decimal("1.000000000000000000000001")]
)
def test_exact_decimal_money_filter_is_not_coerced_to_float(
    postgres: tuple[psycopg.Connection, str],
    literal: str | Decimal,
) -> None:
    connection, schema = postgres
    connection.execute(
        "CREATE TABLE precise_money(id bigint PRIMARY KEY, amount numeric); INSERT INTO precise_money VALUES (1,1.000000000000000000000001),(2,1.0)"
    )
    base = source(
        "money", "precise_money", ["id"], [field("id"), field("amount")], schema
    )
    metric = {
        "metric_id": "money_amount",
        "name": "money",
        "description": "Exact numeric filtering",
        "owner_term_id": "money",
        "source_ids": ["money"],
        "aggregation": "sum",
        "expression": {"kind": "field", "field_id": "amount"},
        "unit": "currency",
        "fixed_currency": "CNY",
    }
    model = GovernedModel.model_validate(
        {
            "model_id": "fixture-precision",
            "name": "precision",
            "sources": [base],
            "metrics": [metric],
        }
    )
    query = MetricQueryRequest.model_validate(
        {
            "metric_ids": ["money_amount"],
            "all_time": True,
            "filters": [
                {"field": ref("money", "amount"), "operator": "eq", "value": literal}
            ],
        }
    )
    plan = compile_query(model, query)
    assert Decimal("1.000000000000000000000001") in plan.params.values()
    assert rows(connection, plan) == [
        {"money_amount": Decimal("1.000000000000000000000001")}
    ]


def employee_model(schema: str, *, distinct_binding: bool) -> GovernedModel:
    employees = source(
        "employees",
        "employees",
        ["id"],
        [
            field("id"),
            field("manager_id", nullable=True),
            field("name", "string"),
            field("amount"),
        ],
        schema,
    )
    target_id = "managers" if distinct_binding else "employees"
    sources = [employees]
    if distinct_binding:
        sources.append(
            source(
                "managers",
                "employees",
                ["id"],
                [field("id"), field("name", "string")],
                schema,
            )
        )
    employees["dimensions"] = [
        {
            "dimension_id": "manager_name",
            "field": ref(target_id, "name"),
            "relationship_path": ["manager"],
        }
    ]
    relationship = {
        "relationship_id": "manager",
        "source_id": "employees",
        "target_id": target_id,
        "role": "manager",
        "keys": [{"source_field_id": "manager_id", "target_field_id": "id"}],
        "cardinality": "many_to_one",
        "join_type": "left",
    }
    metric = {
        "metric_id": "employee_amount",
        "name": "employee_amount",
        "description": "按经理分组",
        "owner_term_id": "employees",
        "source_ids": ["employees"],
        "aggregation": "sum",
        "expression": {"kind": "field", "field_id": "amount"},
        "supported_dimensions": ["manager_name"],
    }
    return GovernedModel.model_validate(
        {
            "model_id": "fixture-employees",
            "name": "employees",
            "sources": sources,
            "relationships": [relationship],
            "metrics": [metric],
        }
    )


def test_same_table_roles_with_distinct_source_bindings_are_valid(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute(
        "CREATE TABLE employees(id bigint PRIMARY KEY, manager_id bigint, name text, amount numeric); INSERT INTO employees VALUES (1,2,'Alice',10),(2,NULL,'Boss',20)"
    )
    model = employee_model(schema, distinct_binding=True)
    query = MetricQueryRequest(
        metric_ids=("employee_amount",), dimensions=("manager_name",), all_time=True
    )
    result = rows(connection, compile_query(model, query))
    assert {row["manager_name"]: row["employee_amount"] for row in result} == {
        "Boss": Decimal("10"),
        None: Decimal("20"),
    }


def test_float_filter_does_not_downcast_numeric_database_values(
    postgres: tuple[psycopg.Connection, str],
) -> None:
    connection, schema = postgres
    connection.execute(
        "CREATE TABLE decimal_compare(id bigint PRIMARY KEY, amount numeric); "
        "INSERT INTO decimal_compare VALUES (1,0.1),(2,0.10000000000000000000001)"
    )
    base = source(
        "decimal_compare",
        "decimal_compare",
        ["id"],
        [field("id"), field("amount")],
        schema,
    )
    metric = {
        "metric_id": "matching_count",
        "name": "matching_count",
        "description": "Exact numeric equality",
        "owner_term_id": "decimal_compare",
        "source_ids": ["decimal_compare"],
        "aggregation": "count",
        "unit": "count",
    }
    model = GovernedModel.model_validate(
        {
            "model_id": "fixture-float-filter",
            "name": "float filter",
            "sources": [base],
            "metrics": [metric],
        }
    )
    query = MetricQueryRequest.model_validate(
        {
            "metric_ids": ["matching_count"],
            "all_time": True,
            "filters": [
                {
                    "field": ref("decimal_compare", "amount"),
                    "operator": "eq",
                    "value": 0.1,
                }
            ],
        }
    )
    plan = compile_query(model, query)
    assert any(
        isinstance(value, Decimal) and value == Decimal("0.1")
        for value in plan.params.values()
    )
    assert rows(connection, plan) == [{"matching_count": 1}]


def scoped_money_model(
    model: GovernedModel,
    *,
    left_currency: str,
    right_currency: str,
    operator: str = "divide",
) -> GovernedModel:
    def change(data: dict) -> None:
        for metric, currency in zip(
            data["metrics"][:2], (left_currency, right_currency), strict=True
        ):
            metric["required_filters"].append(
                {
                    "field": ref("orders", "currency_code"),
                    "operator": "eq",
                    "value": currency,
                }
            )
        if right_currency != left_currency:
            # Both operands then have actual data: CNY confirmed=250, USD confirmed=100.
            data["metrics"][1]["required_filters"][0]["value"] = "CONFIRMED"
        data["metrics"].append(
            {
                "metric_id": "scoped_composite",
                "name": "scoped_composite",
                "description": "Currency scope compatibility regression",
                "owner_term_id": "lines",
                "source_ids": ["lines"],
                "kind": "composite",
                "expression": {
                    "kind": "binary",
                    "operator": operator,
                    "left": {"kind": "metric", "metric_id": "confirmed_amount"},
                    "right": {"kind": "metric", "metric_id": "cancelled_amount"},
                },
                "supported_dimensions": ["currency_code"],
                "unit": "number" if operator == "divide" else "currency",
                "currency_field_id": None if operator == "divide" else "currency_code",
            }
        )

    return rebuild(model, change)


def test_same_single_currency_dependencies_can_form_ratio(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    model = scoped_money_model(model, left_currency="CNY", right_currency="CNY")
    query = request(metric_ids=["scoped_composite"], dimensions=[])
    assert rows(connection, compile_query(model, query)) == [
        {"scoped_composite": Decimal("5")}
    ]


def test_different_currency_scopes_remain_separate_when_grouped(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    model = scoped_money_model(model, left_currency="CNY", right_currency="USD")
    result = rows(
        connection, compile_query(model, request(metric_ids=["scoped_composite"]))
    )
    assert {row["currency_code"]: row["scoped_composite"] for row in result} == {
        "CNY": None,
        "USD": None,
    }


def test_explicit_non_null_currency_filter_excludes_unknown_money(
    f0: tuple[psycopg.Connection, GovernedModel],
) -> None:
    connection, model = f0
    connection.execute(
        "ALTER TABLE sales_order ALTER COLUMN currency_code DROP NOT NULL; UPDATE sales_order SET currency_code=NULL WHERE order_id=1002"
    )

    def change(data: dict) -> None:
        for binding in data["sources"][1]["fields"]:
            if binding["field_id"] == "currency_code":
                binding["nullable"] = True

    model = rebuild(model, change)
    query = request(
        filters=[{"field": ref("orders", "currency_code"), "operator": "is_not_null"}]
    )
    assert rows(connection, compile_query(model, query)) == [
        {"currency_code": "CNY", "confirmed_amount": Decimal("250")}
    ]
