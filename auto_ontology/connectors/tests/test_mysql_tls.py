# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest
from auto_ontology.connectors.mysql import (
    _parse_connection_string,
    MySQLDatabase,
    cancel_active_mysql_queries,
)
from auto_ontology.connectors.connection_string_factory import build_connection_string
from auto_ontology.connectors.tests.test_mysql_read_transaction import Connection


def test_default_and_explicit_tls_are_strict() -> None:
    default = _parse_connection_string("mysql://u:p@localhost/d")
    assert default["ssl_verify_identity"] and default["ssl_verify_cert"]
    source = dict(
        type="mysql",
        host="mysql.example",
        user="reader",
        password="secret",
        database="d",
        ssl_mode="VERIFY_IDENTITY",
        ssl_ca="/tmp/ca.pem",
        ssl_cert="/tmp/client.pem",
        ssl_key="/tmp/client-key.pem",
        ssl_verify_cert=True,
        ssl_verify_identity=True,
    )
    options = _parse_connection_string(build_connection_string(source))
    assert {
        key: options[key]
        for key in (
            "ssl_ca",
            "ssl_cert",
            "ssl_key",
            "ssl_verify_cert",
            "ssl_verify_identity",
        )
    } == {
        key: source[key]
        for key in (
            "ssl_ca",
            "ssl_cert",
            "ssl_key",
            "ssl_verify_cert",
            "ssl_verify_identity",
        )
    }


@pytest.mark.parametrize(
    "query",
    [
        "ssl_mode=PREFERRED",
        "ssl_mode=REQUIRED",
        "ssl_verify_cert=false",
        "ssl_verify_identity=0",
        "ssl_mode=DISABLED&ssl_ca=ca",
        "ssl_cert=client",
        "ssl_ca=x&ssl_ca=y",
        "ssl_ca_typo=x",
        "ssl_verify_identity=maybe",
        "ssl_cipher=x",
    ],
)
def test_inconsistent_and_unknown_security_options_fail_closed(query: str) -> None:
    with pytest.raises(ValueError):
        _parse_connection_string("mysql://u:p@localhost/d?" + query)


def test_plaintext_requires_explicit_disabled_mode() -> None:
    options = _parse_connection_string("mysql://u:p@localhost/d?ssl_mode=DISABLED")
    assert options["ssl_disabled"] is True
    assert options["ssl_verify_cert"] is False
    assert options["ssl_verify_identity"] is False


def test_query_ping_and_cancellation_share_the_transport_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = []

    def connect(**kwargs):
        item = Connection(kwargs, len(connections) + 1)
        connections.append(item)
        return item

    monkeypatch.setattr(
        "auto_ontology.connectors.mysql.mysql.connector.connect", connect
    )
    db = MySQLDatabase(
        "mysql://u:p@mysql.example/d?ssl_mode=VERIFY_IDENTITY&ssl_ca=/tmp/ca.pem"
    )
    with db.read_transaction() as read:
        read("SELECT 1")
        cancel_active_mysql_queries()
    # execute uses a dictionary cursor, supported by the same stub.
    db.execute("SELECT 1")
    assert len(connections) == 4
    for item in connections:
        assert item.kwargs["ssl_ca"] == "/tmp/ca.pem"
        assert item.kwargs["ssl_verify_cert"] is True
        assert item.kwargs["ssl_verify_identity"] is True
