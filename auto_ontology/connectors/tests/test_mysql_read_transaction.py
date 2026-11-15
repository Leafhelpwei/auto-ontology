# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from decimal import Decimal
from typing import Any

import mysql.connector
import pytest

from auto_ontology.connectors.base import StatementTimeout
from auto_ontology.connectors.mysql import (
    MySQLDatabase,
    _ACTIVE_CONNECTIONS,
    _GovernedMySQLConverter,
    cancel_active_mysql_queries,
)


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.description: list[tuple[str, int]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.statements.append((sql, params))
        if sql.startswith("SET SESSION") and any(
            variable in sql for variable in self.connection.reject
        ):
            raise mysql.connector.Error("Unknown variable", errno=1193)
        if sql.startswith("SELECT"):
            if self.connection.query_error:
                raise self.connection.query_error
            self.description = [("amount", 246), ("id", 8)]

    def fetchall(self) -> list[dict[str, Any]]:
        return self.connection.rows

    def close(self) -> None:
        pass


class Connection:
    def __init__(self, kwargs: dict[str, Any], connection_id: int) -> None:
        self.kwargs = kwargs
        self.connection_id = connection_id
        self.converter = _GovernedMySQLConverter()
        self.statements: list[tuple[str, Any]] = []
        self.transaction: dict[str, Any] = {}
        self.rolled_back = False
        self.closed = False
        self.reject: tuple[str, ...] = ()
        self.query_error: mysql.connector.Error | None = None
        self.rows = [
            {"amount": Decimal("1.000000000000000001"), "id": 9223372036854775807},
            {"amount": None, "id": None},
        ]

    def cursor(self, **kwargs: Any) -> Cursor:
        return Cursor(self)

    def ping(self, **kwargs: Any) -> None:
        pass

    def start_transaction(self, **kwargs: Any) -> None:
        self.transaction = kwargs

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        assert id(self) not in _ACTIVE_CONNECTIONS
        self.closed = True


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> tuple[MySQLDatabase, list[Connection]]:
    connections: list[Connection] = []

    def connect(**kwargs: Any) -> Connection:
        connection = Connection(kwargs, len(connections) + 1)
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        "auto_ontology.connectors.mysql.mysql.connector.connect", connect
    )
    return MySQLDatabase("mysql://reader:password@localhost/metrics"), connections


def test_snapshot_parameters_precision_and_cleanup(database: Any) -> None:
    db, connections = database
    params = {"amount": Decimal("1.000000000000000001"), "text": "中文😀' OR 1=1 --"}
    with db.read_transaction(timeout_s=2.5) as read:
        connection = connections[-1]
        assert connection.kwargs["charset"] == "utf8mb4"
        assert connection.kwargs["collation"] == "utf8mb4_unicode_ci"
        assert connection.kwargs["use_pure"] is True
        assert connection.kwargs["raise_on_warnings"] is True
        assert connection.transaction == {
            "consistent_snapshot": True,
            "isolation_level": "REPEATABLE READ",
            "readonly": True,
        }
        frame = read("SELECT %(amount)s, %(text)s", params)
        assert connection.statements[-1] == ("SELECT %(amount)s, %(text)s", params)
        assert ("SET SESSION max_execution_time = %s", (2500,)) in connection.statements
        assert ("SET SESSION time_zone = '+00:00'", None) in connection.statements
        assert (
            "SET SESSION div_precision_increment = 30",
            None,
        ) in connection.statements
        assert frame.iloc[0]["id"] == 9223372036854775807
        assert isinstance(frame.iloc[0]["id"], int)
        assert frame.iloc[0]["amount"] == Decimal("1.000000000000000001")
        assert frame.iloc[1]["id"] is None
        assert frame.attrs == {
            "dialect": "mysql",
            "mysql_types": {"amount": 246, "id": 8},
        }
        connection.rows = []
        assert list(read("SELECT 1").columns) == ["amount", "id"]
    assert connection.rolled_back and connection.closed
    with pytest.raises(RuntimeError, match="closed"):
        read("SELECT 1")


def test_timeout_rolls_back_and_unregisters(database: Any) -> None:
    db, connections = database
    with pytest.raises(StatementTimeout):
        with db.read_transaction(timeout_s=0.5) as read:
            connections[-1].query_error = mysql.connector.Error("cap", errno=3024)
            read("SELECT 1")
    assert connections[-1].closed and connections[-1].rolled_back
    assert not _ACTIVE_CONNECTIONS


def test_governed_reads_fail_closed_without_timeout(database: Any) -> None:
    db, connections = database
    db._timeout_variable = None
    with pytest.raises(ValueError, match="require statement timeout"):
        with db.read_transaction():
            pytest.fail("Must not expose an uncapped governed reader")
    assert connections[-1].closed
    assert not connections[-1].transaction


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_does_not_connect(database: Any, timeout: float) -> None:
    db, connections = database
    with pytest.raises(ValueError, match="finite positive"):
        with db.read_transaction(timeout_s=timeout):
            pass
    assert len(connections) == 1  # constructor ping only


def test_cancellation_kills_only_registered_open_connections(database: Any) -> None:
    db, connections = database
    with db.read_transaction():
        owned = connections[-1]
        cancel_active_mysql_queries()
        control = connections[-1]
        assert control.statements == [("KILL CONNECTION %s", (owned.connection_id,))]
        assert control.kwargs["user"] == owned.kwargs["user"]
        assert control.kwargs["connection_timeout"] == 1
        assert control.closed
    count = len(connections)
    cancel_active_mysql_queries()
    assert len(connections) == count


def test_other_query_errors_propagate(database: Any) -> None:
    db, connections = database
    with pytest.raises(mysql.connector.Error, match="overflow"):
        with db.read_transaction() as read:
            connections[-1].query_error = mysql.connector.Error("overflow", errno=1264)
            read("SELECT 1")
    assert connections[-1].rolled_back
