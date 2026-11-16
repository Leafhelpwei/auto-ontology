# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explicit, non-downgrading MySQL transport policy shared by every connection."""

from typing import Any, Mapping

TLS_FIELDS = frozenset(
    {
        "ssl_mode",
        "ssl_ca",
        "ssl_cert",
        "ssl_key",
        "ssl_verify_cert",
        "ssl_verify_identity",
    }
)


def mysql_tls_options(values: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(values.get("ssl_mode") or "VERIFY_IDENTITY").upper()
    if mode not in {"DISABLED", "VERIFY_CA", "VERIFY_IDENTITY"}:
        raise ValueError(
            "MySQL ssl_mode must be DISABLED, VERIFY_CA or VERIFY_IDENTITY"
        )
    disabled = mode == "DISABLED"
    result: dict[str, Any] = {
        "ssl_disabled": disabled,
        "ssl_verify_cert": not disabled,
        "ssl_verify_identity": mode == "VERIFY_IDENTITY",
    }
    for field in ("ssl_verify_cert", "ssl_verify_identity"):
        raw = values.get(field)
        if raw is not None and raw != "":
            if isinstance(raw, bool):
                flag = raw
            elif isinstance(raw, str) and raw.lower() in {"true", "false", "1", "0"}:
                flag = raw.lower() in {"true", "1"}
            else:
                raise ValueError("MySQL TLS verification flags must be boolean")
            if flag != result[field]:
                raise ValueError("MySQL TLS verification flags conflict with ssl_mode")
    for field in ("ssl_ca", "ssl_cert", "ssl_key"):
        value = values.get(field)
        if value not in (None, ""):
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("MySQL TLS certificate paths must be strings")
            if disabled:
                raise ValueError("TLS certificate options cannot be used with DISABLED")
            result[field] = value
    if bool(result.get("ssl_cert")) != bool(result.get("ssl_key")):
        raise ValueError("MySQL client certificate and key must be supplied together")
    return result
