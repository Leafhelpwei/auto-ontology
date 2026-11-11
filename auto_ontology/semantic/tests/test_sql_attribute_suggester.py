# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Historical SQL must retain its population, grain and source dependencies."""

from __future__ import annotations

import sqlite3
import os
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from auto_ontology.semantic import sql_attribute_suggester as suggester


@pytest.fixture
def orders() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE orders (id INTEGER, city TEXT, status TEXT, amount NUMERIC);
        INSERT INTO orders VALUES
            (1, 'A', 'paid', 100), (2, 'A', 'paid', 100),
            (3, 'A', 'cancelled', 900), (4, 'B', 'paid', 25),
            (5, 'B', 'paid', NULL);
        """
    )
    yield connection
    connection.close()


@pytest.mark.parametrize(
    ("expression", "source", "expected"),
    [
        (
            "SUM(o.amount)",
            "SELECT o.city, SUM(o.amount) AS total FROM orders o "
            "WHERE o.status = 'paid' GROUP BY o.city HAVING SUM(o.amount) > 50 "
            "ORDER BY o.city",
            [("A", 200)],
        ),
        (
            "SUM(DISTINCT o.amount)",
            "SELECT SUM(DISTINCT o.amount) FROM orders o WHERE o.status = 'paid'",
            [(125,)],
        ),
        (
            "SUM(amount)",
            "WITH paid AS (SELECT * FROM orders WHERE status = 'paid') "
            "SELECT city, SUM(amount) FROM paid GROUP BY city ORDER BY city",
            [("A", 200), ("B", 25)],
        ),
        (
            "status = 'paid'",
            "SELECT city FROM orders WHERE status = 'paid' OR amount = 900 ORDER BY id",
            [("A",), ("A",), ("A",), ("B",), ("B",)],
        ),
    ],
)
def test_preserved_query_has_independent_expected_results(
    orders: sqlite3.Connection,
    expression: str,
    source: str,
    expected: list[tuple],
) -> None:
    candidate = suggester._build_valid_sql(expression, [source])
    assert orders.execute(candidate).fetchall() == expected


def test_join_dependency_closure_is_not_pruned() -> None:
    source = (
        "SELECT SUM(c.amount) FROM accounts a "
        "JOIN orders b ON b.account_id = a.id "
        "JOIN lines c ON c.order_id = b.id "
        "WHERE a.tenant_id = 7 AND b.status = 'paid'"
    )
    assert suggester._build_valid_sql("SUM(c.amount)", [source]) == source


def test_ambiguous_populations_are_not_arbitrarily_selected() -> None:
    with pytest.raises(ValueError, match="context"):
        suggester._build_valid_sql(
            "SUM(amount)",
            [
                "SELECT SUM(amount) FROM orders WHERE status = 'paid'",
                "SELECT SUM(amount) FROM orders WHERE status = 'cancelled'",
            ],
        )


@pytest.mark.parametrize(
    "source",
    [
        "DELETE FROM orders WHERE amount > 0",
        "SELECT * FROM orders; DELETE FROM orders",
        "SELECT * INTO copied_orders FROM orders",
        "WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed",
        "SELECT * FROM orders FOR UPDATE",
        "SELECT SUM(amount) FROM orders /*! WHERE status = 'paid' */",
    ],
)
def test_non_read_only_or_multiple_statements_cannot_become_candidates(
    source: str,
) -> None:
    with pytest.raises(ValueError):
        suggester._build_valid_sql(source, [source])


def test_model_expression_must_exist_in_the_source_ast() -> None:
    with pytest.raises(ValueError, match="source"):
        suggester._build_valid_sql(
            "SUM(amount * 10)", ["SELECT SUM(amount) FROM orders"]
        )


def test_model_fragment_cannot_append_another_statement() -> None:
    with pytest.raises(ValueError):
        suggester._build_valid_sql(
            "SUM(amount); DELETE FROM orders", ["SELECT SUM(amount) FROM orders"]
        )


@pytest.mark.parametrize(
    "source",
    [
        "SELECT id, SUM(amount) OVER (PARTITION BY city ORDER BY id) FROM orders",
        "SELECT city FROM orders o WHERE amount > "
        "(SELECT AVG(amount) FROM orders q WHERE q.city = o.city)",
        "SELECT city FROM orders WHERE status = 'paid' UNION "
        "SELECT city FROM orders WHERE status = 'cancelled'",
    ],
)
def test_complex_sources_are_preserved_as_complete_candidates(source: str) -> None:
    candidate = suggester._rank_query_candidates(
        [{"sql_id": "source", "sql_text": source}]
    )
    assert len(candidate) == 1
    assert suggester._build_valid_sql(candidate[0].sql, [source]) == source


@pytest.mark.skipif(
    not os.environ.get("AUTO_ONTOLOGY_GOVERNED_TEST_DSN"),
    reason="real PostgreSQL fixture DSN not set",
)
def test_real_postgres_preserves_filtered_grouped_amounts() -> None:
    import psycopg
    from decimal import Decimal

    with psycopg.connect(os.environ["AUTO_ONTOLOGY_GOVERNED_TEST_DSN"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE history_orders "
                "(id int, currency text, status text, amount numeric) ON COMMIT DROP"
            )
            cursor.executemany(
                "INSERT INTO history_orders VALUES (%s, %s, %s, %s)",
                [
                    (1, "CNY", "paid", 250),
                    (2, "USD", "paid", 100),
                    (3, "CNY", "cancelled", 50),
                ],
            )
            source = (
                "WITH paid AS (SELECT * FROM history_orders WHERE status = 'paid') "
                "SELECT currency, SUM(amount) AS total FROM paid "
                "GROUP BY currency HAVING SUM(amount) > 0 ORDER BY currency"
            )
            candidate = suggester._build_valid_sql(
                "SUM(amount)", [source], dialect="postgres"
            )
            cursor.execute(candidate)
            assert cursor.fetchall() == [
                ("CNY", Decimal("250")),
                ("USD", Decimal("100")),
            ]


def test_case_sensitive_literals_produce_different_contexts() -> None:
    candidates = suggester._rank_query_candidates(
        [
            {
                "sql_id": "upper",
                "sql_text": "SELECT COUNT(*) FROM orders WHERE city = 'A'",
            },
            {
                "sql_id": "lower",
                "sql_text": "SELECT COUNT(*) FROM orders WHERE city = 'a'",
            },
        ]
    )
    assert len(candidates) == 2
    assert {tuple(c.sql_ids) for c in candidates} == {("upper",), ("lower",)}


def test_cte_name_is_not_schema_qualified_as_a_physical_table() -> None:
    source = (
        "WITH paid AS (SELECT * FROM orders WHERE status = 'paid') "
        "SELECT SUM(amount) FROM paid"
    )
    qualified = suggester._qualify_table_names(
        source, {"orders": "sales", "paid": "unrelated"}
    )
    assert "sales.orders" in qualified
    assert "unrelated.paid" not in qualified


def test_suggestion_pass_does_not_merge_equal_expressions_across_populations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [
        "SELECT SUM(amount) FROM orders WHERE status = 'paid'",
        "SELECT SUM(amount) FROM orders WHERE status = 'cancelled'",
    ]
    monkeypatch.setattr(
        suggester,
        "fetch_terms_with_sqls",
        lambda *args, **kwargs: [
            {
                "term_id": "orders",
                "term_name": "Orders",
                "sqls": [
                    {"sql_id": str(i), "sql_text": sql} for i, sql in enumerate(sources)
                ],
            }
        ],
    )
    monkeypatch.setattr(suggester, "get_dialects", lambda _: ["postgres"])
    monkeypatch.setattr(
        suggester,
        "_judge_with_llm",
        lambda _name, _description, queries: [
            suggester._SuggestedAttr(
                name="Order Amount", description="History candidate", expression=sql
            )
            for sql, _score in queries
        ],
    )
    create = Mock(side_effect=lambda **kw: {"id": kw["name"]})
    monkeypatch.setattr(suggester, "create_sql_attribute", create)

    assert suggester.suggest_sql_attributes("test") == 2
    calls = [call.kwargs for call in create.call_args_list]
    assert {call["expression"] for call in calls} == set(sources)
    assert len({call["name"] for call in calls}) == 2
    assert all(call["source"] == "sql" for call in calls)


def test_ungrounded_model_output_is_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "SELECT SUM(amount) FROM orders WHERE status = 'paid'"
    monkeypatch.setattr(
        suggester,
        "fetch_terms_with_sqls",
        lambda *args, **kwargs: [
            {
                "term_id": "orders",
                "term_name": "Orders",
                "sqls": [{"sql_id": "source", "sql_text": source}],
            }
        ],
    )
    monkeypatch.setattr(suggester, "get_dialects", lambda _: ["postgres"])
    monkeypatch.setattr(
        suggester,
        "_judge_with_llm",
        lambda *args: [
            suggester._SuggestedAttr(
                name="All Revenue",
                description="Unscoped",
                expression="SELECT SUM(amount) FROM orders",
            )
        ],
    )
    create = Mock()
    monkeypatch.setattr(suggester, "create_sql_attribute", create)
    assert suggester.suggest_sql_attributes("test") == 0
    create.assert_not_called()
