# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat SQL uses bounded reads and emits no SQL literals in logs."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pandas as pd
import pytest
from pytest import LogCaptureFixture

from auto_ontology.connectors.databricks import AUTH_SSO_FEDERATION, AUTH_STORED_TOKEN
from auto_ontology.infra.feature_flags import DEFAULT_SQL_QUERY_TIMEOUT_SECONDS
from auto_ontology.retrieval.text_to_sql import chat_sql
from auto_ontology.retrieval.text_to_sql.chat_sql import (
    _MAX_LOGGED_SQL_CHARS,
    execute_chat_sql,
    log_chat_sql,
    truncate_sql,
)

_LOGGER = "auto_ontology.retrieval.text_to_sql.chat_sql"


@pytest.fixture
def configured_timeout(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stub the settings read; append to the list to change the stored value."""
    stored = [DEFAULT_SQL_QUERY_TIMEOUT_SECONDS]
    monkeypatch.setattr(chat_sql, "get_sql_query_timeout_seconds", lambda: stored[-1])
    return stored


@pytest.mark.parametrize("auth_mode", [AUTH_SSO_FEDERATION, AUTH_STORED_TOKEN, None])
def test_logs_fingerprint_without_sql_or_source_identifiers(
    caplog: LogCaptureFixture, auth_mode: str | None
) -> None:
    connector = SimpleNamespace(
        database_name="private_customer_db", dialect="postgres", auth_mode=auth_mode
    )
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        log_chat_sql(
            connector,
            "SELECT * FROM customers WHERE email='sensitive@example.invalid'",
            kind="probe SQL",
        )
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "sha256=" in text and "kind=probe SQL" in text and "dialect=postgres" in text
    assert (
        "sensitive" not in text
        and "private_customer_db" not in text
        and "SELECT" not in text
    )


def test_unknown_database_name_does_not_crash(caplog: LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        log_chat_sql(SimpleNamespace(), "SELECT 1")
    assert "dialect=unknown" in caplog.text


def test_long_sql_is_truncated() -> None:
    assert truncate_sql("SELECT 1") == "SELECT 1"

    truncated = truncate_sql("x" * (_MAX_LOGGED_SQL_CHARS + 50))
    assert truncated.startswith("x" * 100)
    assert "[50 more chars]" in truncated


class _Recorder:
    """Connector stub recording how ``execute`` was called."""

    database_name = "kdc_ca1"

    def __init__(self, supports_timeout: bool) -> None:
        self.supports_statement_timeout = supports_timeout
        self.calls: list[tuple[str, dict]] = []

    def execute(self, sql: str, **kwargs: object) -> pd.DataFrame:
        self.calls.append((sql, kwargs))
        return pd.DataFrame({"value": [1]})


def test_chat_sql_is_capped_at_30_seconds(configured_timeout: list[int]) -> None:
    connector = _Recorder(supports_timeout=True)

    assert execute_chat_sql(connector, "SELECT 1")["value"].tolist() == [1]
    assert connector.calls == [
        ("SELECT 1 LIMIT 1001", {"timeout_s": DEFAULT_SQL_QUERY_TIMEOUT_SECONDS})
    ]
    assert DEFAULT_SQL_QUERY_TIMEOUT_SECONDS == 30


def test_chat_sql_uses_the_configured_timeout(configured_timeout: list[int]) -> None:
    """Read per statement, so a change in Agent Settings applies to the next one."""
    connector = _Recorder(supports_timeout=True)

    configured_timeout.append(90)
    execute_chat_sql(connector, "SELECT 1")
    configured_timeout.append(5)
    execute_chat_sql(connector, "SELECT 2")

    assert [kwargs for _, kwargs in connector.calls] == [
        {"timeout_s": 90},
        {"timeout_s": 5},
    ]


def test_without_settings_the_default_is_30_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No frontend means no ``configurations`` table; the read must fall back."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(
        "auto_ontology.infra.feature_flags._read_configuration_row", _boom
    )
    connector = _Recorder(supports_timeout=True)

    execute_chat_sql(connector, "SELECT 1")

    assert connector.calls == [("SELECT 1 LIMIT 1001", {"timeout_s": 30})]


def test_connectors_without_timeout_support_are_called_unchanged() -> None:
    """A connector without a cap must not get an argument it would reject."""
    connector = _Recorder(supports_timeout=False)

    execute_chat_sql(connector, "SELECT 1")

    assert connector.calls == [("SELECT 1 LIMIT 1001", {})]


def test_probe_sql_is_also_capped(configured_timeout: list[int]) -> None:
    connector = _Recorder(supports_timeout=True)

    execute_chat_sql(connector, "SELECT 1", kind="probe SQL")

    assert connector.calls[0][1] == {"timeout_s": DEFAULT_SQL_QUERY_TIMEOUT_SECONDS}


def test_an_explicit_timeout_overrides_the_configured_one(
    configured_timeout: list[int],
) -> None:
    connector = _Recorder(supports_timeout=True)

    execute_chat_sql(connector, "SELECT 1", timeout_s=7)

    assert connector.calls == [("SELECT 1 LIMIT 1001", {"timeout_s": 7})]
