# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Actual PostgreSQL/MySQL 5.7 read-only, memory, timeout and cleanup checks."""

from __future__ import annotations

import multiprocessing as mp
import os
import resource
import signal
import threading
from pathlib import Path
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import sqlglot

from auto_ontology.connectors.base import StatementTimeout
from auto_ontology.connectors.mysql import MySQLDatabase
from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.connectors.query_policy import (
    QueryBudget,
    ResultLimitExceeded,
    UnsafeQuery,
)


@pytest.fixture(scope="module", params=["postgres", "mysql"])
def source(request: pytest.FixtureRequest) -> Any:
    dialect = request.param
    variable = (
        "AUTO_ONTOLOGY_GOVERNED_TEST_DSN"
        if dialect == "postgres"
        else "AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN"
    )
    dsn = os.environ.get(variable)
    if not dsn:
        pytest.skip(f"Requires isolated {variable}")
    connector = PostgresDatabase(dsn) if dialect == "postgres" else MySQLDatabase(dsn)
    if dialect == "mysql":
        connector._connect_kwargs["autocommit"] = True
    table = "bounded_" + uuid4().hex
    if dialect == "postgres":
        connector.execute(
            f"CREATE TABLE {table} AS SELECT generate_series(1,1000000) AS id"
        )
    else:
        connector.execute(f"CREATE TABLE {table} (id BIGINT PRIMARY KEY) ENGINE=InnoDB")
        digits = "(" + " UNION ALL ".join(f"SELECT {n} AS n" for n in range(10)) + ")"
        connector.execute(
            f"INSERT INTO {table} SELECT a.n+b.n*10+c.n*100+d.n*1000+e.n*10000+f.n*100000+1 FROM {digits} a CROSS JOIN {digits} b CROSS JOIN {digits} c CROSS JOIN {digits} d CROSS JOIN {digits} e CROSS JOIN {digits} f"
        )
    try:
        yield dialect, dsn, connector, table
    finally:
        connector.execute(f"DROP TABLE {table}")
        connector.close()


def test_million_rows_are_bounded_before_dataframe(source: Any) -> None:
    _, _, connector, table = source
    frame = connector.execute_readonly(
        f"SELECT id FROM {table} ORDER BY id", budget=QueryBudget(max_rows=100)
    )
    assert frame["id"].tolist() == list(range(1, 101))
    assert frame.attrs["query_result"]["reason"] == "row_limit"
    small = connector.execute_readonly(f"SELECT id FROM {table} WHERE id=1")
    assert small["id"].tolist() == [1] and not small.attrs["query_result"]["truncated"]
    empty = connector.execute_readonly(f"SELECT id FROM {table} WHERE id<0")
    assert list(empty.columns) == ["id"] and len(empty) == 0


def test_byte_budget_duplicates_null_and_exact_types(source: Any) -> None:
    _, _, connector, table = source
    frame = connector.execute_readonly(
        f"SELECT id, repeat('x',200) AS label FROM {table}",
        budget=QueryBudget(max_bytes=4096, max_row_bytes=4096),
    )
    assert (
        0 < len(frame) < 100 and frame.attrs["query_result"]["reason"] == "byte_limit"
    )
    frame = connector.execute_readonly(
        "SELECT 1 AS id, 2 AS id, NULL AS n, CAST(123456789.000000000000000001 AS DECIMAL(38,18)) AS exact_value"
    )
    assert list(frame.columns) == ["id", "id", "n", "exact_value"]
    assert frame.iloc[0].tolist() == [
        1,
        2,
        None,
        Decimal("123456789.000000000000000001"),
    ]


def test_write_capable_credentials_cannot_mutate_via_explore(source: Any) -> None:
    _, _, connector, table = source
    for sql in (
        f"UPDATE {table} SET id=0",
        f"DELETE FROM {table}",
        f"SELECT * INTO copy FROM {table}",
    ):
        with pytest.raises(UnsafeQuery):
            connector.execute_readonly(sql)
    assert (
        int(connector.execute(f"SELECT count(*) FROM {table}").iloc[0, 0]) == 1_000_000
    )


def test_database_readonly_still_blocks_a_side_effect_function(
    source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialect, _, connector, table = source
    function = "effect_" + uuid4().hex
    if dialect == "postgres":
        connector.execute(
            f"CREATE FUNCTION {function}() RETURNS int LANGUAGE plpgsql AS $$ BEGIN UPDATE {table} SET id=-1 WHERE id=1; RETURN 1; END $$"
        )
        module = "auto_ontology.connectors.postgres.validate_read_query"
    else:
        connector.execute(
            f"CREATE FUNCTION {function}() RETURNS INT DETERMINISTIC BEGIN UPDATE {table} SET id=-1 WHERE id=1; RETURN 1; END"
        )
        module = "auto_ontology.connectors.mysql.validate_read_query"
    try:
        with pytest.raises(UnsafeQuery):
            connector.execute_readonly(f"SELECT {function}() AS effect")
        # Deliberately bypass only the AST to verify the independent database
        # READ ONLY layer against an account with UPDATE permission.
        monkeypatch.setattr(
            module, lambda sql, dialect: sqlglot.parse_one(sql, read=dialect)
        )
        with pytest.raises(Exception) as rejected:
            connector.execute_readonly(f"SELECT {function}() AS effect")
        assert (
            "read-only" in str(rejected.value).lower()
            or getattr(rejected.value, "errno", None) == 1792
        )
        assert (
            int(
                connector.execute(f"SELECT count(*) FROM {table} WHERE id=1").iloc[0, 0]
            )
            == 1
        )
    finally:
        connector.execute(f"DROP FUNCTION {function}")


def test_timeout_releases_transaction_and_subsequent_read_succeeds(source: Any) -> None:
    _, _, connector, table = source
    started = time.monotonic()
    with pytest.raises(StatementTimeout):
        connector.execute_readonly(
            f"SELECT SUM(a.id+b.id) FROM {table} a CROSS JOIN {table} b", timeout_s=0.1
        )
    assert time.monotonic() - started < 4
    assert connector.execute_readonly("SELECT 1 AS ok").iloc[0, 0] == 1


def _measure_reader(dialect: str, dsn: str, sql: str, pipe: Any) -> None:
    connector = PostgresDatabase(dsn) if dialect == "postgres" else MySQLDatabase(dsn)

    def current_rss() -> int:
        return (
            int(Path("/proc/self/statm").read_text().split()[1])
            * os.sysconf("SC_PAGE_SIZE")
            // 1024
        )

    cold_rss = current_rss()
    connector.execute_readonly("SELECT 1 AS warmup")
    before = current_rss()
    peak = [before]
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(0.002):
            peak[0] = max(peak[0], current_rss())

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        frame = connector.execute_readonly(sql)
        result = {"rows": len(frame), "metadata": frame.attrs["query_result"]}
    except ResultLimitExceeded:
        result = {"oversized": True}
    finally:
        connector.close()
    stop.set()
    sampler.join()
    result["rss_growth_kib"] = max(peak[0], current_rss()) - before
    result["first_query_initialization_kib"] = before - cold_rss
    result["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    pipe.send(result)
    pipe.close()


def test_actual_client_peak_rss_is_bounded_for_million_rows_and_huge_cell(
    source: Any,
) -> None:
    dialect, dsn, admin, table = source
    old_packet_size = None
    if dialect == "mysql":
        old_packet_size = int(
            admin.execute("SELECT @@global.max_allowed_packet").iloc[0, 0]
        )
        admin.execute("SET GLOBAL max_allowed_packet=134217728")
    try:
        context = mp.get_context("spawn")
        measurements = []
        for sql in (
            f"SELECT id FROM {table}",
            "SELECT repeat('x',67108864) AS enormous",
        ):
            parent, child = context.Pipe(duplex=False)
            process = context.Process(
                target=_measure_reader, args=(dialect, dsn, sql, child)
            )
            process.start()
            child.close()
            try:
                assert parent.poll(45), "bounded reader did not finish"
                result = parent.recv()
                process.join(5)
                assert process.exitcode == 0
                measurements.append(result)
            finally:
                if process.is_alive():
                    process.kill()
                    process.join()
                parent.close()
        assert measurements[0]["rows"] == 1000
        assert measurements[0]["metadata"]["truncated"]
        assert measurements[1]["oversized"]
        assert all(item["rss_growth_kib"] < 32 * 1024 for item in measurements), (
            measurements
        )
        print(dialect, "bounded client RSS measurements", measurements)
    finally:
        if old_packet_size is not None:
            admin.execute(f"SET GLOBAL max_allowed_packet={old_packet_size}")


def _cancel_reader(dialect: str, dsn: str, table: str, ready: Any) -> None:
    from auto_ontology.server.chat.worker import _terminate_worker

    signal.signal(signal.SIGTERM, _terminate_worker)
    if dialect == "postgres":
        dsn += ("&" if "?" in dsn else "?") + "application_name=" + table
    connector = PostgresDatabase(dsn) if dialect == "postgres" else MySQLDatabase(dsn)
    ready.send(True)
    ready.close()
    try:
        connector.execute_readonly(
            f"SELECT SUM(a.id+b.id) FROM {table} a CROSS JOIN {table} b", timeout_s=30
        )
    finally:
        connector.close()


def test_cancelling_client_closes_the_actual_server_query(source: Any) -> None:
    dialect, dsn, admin, table = source
    if dialect == "postgres":
        active_sql = f"SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND state='active' AND query LIKE '%{table}%'"
    else:
        active_sql = f"SELECT count(*) FROM information_schema.processlist WHERE id<>connection_id() AND command='Query' AND info LIKE '%{table}%'"
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_cancel_reader, args=(dialect, dsn, table, sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(15)
        assert receiver.recv() is True
        receiver.close()
        if dialect == "postgres":
            active_sql = f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{table}' AND state='active'"
        deadline = time.monotonic() + 8
        while int(admin.execute(active_sql).iloc[0, 0]) == 0:
            assert time.monotonic() < deadline and process.is_alive()
            time.sleep(0.025)
        process.terminate()
        process.join(5)
        assert not process.is_alive()
        deadline = time.monotonic() + 3
        while int(admin.execute(active_sql).iloc[0, 0]) != 0:
            assert time.monotonic() < deadline
            time.sleep(0.025)
        assert admin.execute_readonly("SELECT 1 AS ok").iloc[0, 0] == 1
    finally:
        if process.is_alive():
            process.kill()
            process.join()
