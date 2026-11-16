# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Independent order/payment/refund gold results, on PostgreSQL and MySQL 5.7."""

from decimal import Decimal

import pytest
from auto_ontology.semantic.contracts.tests.test_compiler_results import (
    postgres as postgres,
)
from auto_ontology.semantic.contracts.tests.test_mysql_results import (
    mysql_fixture as mysql_fixture,
)

from auto_ontology.semantic.contracts.compiler import compile_query, quote
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
)
from auto_ontology.semantic.contracts.tests.fixtures import field, ref, source
from auto_ontology.semantic.contracts.tests.test_compiler_results import (
    rows as pg_rows,
)  # noqa: F401
from auto_ontology.semantic.contracts.tests.test_mysql_results import (
    rows as mysql_rows,
)  # noqa: F401

FACTS = {
    "orders": [
        (1, 1, "USD", "100", "2026-01-01"),
        (2, 1, "USD", "200", "2026-01-02"),
        (3, 2, "EUR", "50", "2026-01-01"),
        (4, 3, "USD", "0", "2026-01-01"),
    ],
    "payments": [
        (1, 1, "USD", "60", "2026-01-01"),
        (2, 1, "USD", "40", "2026-01-02"),
        (3, 2, "EUR", "50", "2026-01-03"),
        (4, 3, "USD", "10", "2026-01-01"),
        (5, 4, "USD", "20", "2026-01-01"),
    ],
    "refunds": [
        (1, 1, "USD", "10", "2026-01-02"),
        (2, 2, "EUR", "5", "2026-01-04"),
        (3, 4, "USD", "5", "2026-01-01"),
    ],
}
ROLES = {
    "orders_amount": "order_created",
    "payments_amount": "payment_paid",
    "refunds_amount": "refund_processed",
}


def model_for(schema, dialect, names):
    sources = [
        source(
            "customers",
            names["customers"],
            ["customer_id"],
            [field("customer_id")],
            schema,
        ),
        source(
            "currencies",
            names["currencies"],
            ["currency_code"],
            [field("currency_code", "string")],
            schema,
        ),
    ]
    # The shared canonical currency is distinct from each fact's raw FK field.
    sources[1]["fields"][0]["field_id"] = "canonical_currency"
    sources[1]["grain"] = ["canonical_currency"]
    relationships, metrics = [], []
    for fact in FACTS:
        time_field = field("occurred_at", "datetime")
        if dialect == "mysql":
            time_field["datetime_timezone"] = "UTC"
        binding = source(
            fact,
            names[fact],
            ["id"],
            [
                field("id"),
                field("customer_id"),
                field("currency_code", "string"),
                field("amount"),
                time_field,
            ],
            schema,
        )
        binding["dimensions"] = [
            {
                "dimension_id": "customer",
                "field": ref("customers", "customer_id"),
                "relationship_path": [fact + "_customer"],
            },
            {
                "dimension_id": "currency",
                "field": ref("currencies", "canonical_currency"),
                "relationship_path": [fact + "_currency"],
            },
        ]
        for dim, target, key in [
            ("customer", "customers", "customer_id"),
            ("currency", "currencies", "currency_code"),
        ]:
            relationships.append(
                {
                    "relationship_id": fact + "_" + dim,
                    "source_id": fact,
                    "target_id": target,
                    "role": dim,
                    "keys": [
                        {
                            "source_field_id": key,
                            "target_field_id": "canonical_currency"
                            if target == "currencies"
                            else key,
                        }
                    ],
                    "cardinality": "many_to_one",
                    "join_type": "inner",
                }
            )
        sources.append(binding)
        metrics.append(
            {
                "metric_id": fact + "_amount",
                "name": fact,
                "description": "Reviewed fact total",
                "owner_term_id": fact,
                "source_ids": [fact],
                "aggregation": "sum",
                "expression": {"kind": "field", "field_id": "amount"},
                "unit": "currency",
                "currency_field_id": "canonical_currency",
                "supported_dimensions": ["customer", "currency"],
                "time_field_id": "occurred_at",
                "time_role": ROLES[fact + "_amount"],
                "supported_granularities": ["day", "month"],
                "empty_value": "zero",
            }
        )
    for metric_id, left, right, operator, unit in [
        ("net_paid", "payments_amount", "refunds_amount", "subtract", "currency"),
        ("paid_ratio", "payments_amount", "orders_amount", "divide", "number"),
        ("net_ratio", "net_paid", "orders_amount", "divide", "number"),
    ]:
        involved = (
            ["payments", "refunds"]
            if metric_id == "net_paid"
            else ["payments", "orders"]
            if metric_id == "paid_ratio"
            else list(FACTS)
        )
        metrics.append(
            {
                "metric_id": metric_id,
                "name": metric_id,
                "description": "Independent aggregate alignment",
                "owner_term_id": "orders",
                "kind": "composite",
                "aggregation_strategy": "independent_facts",
                "source_ids": involved,
                "expression": {
                    "kind": "binary",
                    "operator": operator,
                    "left": {"kind": "metric", "metric_id": left},
                    "right": {"kind": "metric", "metric_id": right},
                },
                "unit": unit,
                "currency_field_id": "canonical_currency"
                if unit == "currency"
                else None,
                "supported_dimensions": ["customer", "currency"],
                "supported_granularities": ["day", "month"],
            }
        )
    return GovernedModel.model_validate(
        {
            "model_id": "multi-fact",
            "name": "Order / payment / refund",
            "dialect": dialect,
            "sources": sources,
            "relationships": relationships,
            "metrics": metrics,
        }
    )


@pytest.fixture(params=["postgres", "mysql"])
def multi(request):
    dialect = request.param
    if dialect == "postgres":
        connection, schema = request.getfixturevalue("postgres")
        names = {key: "mf_" + key for key in ("customers", "currencies", *FACTS)}
        execute = connection.execute

        def run(plan):
            return pg_rows(connection, plan)

        cursor = None
    else:
        admin, reader, initial = request.getfixturevalue("mysql_fixture")
        schema = initial.sources[0].schema_name
        prefix = initial.sources[0].table_name + "_mf_"
        names = {key: prefix + key for key in ("customers", "currencies", *FACTS)}
        cursor = admin.cursor()
        execute = cursor.execute

        def run(plan):
            return mysql_rows(reader, plan)

    created = []
    try:

        def table(name):
            return quote(schema, dialect) + "." + quote(names[name], dialect)

        execute(f"CREATE TABLE {table('customers')} (customer_id integer PRIMARY KEY)")
        created.append("customers")
        execute(f"INSERT INTO {table('customers')} VALUES (1),(2),(3),(4)")
        execute(
            f"CREATE TABLE {table('currencies')} (currency_code varchar(3) PRIMARY KEY)"
        )
        created.append("currencies")
        execute(f"INSERT INTO {table('currencies')} VALUES ('USD'),('EUR')")
        for fact, values in FACTS.items():
            time_type = "timestamptz" if dialect == "postgres" else "datetime(6)"
            execute(
                f"CREATE TABLE {table(fact)} (id integer PRIMARY KEY, customer_id integer NOT NULL, currency_code varchar(3) NOT NULL, amount decimal(20,6) NOT NULL, occurred_at {time_type} NOT NULL)"
            )
            created.append(fact)
            for row in values:
                execute(
                    f"INSERT INTO {table(fact)} VALUES (%s,%s,%s,%s,%s)",
                    (
                        *row[:-1],
                        row[-1]
                        + ("T00:00:00+00:00" if dialect == "postgres" else " 00:00:00"),
                    ),
                )
        execute(f"ALTER TABLE {table('payments')} ADD COLUMN order_id integer")
        execute(
            f"UPDATE {table('payments')} SET order_id = CASE WHEN id IN (1,2) THEN 1 WHEN id=3 THEN 3 WHEN id=4 THEN 4 ELSE NULL END"
        )
        # A naive detail LEFT JOIN would count the first order twice (400).
        # Independent fact aggregation must retain the true order total (300).
        detail = execute(
            f"SELECT SUM(o.amount) FROM {table('orders')} o LEFT JOIN {table('payments')} p ON p.order_id=o.id WHERE o.customer_id=1"
        )
        assert (detail if dialect == "postgres" else cursor).fetchone()[0] == Decimal(
            400
        )
        yield model_for(schema, dialect, names), run
    finally:
        if dialect == "mysql":
            for name in reversed(created):
                execute(f"DROP TABLE {table(name)}")
            cursor.close()


def query(*, timed=False):
    data = {
        "metric_ids": ["paid_ratio", "net_ratio"],
        "dimensions": ["customer", "currency"],
        "all_time": not timed,
    }
    if timed:
        data["time"] = {
            "start": "2026-01-01T00:00:00Z",
            "end_exclusive": "2026-01-03T00:00:00Z",
            "timezone": "UTC",
            "time_role": "per_metric",
            "metric_roles": ROLES,
            "granularity": None,
        }
    return MetricQueryRequest.model_validate(data)


def gold(timed, daily=False):
    aggregates = {}
    for fact, records in FACTS.items():
        for _, customer, currency, amount, date in records:
            if timed and date >= "2026-01-03":
                continue
            group = (customer, currency, date) if daily else (customer, currency)
            values = aggregates.setdefault(group, {key: Decimal(0) for key in FACTS})
            values[fact] += Decimal(amount)
    return {
        key: {
            "paid_ratio": values["payments"] / values["orders"]
            if values["orders"]
            else None,
            "net_ratio": (values["payments"] - values["refunds"]) / values["orders"]
            if values["orders"]
            else None,
        }
        for key, values in aggregates.items()
    }


@pytest.mark.parametrize("timed", [False, True])
def test_independent_aggregation_matches_gold_with_partial_missing_and_zero_facts(
    multi, timed
):
    model, run = multi
    plan = compile_query(model, query(timed=timed))
    assert plan.evidence["fact_aggregation"] == "independent_before_alignment"
    assert set(plan.evidence["selected_sources"].values()) == set(FACTS)
    actual = {(int(row["customer"]), row["currency"]): row for row in run(plan)}
    expected = gold(timed)
    assert set(actual) == set(expected)
    for key, values in expected.items():
        for metric, value in values.items():
            if value is None:
                assert actual[key][metric] is None
            else:
                assert abs(actual[key][metric] - value) < Decimal(
                    "0.000000000000000001"
                )
    if model.dialect == "mysql":
        assert not plan.sql.startswith("WITH")


def test_multi_fact_rejects_ambiguous_time_and_unproven_dimension_alignment(multi):
    model, run = multi
    timed = query(timed=True)
    with pytest.raises(ContractError, match="time role"):
        compile_query(
            model,
            timed.model_copy(
                update={"time": timed.time.model_copy(update={"metric_roles": {}})}
            ),
        )
    data = model.model_dump(mode="json")
    for source_data in data["sources"]:
        if source_data["source_id"] == "payments":
            source_data["dimensions"][0] = {
                "dimension_id": "customer",
                "field": ref("payments", "customer_id"),
                "relationship_path": [],
            }
    with pytest.raises(ContractError) as error:
        compile_query(GovernedModel.model_validate(data), query())
    assert error.value.code == "UNPROVEN_DIMENSION_ALIGNMENT"
    with pytest.raises(ContractError):
        compile_query(model, query().model_copy(update={"dimensions": ("customer",)}))


def test_each_fact_uses_its_own_business_event_day(multi):
    model, run = multi
    request = query(timed=True)
    request = request.model_copy(
        update={"time": request.time.model_copy(update={"granularity": "day"})}
    )
    actual = {
        (int(row["customer"]), row["currency"], str(row["__time"])[:10]): row
        for row in run(compile_query(model, request))
    }
    expected = gold(True, daily=True)
    assert set(actual) == set(expected)
    for group, values in expected.items():
        for metric, value in values.items():
            if value is None:
                assert actual[group][metric] is None
            else:
                assert abs(actual[group][metric] - value) < Decimal("1e-18")


def test_multi_fact_does_not_silently_relax_existing_composite_approval(multi):
    model, _ = multi
    data = model.model_dump(mode="json")
    for metric in data["metrics"]:
        if metric["metric_id"] == "net_paid":
            metric["aggregation_strategy"] = "single_fact"
    with pytest.raises(ContractError):
        compile_query(GovernedModel.model_validate(data), query())
    partial_roles = query(timed=True)
    with pytest.raises(ContractError, match="exactly"):
        compile_query(
            model,
            partial_roles.model_copy(
                update={
                    "time": partial_roles.time.model_copy(
                        update={
                            "metric_roles": {"orders_amount": ROLES["orders_amount"]}
                        }
                    )
                }
            ),
        )
