# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single read-only probe boundary with call, row, byte and time budgets."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from auto_ontology.connectors.base import SQLDatabase
from auto_ontology.connectors.query_policy import (
    QueryBudget,
    UnsafeQuery,
    validate_read_query,
    bounded_query,
)

from auto_ontology.retrieval.text_to_sql.db_probe.config import (
    DB_PROBE_MAX_CALLS,
    DB_PROBE_MAX_ROWS,
)

from auto_ontology.retrieval.text_to_sql.chat_sql import execute_chat_sql

logger = logging.getLogger(__name__)


def is_read_only_select(sql: str, dialect: str | None = None) -> bool:
    """Use the same AST and function policy as final query execution."""
    try:
        validate_read_query(sql, dialect)
        return True
    except UnsafeQuery:
        return False


def _ensure_limit(sql: str, max_rows: int, dialect: str | None = None) -> str:
    return bounded_query(validate_read_query(sql, dialect), dialect or "", max_rows)


@dataclass
class ProbeExecutor:
    """Runs read-only probes against a single connector under strict limits.

    Supports the context-manager protocol for symmetry with future
    resource-holding backends; the current inline implementation holds nothing::

        with ProbeExecutor(connector) as ex:
            ex.run("SELECT ...")

    Parameters
    ----------
    connector:
        The resolved :class:`SQLDatabase` to probe (may be ``None`` — every
        ``run`` then fails gracefully).
    """

    connector: Optional[SQLDatabase]
    max_calls: int = DB_PROBE_MAX_CALLS
    max_rows: int = DB_PROBE_MAX_ROWS
    calls: int = field(default=0, init=False)
    log: list[dict[str, Any]] = field(default_factory=list, init=False)

    @property
    def budget_left(self) -> int:
        return max(0, self.max_calls - self.calls)

    def close(self) -> None:
        """No-op — inline execution holds no resources. Present for API symmetry."""

    def __enter__(self) -> "ProbeExecutor":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def run(self, sql: str, purpose: str = "") -> dict[str, Any]:
        """Execute one probe, returning a structured result entry.

        The entry is always appended to ``self.log`` and returned; it never
        raises. Shape: ``{sql, purpose, ok, rows, row_count, truncated, error,
        elapsed_ms}``.
        """
        entry: dict[str, Any] = {
            "sql": sql,
            "purpose": purpose,
            "ok": False,
            "rows": None,
            "row_count": 0,
            "truncated": False,
            "error": None,
            "elapsed_ms": 0,
        }

        if self.connector is None:
            entry["error"] = "no connector available"
            self.log.append(entry)
            return entry

        if self.calls >= self.max_calls:
            entry["error"] = "probe budget exhausted"
            self.log.append(entry)
            return entry

        if not is_read_only_select(sql, getattr(self.connector, "dialect", None)):
            entry["error"] = "rejected: not a single read-only SELECT"
            logger.warning("Probe rejected by read-only policy")
            self.log.append(entry)
            return entry

        bounded_sql = _ensure_limit(
            sql, self.max_rows + 1, getattr(self.connector, "dialect", None)
        )
        entry["sql"] = bounded_sql
        self.calls += 1

        start = time.perf_counter()
        try:
            df = execute_chat_sql(
                self.connector,
                bounded_sql,
                kind="probe SQL",
                budget=QueryBudget(max_rows=self.max_rows, max_bytes=64 * 1024),
            )
        except Exception as exc:  # noqa: BLE001 — probes must never break the pipeline
            entry["error"] = str(exc)
            entry["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
            logger.info("Probe failed error_class=%s", type(exc).__name__)
            self.log.append(entry)
            return entry

        entry["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        try:
            total = len(df)
            records = df.head(self.max_rows).to_dict(orient="records")
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"result parse failed: {exc}"
            self.log.append(entry)
            return entry

        entry["ok"] = True
        entry["rows"] = records
        entry["row_count"] = total
        entry["truncated"] = total > self.max_rows or bool(
            df.attrs.get("query_result", {}).get("truncated")
        )
        self.log.append(entry)
        return entry


__all__ = ["ProbeExecutor", "is_read_only_select"]
