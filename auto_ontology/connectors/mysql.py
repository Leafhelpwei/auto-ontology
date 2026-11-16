# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MySQL connector implementing Auto Ontology's SQLDatabase ABC."""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import mysql.connector
from mysql.connector.conversion import MySQLConverter
import pandas as pd

from auto_ontology.catalog.constants import TableTypes
from auto_ontology.connectors.base import SQLDatabase, StatementTimeout

from auto_ontology.connectors.mysql_tls import TLS_FIELDS, mysql_tls_options

logger = logging.getLogger(__name__)

# The server stopping a statement at the session cap: MySQL's
# ER_QUERY_TIMEOUT (``max_execution_time``) and MariaDB's ER_STATEMENT_TIMEOUT
# (``max_statement_time``).
_TIMEOUT_ERRNOS = frozenset({3024, 1969})

QueryParameters = list[Any] | dict[str, Any] | None
ScopedReader = Callable[[str, QueryParameters], pd.DataFrame]

# Only live connections owned by this process are eligible for worker cleanup.
# Taking a tuple snapshot is atomic under the GIL; the SIGTERM handler must not
# acquire a lock that the interrupted thread could itself be holding.
_ACTIVE_CONNECTIONS: dict[int, tuple[dict[str, Any], int]] = {}


class _GovernedMySQLConverter(MySQLConverter):
    """Do not silently turn legacy zero/invalid dates into SQL NULL results."""

    invalid_temporal = False

    def _date_to_python(self, value: Any, dsc: Any = None) -> date | None:
        result = MySQLConverter._date_to_python(value, dsc)
        if result is None:
            self.invalid_temporal = True
        return result

    def _datetime_to_python(self, value: Any, dsc: Any = None) -> datetime | None:
        result = MySQLConverter._datetime_to_python(value, dsc)
        if result is None:
            self.invalid_temporal = True
        return result

    _timestamp_to_python = _datetime_to_python


@contextmanager
def _registered_connection(connection: Any, kwargs: dict[str, Any]) -> Iterator[None]:
    connection_id = getattr(connection, "connection_id", None)
    token = id(connection)
    if isinstance(connection_id, int) and not isinstance(connection_id, bool):
        _ACTIVE_CONNECTIONS[token] = (kwargs, connection_id)
    try:
        yield
    finally:
        _ACTIVE_CONNECTIONS.pop(token, None)


def cancel_active_mysql_queries() -> None:
    """Best-effort worker shutdown: kill its own live server connections.

    MySQL 5.7 can keep computing after a client disappears. KILL CONNECTION
    also ends its transaction and releases locks. Killing one's own sessions
    needs no SUPER or PROCESS privilege. The worker's SIGKILL grace period is
    the outer bound if the server or network cannot accept this cleanup.
    """
    for kwargs, connection_id in tuple(_ACTIVE_CONNECTIONS.values()):
        try:
            connection = mysql.connector.connect(
                **kwargs, connection_timeout=1, read_timeout=1, write_timeout=1
            )
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("KILL CONNECTION %s", (connection_id,))
                finally:
                    cursor.close()
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 — termination must continue on failure
            logger.warning("Could not cancel an active MySQL connection")


def _parse_connection_string(connection_string: str) -> dict[str, Any]:
    """Convert a MySQL URI into ``mysql.connector.connect`` keyword arguments."""
    parsed = urlparse(connection_string)
    if parsed.scheme.split("+", 1)[0].lower() != "mysql":
        raise ValueError("Invalid MySQL connection string scheme")
    if not parsed.hostname:
        raise ValueError("MySQL connection string is missing a host")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("MySQL connection string is missing a database")

    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - TLS_FIELDS or any(len(values) != 1 for values in query.values()):
        raise ValueError("Unsupported or duplicated MySQL connection parameter")
    tls = mysql_tls_options({key: value[0] for key, value in query.items()})
    return {
        **tls,
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


# ER_UNKNOWN_SYSTEM_VARIABLE: this server does not have the variable we tried.
_UNKNOWN_VARIABLE_ERRNO = 1193

# How each server spells the cap, as (variable, value for a cap in seconds):
# MySQL 5.7.8+ takes milliseconds, MariaDB 10.1+ takes seconds. Tried in order.
_TIMEOUT_VARIABLES: tuple[tuple[str, Callable[[float], float]], ...] = (
    ("max_execution_time", lambda s: max(1, int(s * 1000))),
    ("max_statement_time", float),
)

# ``_timeout_variable`` before the first capped statement has probed the server.
_NOT_PROBED = "not probed"


class MySQLDatabase(SQLDatabase):
    """Concrete :class:`SQLDatabase` backed by ``mysql-connector-python``."""

    supports_statement_timeout = True

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        self._connect_kwargs = _parse_connection_string(connection_string)
        # New connector releases default to an 8.0-only collation. Both of
        # these settings work on 5.7 and preserve supplementary Unicode.
        # Pure Python socket I/O also lets the worker's SIGTERM handler run
        # while a query is waiting for its first server result.
        self._connect_kwargs.update(
            charset="utf8mb4", collation="utf8mb4_unicode_ci", use_pure=True
        )
        self._database_name = str(self._connect_kwargs["database"])
        # Which of _TIMEOUT_VARIABLES this server accepts, learned on the first
        # capped statement so later ones skip the variants it rejected. None
        # once neither is supported.
        self._timeout_variable: str | None = _NOT_PROBED
        self.ping()

    def _set_statement_timeout(self, cursor: Any, timeout_s: float) -> None:
        """Cap statements on *cursor*'s session at *timeout_s* seconds.

        A server that knows neither variable (MySQL < 5.7.8, MariaDB < 10.1,
        other MySQL-protocol engines) runs the statement uncapped, as it did
        before the cap existed, rather than failing every query.
        """
        for variable, to_value in _TIMEOUT_VARIABLES:
            if self._timeout_variable not in (_NOT_PROBED, variable):
                continue
            try:
                cursor.execute(f"SET SESSION {variable} = %s", (to_value(timeout_s),))
            except mysql.connector.Error as exc:
                if exc.errno != _UNKNOWN_VARIABLE_ERRNO:
                    raise
                continue
            self._timeout_variable = variable
            return
        if self._timeout_variable is not None:
            logger.warning(
                "MySQL server for %s supports no statement timeout variable; "
                "statements on it run uncapped",
                self._database_name,
            )
        self._timeout_variable = None

    @property
    def dialect(self) -> str:
        """Return this engine's sqlglot dialect name.

        Must be a member of ``sqlglot.dialects.DIALECTS`` — callers pass it
        straight to sqlglot without translation. See ``CONNECTOR_REGISTRY``.
        """
        return "mysql"

    @property
    def database_name(self) -> str:
        return self._database_name

    def execute(
        self,
        sql: str,
        parameters: QueryParameters = None,
        *,
        timeout_s: float | None = None,
    ) -> pd.DataFrame:
        """Run *sql*; with *timeout_s*, the server stops it after that long.

        The cap is a session variable, and each call owns its connection, so it
        never outlives the statement. MySQL applies ``max_execution_time`` to
        read-only ``SELECT`` only, which is all the agent issues.

        Raises:
            StatementTimeout: the server stopped the statement at the cap.
        """
        connection = mysql.connector.connect(**self._connect_kwargs)
        try:
            with _registered_connection(connection, self._connect_kwargs):
                cursor = connection.cursor(dictionary=True)
                started = time.monotonic()
                try:
                    if timeout_s is not None:
                        self._set_statement_timeout(cursor, timeout_s)
                    cursor.execute(sql, parameters)
                    if cursor.description is None:
                        return pd.DataFrame()
                    rows = cursor.fetchall()
                    columns = [description[0] for description in cursor.description]
                    return pd.DataFrame(rows, columns=columns)
                except mysql.connector.Error as exc:
                    if timeout_s is not None and exc.errno in _TIMEOUT_ERRNOS:
                        raise StatementTimeout.since(timeout_s, started) from exc
                    raise
                finally:
                    cursor.close()
        finally:
            connection.close()

    @contextmanager
    def read_transaction(self, *, timeout_s: float = 30) -> Iterator[ScopedReader]:
        """Pin governed validation and execution to one capped read snapshot.

        Unlike legacy execute(), this path requires a server-side timeout.
        The service additionally restricts reads to physical InnoDB tables and
        validates generated SQL; READ ONLY alone permits temporary-table writes.
        """
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("A finite positive statement timeout is required")
        connection = mysql.connector.connect(
            **self._connect_kwargs,
            raise_on_warnings=True,
            converter_class=_GovernedMySQLConverter,
            connection_timeout=min(10, math.ceil(timeout_s)),
            read_timeout=math.ceil(timeout_s) + 1,
            write_timeout=math.ceil(timeout_s) + 1,
        )
        try:
            with _registered_connection(connection, self._connect_kwargs):
                cursor = connection.cursor()
                try:
                    self._set_statement_timeout(cursor, timeout_s)
                    if self._timeout_variable is None:
                        raise ValueError(
                            "Governed MySQL reads require statement timeout support"
                        )
                    cursor.execute("SET SESSION time_zone = '+00:00'")
                    cursor.execute(
                        "SET SESSION lock_wait_timeout = %s", (math.ceil(timeout_s),)
                    )
                    cursor.execute("SET SESSION div_precision_increment = 30")
                    try:
                        cursor.execute(
                            "SET SESSION sql_mode = "
                            "'ONLY_FULL_GROUP_BY,STRICT_ALL_TABLES,NO_ZERO_IN_DATE,"
                            "NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"
                        )
                    except mysql.connector.Error as exc:
                        # 5.7 warns when its default NO_AUTO_CREATE_USER is
                        # removed. The assignment succeeded; this deprecation
                        # is unrelated to query data or numeric conversion.
                        if exc.errno != 3090:
                            raise
                finally:
                    cursor.close()
                connection.start_transaction(
                    consistent_snapshot=True,
                    isolation_level="REPEATABLE READ",
                    readonly=True,
                )
                active = True

                def read(sql: str, parameters: QueryParameters = None) -> pd.DataFrame:
                    if not active:
                        raise RuntimeError("MySQL read transaction is closed")
                    started = time.monotonic()
                    cursor = connection.cursor(dictionary=True)
                    try:
                        converter = connection.converter
                        converter.invalid_temporal = False
                        cursor.execute(sql, parameters)
                        description = cursor.description or ()
                        columns = [item[0] for item in description]
                        rows = cursor.fetchall() if description else []
                        # Raise only after the driver consumes the result EOF.
                        # Raising inside row conversion leaves its unread flag
                        # set and can make rollback wait for that EOF again.
                        if converter.invalid_temporal:
                            raise mysql.connector.DataError(
                                "Invalid MySQL date or datetime result", errno=1292
                            )
                        frame = pd.DataFrame(rows, columns=columns, dtype=object)
                        frame.attrs["dialect"] = "mysql"
                        frame.attrs["mysql_types"] = {
                            item[0]: item[1] for item in description
                        }
                        return frame
                    except mysql.connector.Error as exc:
                        if exc.errno in _TIMEOUT_ERRNOS or exc.errno == 1205:
                            raise StatementTimeout.since(timeout_s, started) from exc
                        raise
                    finally:
                        cursor.close()

                try:
                    yield read
                finally:
                    active = False
                    connection.rollback()
        finally:
            connection.close()

    def get_tables(self) -> pd.DataFrame:
        view_type = TableTypes.VIEW
        base_table_type = TableTypes.BASE_TABLE
        return self.execute(
            f"""
            SELECT
                TABLE_SCHEMA AS table_schema,
                TABLE_NAME AS table_name,
                CASE TABLE_TYPE
                    WHEN 'VIEW' THEN '{view_type}'
                    ELSE '{base_table_type}'
                END AS table_type
            FROM information_schema.tables
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
            ORDER BY TABLE_SCHEMA, TABLE_NAME
            """
        )

    def get_columns(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                TABLE_SCHEMA AS table_schema,
                TABLE_NAME AS table_name,
                COLUMN_NAME AS column_name,
                DATA_TYPE AS data_type,
                IS_NULLABLE AS is_nullable,
                ORDINAL_POSITION AS ordinal_position
            FROM information_schema.columns
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
            """
        )

    def get_queries(self, hours: int = 24) -> pd.DataFrame:
        # Query history requires optional Performance Schema consumers and
        # elevated privileges. It is not needed for schema ingestion.
        return pd.DataFrame(columns=["end_time", "query_text"])

    def get_views(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                TABLE_SCHEMA AS table_schema,
                TABLE_NAME AS table_name,
                VIEW_DEFINITION AS view_definition
            FROM information_schema.views
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_SCHEMA, TABLE_NAME
            """
        )

    def get_pks(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                kcu.TABLE_SCHEMA AS table_schema,
                kcu.TABLE_NAME AS table_name,
                kcu.COLUMN_NAME AS column_name,
                kcu.ORDINAL_POSITION AS ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
             AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
             AND tc.TABLE_NAME = kcu.TABLE_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
              AND tc.TABLE_SCHEMA = DATABASE()
            ORDER BY kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.ORDINAL_POSITION
            """
        )

    def get_fks(self) -> pd.DataFrame:
        return self.execute(
            """
            SELECT
                TABLE_SCHEMA AS table_schema,
                TABLE_NAME AS table_name,
                COLUMN_NAME AS column_name,
                REFERENCED_TABLE_SCHEMA AS referenced_schema,
                REFERENCED_TABLE_NAME AS referenced_table,
                REFERENCED_COLUMN_NAME AS referenced_column
            FROM information_schema.key_column_usage
            WHERE TABLE_SCHEMA = DATABASE()
              AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME
            """
        )

    def ping(self) -> None:
        connection = mysql.connector.connect(**self._connect_kwargs)
        try:
            connection.ping(reconnect=False)
        finally:
            connection.close()

    def close(self) -> None:
        """No-op because each operation owns and closes its connection."""
