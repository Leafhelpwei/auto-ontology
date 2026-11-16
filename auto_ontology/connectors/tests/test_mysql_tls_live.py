# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Certificate checks against an isolated TLS-enabled MySQL 5.7 fixture."""

import os
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
import mysql.connector
import pytest
from auto_ontology.connectors.mysql import MySQLDatabase


@pytest.fixture
def tls_dsn() -> str:
    dsn = os.environ.get("AUTO_ONTOLOGY_MYSQL57_TEST_DSN")
    if not dsn or not os.environ.get("MYSQL_WRONG_CA"):
        pytest.skip("Requires isolated MySQL 5.7 TLS fixture and a second untrusted CA")
    return dsn


def replace_query(dsn: str, **changes: str) -> str:
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query.update(changes)
    return urlunsplit(parts._replace(query=urlencode(query)))


def test_tls_mysql57_verifies_identity_and_runs_snapshot(tls_dsn: str) -> None:
    database = MySQLDatabase(tls_dsn)
    with database.read_transaction() as read:
        assert str(read("SELECT VERSION() AS version").iloc[0]["version"]).startswith(
            "5.7."
        )
        cipher = read("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        assert cipher.iloc[0]["Value"]


def test_tls_mysql57_rejects_wrong_ca(tls_dsn: str) -> None:
    with pytest.raises(mysql.connector.Error):
        MySQLDatabase(replace_query(tls_dsn, ssl_ca=os.environ["MYSQL_WRONG_CA"]))


def test_tls_mysql57_rejects_wrong_hostname(tls_dsn: str) -> None:
    parts = urlsplit(tls_dsn)
    assert parts.hostname == "127.0.0.1", "Fixture certificate must exclude localhost"
    wrong = urlunsplit(
        parts._replace(netloc=parts.netloc.replace("@127.0.0.1", "@localhost"))
    )
    with pytest.raises(mysql.connector.Error):
        MySQLDatabase(wrong)


def test_tls_mysql57_rejects_untrusted_default_and_plaintext(tls_dsn: str) -> None:
    parts = urlsplit(tls_dsn)
    with pytest.raises(mysql.connector.Error):
        MySQLDatabase(urlunsplit(parts._replace(query="")))
    with pytest.raises(mysql.connector.Error):
        MySQLDatabase(urlunsplit(parts._replace(query="ssl_mode=DISABLED")))
