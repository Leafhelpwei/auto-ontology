# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only, bounded SQL execution for Chat and model-issued probes.

Trusted ingestion/introspection keeps the connector's general execute() API.
Interactive SQL always passes deterministic checks before touching a driver.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import pandas as pd

from auto_ontology.infra.feature_flags import get_sql_query_timeout_seconds
from auto_ontology.connectors.base import SQLDatabase
from auto_ontology.connectors.query_policy import QueryBudget, validate_read_query

logger = logging.getLogger(__name__)

# Generated SQL is normally short, but a pathological statement shouldn't flood
# the log; keep enough to be recognisable and say how much was cut.
_MAX_LOGGED_SQL_CHARS = 4000


def truncate_sql(sql: str) -> str:
    """Return *sql* capped at a length that is safe to log."""
    if len(sql) <= _MAX_LOGGED_SQL_CHARS:
        return sql
    omitted = len(sql) - _MAX_LOGGED_SQL_CHARS
    return f"{sql[:_MAX_LOGGED_SQL_CHARS]}… [{omitted} more chars]"


def log_chat_sql(connector: Any, sql: str, *, kind: str = "chat SQL") -> None:
    """Record a fingerprint and size, never SQL literals or database identifiers."""
    logger.info(
        "Interactive query kind=%s dialect=%s sha256=%s characters=%d",
        kind,
        getattr(connector, "dialect", "unknown"),
        hashlib.sha256(sql.encode()).hexdigest()[:16],
        len(sql),
    )


def execute_chat_sql(
    connector: Any,
    sql: str,
    *,
    kind: str = "chat SQL",
    timeout_s: float | None = None,
    budget: QueryBudget | None = None,
) -> pd.DataFrame:
    """Validate and execute one interactive query with configured time and result caps."""
    validate_read_query(sql, getattr(connector, "dialect", None))
    log_chat_sql(connector, sql, kind=kind)
    if timeout_s is None:
        timeout_s = get_sql_query_timeout_seconds()
    reader = getattr(connector, "execute_readonly", None)
    if callable(reader):
        return reader(sql, timeout_s=timeout_s, budget=budget or QueryBudget())
    # Third-party duck-typed connectors retain the same deterministic policy and
    # result cap. Production built-ins implement/inherit this method directly.
    return SQLDatabase.execute_readonly(
        connector, sql, timeout_s=timeout_s, budget=budget
    )
