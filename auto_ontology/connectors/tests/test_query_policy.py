# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the shared safety boundary with bypasses and real local execution."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from auto_ontology.connectors.query_policy import (
    QueryBudget,
    ResultLimitExceeded,
    UnsafeQuery,
    bounded_query,
    validate_read_query,
)
from auto_ontology.connectors.sqlite import SQLiteDatabase
from auto_ontology.retrieval.text_to_sql.chat_sql import execute_chat_sql
from auto_ontology.retrieval.text_to_sql.agents.sql_parse_validation import (
    SQLValidationAgent,
)
from auto_ontology.retrieval.text_to_sql.db_probe.executor import (
    ProbeExecutor,
    is_read_only_select,
)


BAD_QUERIES = [
    ("postgres", "UPDATE public.audit_fake SET id=2"),
    ("postgres", "DELETE FROM public.audit_fake"),
    ("postgres", "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x"),
    ("postgres", "SELECT * INTO copy FROM t"),
    ("postgres", "SELECT * FROM t FOR UPDATE"),
    ("postgres", "SELECT 1; DELETE FROM t"),
    ("postgres", "SELECT pg_read_file('/etc/passwd')"),
    ("postgres", "SELECT nextval('seq')"),
    ("postgres", "SELECT pg_advisory_lock(1)"),
    ("postgres", "SELECT audit_side_effect(id) FROM t"),
    ("postgres", "SELECT public.sum(id) FROM t"),
    ("mysql", "SELECT 1 INTO OUTFILE '/tmp/file'"),
    ("mysql", "SELECT @x := 1"),
    ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
    ("mysql", "SELECT GET_LOCK('shared',10)"),
    ("mysql", "SELECT SLEEP(10)"),
    ("mysql", "SELECT 1 /*!50000 INTO OUTFILE '/tmp/file' */"),
    ("mysql", "SELECT * FROM t LOCK IN SHARE MODE"),
    ("sqlite", "SELECT load_extension('/tmp/code.so')"),
    ("duckdb", "SELECT * FROM read_csv('/etc/passwd')"),
    ("duckdb", "SELECT * FROM '/etc/passwd'"),
    ("postgres", "COPY t TO PROGRAM 'echo x'"),
]


@pytest.mark.parametrize("dialect,sql", BAD_QUERIES)
def test_all_interactive_entry_points_reject_without_executing(
    dialect: str, sql: str
) -> None:
    connector = SimpleNamespace(
        dialect=dialect, execute=Mock(), execute_readonly=Mock()
    )
    with pytest.raises(UnsafeQuery):
        execute_chat_sql(connector, sql, timeout_s=1)
    connector.execute.assert_not_called()
    connector.execute_readonly.assert_not_called()
    assert not is_read_only_select(sql, dialect)
    assert not ProbeExecutor(connector).run(sql)["ok"]
    parsed = SQLValidationAgent._sql_parse_validation({}, sql, [dialect])
    assert parsed["query_policy_rejected"] and parsed["another_try"] == 0
    # Even a permissive LLM is never consulted for a rejected statement.
    llm = Mock()
    result = SQLValidationAgent().execute(
        {
            "connectors": [connector],
            "llm": llm,
            "path_state": {"sql_generation_result": SimpleNamespace(sql_code=sql)},
        }
    )
    assert result["decision"] == "unconstructable"
    assert result["path_state"]["sql_code"] is None
    llm.assert_not_called()


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("postgres", "SELECT 'DELETE; UPDATE' AS label, SUM(x) FROM t GROUP BY label"),
        (
            "postgres",
            "WITH x AS (SELECT id FROM t) SELECT * FROM x WHERE EXISTS (SELECT 1 FROM u)",
        ),
        (
            "postgres",
            "SELECT SUM(x), CASE WHEN x>0 THEN COUNT(*) ELSE 0 END FROM t GROUP BY x",
        ),
        (
            "postgres",
            "SELECT ROW_NUMBER() OVER (ORDER BY id), DATE_TRUNC('month',created) FROM t",
        ),
        (
            "mysql",
            "SELECT DATE_FORMAT(created,'%Y-%m'), IFNULL(SUM(x),0) FROM t GROUP BY 1",
        ),
        ("sqlite", "SELECT strftime('%Y', created), count(*) FROM t GROUP BY 1"),
    ],
)
def test_normal_analytical_queries_remain_allowed(dialect: str, sql: str) -> None:
    validate_read_query(sql, dialect)


def test_limit_is_on_the_outer_result_not_a_nested_query() -> None:
    sql = "SELECT * FROM t WHERE id IN (SELECT id FROM x LIMIT 1) ORDER BY id LIMIT 999999"
    bounded = bounded_query(validate_read_query(sql, "postgres"), "postgres", 101)
    assert bounded.endswith("LIMIT 101") and "FROM x LIMIT 1" in bounded
    assert bounded_query(
        validate_read_query("SELECT * FROM t LIMIT 3"), "", 101
    ).endswith("LIMIT 3")


def test_sqlite_reads_are_bounded_and_never_change_the_source(tmp_path: Path) -> None:
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as source:
        source.execute("CREATE TABLE data (id INTEGER, label TEXT)")
        source.executemany(
            "INSERT INTO data VALUES (?,?)", [(i, "x" * 200) for i in range(10000)]
        )
        source.execute("INSERT INTO data VALUES (?,?)", (10001, "x" * 2_000_000))
    connector = SQLiteDatabase(str(path))
    try:
        frame = execute_chat_sql(
            connector,
            "SELECT id FROM data ORDER BY id",
            timeout_s=2,
            budget=QueryBudget(max_rows=7),
        )
        assert frame["id"].tolist() == list(range(7))
        assert frame.attrs["query_result"]["reason"] == "row_limit"
        byte_frame = connector.execute_readonly(
            "SELECT * FROM data", budget=QueryBudget(max_bytes=1024, max_row_bytes=1024)
        )
        assert 0 < len(byte_frame) < 7
        assert byte_frame.attrs["query_result"]["reason"] == "byte_limit"
        with pytest.raises(ResultLimitExceeded):
            connector.execute_readonly("SELECT label FROM data WHERE id=10001")
    finally:
        connector.close()
