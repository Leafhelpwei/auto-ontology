# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in tests against official MySQL 5.7, including actual server cancellation."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import mysql.connector
import pytest

from auto_ontology.connectors.base import StatementTimeout
from auto_ontology.connectors.mysql import MySQLDatabase, _parse_connection_string
from auto_ontology.server.chat.worker import PrewarmedWorker, _terminate_worker


@pytest.fixture
def mysql57() -> Any:
    dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_TEST_DSN")
    admin_dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN")
    if not dsn or not admin_dsn:
        pytest.skip("Requires isolated MySQL 5.7 reader and fixture-admin DSNs")
    admin = mysql.connector.connect(
        **_parse_connection_string(admin_dsn),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=True,
    )
    table = f"connector_probe_{uuid4().hex}"
    cursor = admin.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, "
        "amount DECIMAL(38,18), big BIGINT UNSIGNED, label VARCHAR(100)) ENGINE=InnoDB"
    )
    cursor.executemany(
        f"INSERT INTO {table} VALUES (%s,%s,%s,%s)",
        [
            (
                1,
                Decimal("123456789.000000000000000001"),
                18446744073709551615,
                "中文😀' OR 1=1 --",
            ),
            (2, None, None, None),
        ]
        + [(i, 1, i, "x") for i in range(3, 65)],
    )
    try:
        yield MySQLDatabase(dsn), admin, table
    finally:
        cursor.execute(f"DROP TABLE {table}")
        cursor.close()
        admin.close()


def test_mysql57_snapshot_precision_unicode_and_parameters(mysql57: Any) -> None:
    database, admin, table = mysql57
    with database.read_transaction() as read:
        state = read(
            "SELECT VERSION() AS version, @@time_zone AS zone, "
            "@@div_precision_increment AS digits, @@sql_mode AS mode"
        ).iloc[0]
        assert state["version"].startswith("5.7.")
        assert state["zone"] == "+00:00" and state["digits"] == 30
        assert "ONLY_FULL_GROUP_BY" in state["mode"]
        frame = read(f"SELECT * FROM {table} WHERE id <= 2 ORDER BY id")
        assert frame.iloc[0]["amount"] == Decimal("123456789.000000000000000001")
        assert frame.iloc[0]["big"] == 18446744073709551615
        assert isinstance(frame.iloc[0]["big"], int)
        assert frame.iloc[1]["big"] is None
        assert frame.iloc[0]["label"] == "中文😀' OR 1=1 --"
        bound = read(
            f"SELECT id FROM {table} WHERE label = %(label)s AND amount = %(amount)s",
            {"label": frame.iloc[0]["label"], "amount": frame.iloc[0]["amount"]},
        )
        assert bound["id"].tolist() == [1]
        cursor = admin.cursor()
        cursor.execute(f"UPDATE {table} SET amount=999 WHERE id=1")
        cursor.close()
        assert (
            read(f"SELECT amount FROM {table} WHERE id=1").iloc[0]["amount"]
            == frame.iloc[0]["amount"]
        )
        with pytest.raises(mysql.connector.Error) as rejected:
            read(f"UPDATE {table} SET amount=0 WHERE id=1")
        assert rejected.value.errno in {1142, 1792}
    with database.read_transaction() as read:
        assert read(f"SELECT amount FROM {table} WHERE id=1").iloc[0]["amount"] == 999


def test_mysql57_server_timeout_and_numeric_warning_rejection(mysql57: Any) -> None:
    database, _, table = mysql57
    started = time.monotonic()
    with pytest.raises(StatementTimeout):
        with database.read_transaction(timeout_s=0.15) as read:
            read(
                f"SELECT SUM(a.id+b.id+c.id+d.id) FROM {table} a "
                f"CROSS JOIN {table} b CROSS JOIN {table} c CROSS JOIN {table} d"
            )
    assert time.monotonic() - started < 3
    with pytest.raises(mysql.connector.Error):
        with database.read_transaction() as read:
            read("SELECT CAST('not a number' AS DECIMAL(38,18))")


def test_mysql57_readonly_transaction_rejects_even_privileged_writes(
    mysql57: Any,
) -> None:
    _, _, table = mysql57
    database = MySQLDatabase(os.environ["AUTO_ONTOLOGY_MYSQL57_ADMIN_DSN"])
    with database.read_transaction() as read:
        with pytest.raises(mysql.connector.Error) as rejected:
            read(f"UPDATE {table} SET amount=0 WHERE id=1")
        assert rejected.value.errno == 1792


def test_mysql57_metadata_lock_wait_is_bounded(mysql57: Any) -> None:
    database, admin, table = mysql57
    cursor = admin.cursor()
    cursor.execute(f"LOCK TABLES {table} WRITE")
    started = time.monotonic()
    try:
        with pytest.raises(StatementTimeout):
            with database.read_transaction(timeout_s=0.2) as read:
                read(f"SELECT id FROM {table} LIMIT 0")
        assert time.monotonic() - started < 3
    finally:
        cursor.execute("UNLOCK TABLES")
        cursor.close()


@pytest.mark.parametrize("sql_type", ["DATE", "DATETIME", "TIMESTAMP NULL"])
def test_mysql57_invalid_legacy_dates_fail_instead_of_becoming_null(
    mysql57: Any, sql_type: str
) -> None:
    database, admin, table = mysql57
    cursor = admin.cursor()
    cursor.execute("SET SESSION sql_mode = ''")
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN legacy_date {sql_type}")
    cursor.execute(f"UPDATE {table} SET legacy_date='0000-00-00' WHERE id=1")
    cursor.close()
    with database.read_transaction() as read:
        assert (
            read(f"SELECT legacy_date FROM {table} WHERE id=2").iloc[0]["legacy_date"]
            is None
        )
        with pytest.raises(mysql.connector.DataError, match="Invalid MySQL"):
            read(f"SELECT legacy_date FROM {table} WHERE id=1")


def _cancel_child(dsn: str, table: str, output: Any) -> None:
    signal.signal(signal.SIGTERM, _terminate_worker)
    database = MySQLDatabase(dsn)
    with database.read_transaction(timeout_s=30) as read:
        connection_id = int(read("SELECT CONNECTION_ID() AS id").iloc[0]["id"])
        output.put(connection_id)
        read(
            "SELECT /* mysql57_cancel_probe */ SUM(a.id+b.id+c.id+d.id+e.id) "
            f"FROM {table} a CROSS JOIN {table} b "
            f"CROSS JOIN {table} c CROSS JOIN {table} d CROSS JOIN {table} e"
        )


def test_mysql57_worker_kill_stops_real_server_query_without_super(
    mysql57: Any,
) -> None:
    database, _, table = mysql57
    context = mp.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=_cancel_child,
        args=(os.environ["AUTO_ONTOLOGY_MYSQL57_TEST_DSN"], table, output),
    )
    process.start()
    try:
        connection_id = output.get(timeout=15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = database.execute(
                "SELECT INFO FROM information_schema.PROCESSLIST WHERE ID = %s",
                [connection_id],
            )
            if not frame.empty and "mysql57_cancel_probe" in str(frame.iloc[0]["INFO"]):
                break
            time.sleep(0.03)
        else:
            pytest.fail("Child did not begin the expensive server query")
        # Exercise the production SIGTERM → SIGKILL grace policy on this
        # deterministic worker; no LLM or unrelated service is involved.
        worker = PrewarmedWorker.__new__(PrewarmedWorker)
        worker._proc = process
        started = time.monotonic()
        worker.kill()
        assert process.exitcode == 128 + signal.SIGTERM
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if database.execute(
                "SELECT ID FROM information_schema.PROCESSLIST WHERE ID = %s",
                [connection_id],
            ).empty:
                break
            time.sleep(0.03)
        else:
            pytest.fail("MySQL server query survived worker termination")
        assert time.monotonic() - started < 4  # far below its 30-second cap
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=3)
        output.close()
