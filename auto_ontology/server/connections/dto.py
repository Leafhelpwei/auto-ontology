# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Closed per-dialect input fields; runtime credentials are never persisted."""

from typing import Any
from urllib.parse import urlsplit
from auto_ontology.catalog.table_filter import FILTER_KEYS
from auto_ontology.connectors.mysql_tls import TLS_FIELDS

COMMON = {"type", "database", "schemas", "schema", *FILTER_KEYS}
NETWORK = {"host", "port", "user", "password"}
SECRET_FIELDS = frozenset(
    {
        "password",
        "private_key",
        "private_key_passphrase",
        "truststore_password",
        "ssl_key",
    }
)
DIALECT_FIELDS = {
    "postgresql": NETWORK,
    "postgres": NETWORK,
    "mysql": NETWORK | TLS_FIELDS,
    "snowflake": {
        "account",
        "warehouse",
        "user",
        "password",
        "private_key",
        "private_key_passphrase",
    },
    "databricks": {"host", "http_path", "password", "sso_federation"},
    "heavydb": NETWORK | {"protocol"},
    "kyuubi": NETWORK
    | {"ssa_url", "truststore", "truststore_file", "truststore_password"},
    "trino": NETWORK | {"http_scheme"},
    "clickhouse": NETWORK | {"http_scheme"},
}
PUBLIC_FIELDS = (
    frozenset().union(COMMON, *DIALECT_FIELDS.values())
    - SECRET_FIELDS
    - {"truststore_file"}
)


def validate_connection_fields(connection: dict[str, Any]) -> dict[str, Any]:
    kind = connection.get("type")
    if not isinstance(kind, str) or kind not in DIALECT_FIELDS:
        raise ValueError("Unsupported connection type")
    allowed = COMMON | DIALECT_FIELDS[kind] | {"clear_secret_fields"}
    if set(connection) - allowed:
        raise ValueError("Connection contains unsupported or internal fields")
    for name, value in connection.items():
        if name in {"schemas", "clear_secret_fields"}:
            if not isinstance(value, list) or not all(
                isinstance(x, str) and x for x in value
            ):
                raise ValueError("Connection list fields must contain nonempty strings")
            if name == "clear_secret_fields" and (
                set(value) - (DIALECT_FIELDS[kind] & SECRET_FIELDS)
            ):
                raise ValueError("Only credential fields can be explicitly cleared")
        elif name in {"sso_federation", "ssl_verify_cert", "ssl_verify_identity"}:
            if not isinstance(value, bool):
                raise ValueError("Connection flag fields must be boolean")
        elif name == "port" and isinstance(value, int) and not isinstance(value, bool):
            if not 1 <= value <= 65535:
                raise ValueError("Port is outside the valid range")
        elif value is not None and not isinstance(value, str):
            raise ValueError("Connection text fields must be strings")
    host = connection.get("host")
    if host:
        parsed = urlsplit(host if "://" in host else "//" + host)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Host must not contain credentials")
    return connection
