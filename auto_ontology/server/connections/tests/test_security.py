# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from urllib.parse import unquote, urlsplit
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auto_ontology.server.connections import service
from auto_ontology.server.connections import router as routes
from auto_ontology.server.connections.dto import validate_connection_fields
from auto_ontology.connectors.connection_string_factory import build_connection_string
from auto_ontology.connectors.mysql import _parse_connection_string


def connection(**extra: object) -> dict:
    return {
        "type": "mysql",
        "host": "localhost",
        "database": "test",
        "user": "reader",
        "password": "test",
        **extra,
    }


@pytest.mark.parametrize(
    "field", ["access_token_override", "access_token", "unknown_secret", "password_env"]
)
def test_internal_and_unknown_fields_rejected_by_write_and_test_routes(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(service, "_stored_connection", lambda _: connection())
    with TestClient(app) as client:
        for method, url in [
            ("post", "/connections"),
            ("post", "/connections/test"),
            ("put", "/connections/test"),
        ]:
            response = getattr(client, method)(
                url, json={"connection": connection(**{field: "SECRET_SENTINEL"})}
            )
            assert response.status_code == 422
            assert "SECRET_SENTINEL" not in response.text


def test_read_serialization_uses_public_allowlist_for_legacy_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = connection(
        password="SECRET_PASSWORD",
        private_key="SECRET_KEY",
        truststore_file="SECRET_STORE",
        access_token_override="SECRET_RUNTIME",
        unknown_new_credential="SECRET_FUTURE",
        table_allow_regex="^sales",
    )
    monkeypatch.setattr(routes, "_list_connections", lambda: [source])
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).get("/connections")
    assert response.status_code == 200
    assert "SECRET_" not in response.text
    assert response.json()["data"][0]["connection"]["table_allow_regex"] == "^sales"


@pytest.mark.parametrize(
    "kind",
    [
        "postgresql",
        "mysql",
        "databricks",
        "kyuubi",
        "trino",
        "clickhouse",
        "heavydb",
        "snowflake",
    ],
)
def test_secret_bytes_reach_uri_unchanged(kind: str) -> None:
    secret = " \n密码😀 \t "
    source = {
        **connection(),
        "type": kind,
        "password": secret,
        "http_path": "/sql/x",
        "account": "account",
        "warehouse": "warehouse",
    }
    uri = build_connection_string(source)
    assert unquote(urlsplit(uri).password) == secret
    if kind == "mysql":
        assert _parse_connection_string(uri)["password"] == secret


def test_empty_keep_clear_and_replace_have_distinct_semantics() -> None:
    stored = connection(password=" old secret ")
    assert (
        service._with_stored_secrets(connection(password=""), stored)["password"]
        == " old secret "
    )
    assert (
        service._with_stored_secrets(connection(password="  "), stored)["password"]
        == "  "
    )
    cleared = service._with_stored_secrets(
        connection(password="", clear_secret_fields=["password"]), stored
    )
    assert cleared["password"] == ""
    assert "clear_secret_fields" not in cleared


@pytest.mark.parametrize(
    "values",
    [
        connection(bogus={}),
        connection(host="user:SECRET@host"),
        connection(schemas="public"),
        connection(sso_federation="yes"),
        connection(port=99999),
    ],
)
def test_connection_shape_is_closed(values: dict) -> None:
    with pytest.raises(ValueError):
        validate_connection_fields(values)


def test_edit_does_not_copy_legacy_runtime_tokens_back_into_storage() -> None:
    stored = connection(password="stored", access_token_override="RUNTIME_SENTINEL")
    result = service._with_stored_secrets(connection(password=""), stored)
    assert result["password"] == "stored"
    assert "access_token_override" not in result
