# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Catalog-bound governance and execution for immutable metric model revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
import math

import pandas as pd
from psycopg import sql as pg_sql
from typing import Any

from sqlalchemy import select

from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.connectors.registry import get_connectors
from auto_ontology.dal import governed_models as dal
from auto_ontology.dal import schema as s
from auto_ontology.dal.session import store
from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import (
    ContractError,
    GovernedModel,
    MetricQueryRequest,
    Plan,
    ValidationIssue,
    ValidationReport,
)
from auto_ontology.semantic.contracts.validator import validate_model


@dataclass
class CatalogState:
    fingerprint: str
    database_names: dict[str, str]
    connectors: dict[str, PostgresDatabase]


def enabled() -> bool:
    return os.environ.get(
        "AUTO_ONTOLOGY_GOVERNED_METRICS_ENABLED", "false"
    ).lower() in {"1", "true", "yes"}


def require_enabled() -> None:
    if not enabled():
        raise ContractError("FEATURE_DISABLED", "Governed metric queries are disabled")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractError(code, message)


def _data_type(value: str | None) -> str | None:
    value = (value or "").lower()
    if "[]" in value:
        return None
    if value.startswith(
        (
            "numeric",
            "decimal",
            "smallint",
            "integer",
            "bigint",
            "real",
            "double precision",
            "int2",
            "int4",
            "int8",
            "float",
        )
    ):
        return "number"
    if value.startswith(("character", "varchar", "text", "name", "uuid")):
        return "string"
    if value in {"boolean", "bool"}:
        return "boolean"
    if value == "date":
        return "date"
    if value == "timestamptz" or (
        value.startswith("timestamp") and value.endswith(" with time zone")
    ):
        return "datetime"
    return None


def _unique(keys: list[str], unique_sets: list[list[str]]) -> bool:
    return any(set(key) == set(keys) and len(key) == len(keys) for key in unique_sets)


_COLUMNS_SQL = """
SELECT a.attname AS name, pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""
_UNIQUE_SQL = """
SELECT array_agg(a.attname ORDER BY k.ordinality) AS columns
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE n.nspname = %(schema)s AND c.relname = %(table)s
  AND i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL
  AND k.ordinality <= i.indnkeyatts
GROUP BY i.indexrelid ORDER BY i.indexrelid
"""


def catalog_state(
    model: GovernedModel,
    *,
    live: bool = False,
    readers: dict[str, Callable] | None = None,
) -> CatalogState:
    """Resolve every binding by actual Catalog ID; certification also probes PG.

    A source name is never accepted as authority to choose a database or table.
    The PG structural fingerprint catches out-of-band DDL before the next ingest.
    Current Auto Ontology gives catalog readers global catalog visibility; zones
    are organizational filters, not account grants (see the trusted gateway).
    """
    table_ids = [source.catalog_table_id for source in model.sources]
    table_rows = store().query_read(
        select(
            s.catalog_table.c.id,
            s.catalog_table.c.name,
            s.catalog_table.c.pk,
            s.catalog_schema.c.name.label("schema_name"),
            s.catalog_database.c.id.label("database_id"),
            s.catalog_database.c.name.label("database_name"),
            s.catalog_database.c.dialect,
        )
        .select_from(s.catalog_table.join(s.catalog_schema).join(s.catalog_database))
        .where(s.catalog_table.c.id.in_(table_ids))
    )
    tables = {row["id"]: row for row in table_rows}
    columns = store().query_read(
        select(
            s.catalog_column.c.id,
            s.catalog_column.c.table_id,
            s.catalog_column.c.name,
            s.catalog_column.c.data_type,
            s.catalog_column.c.is_nullable,
            s.catalog_column.c.is_unique,
        ).where(s.catalog_column.c.table_id.in_(table_ids))
    )
    terms = {source.owner_term_id for source in model.sources} | {
        metric.owner_term_id for metric in model.metrics
    }
    existing_terms = {
        row["id"]
        for row in store().query_read(select(s.term.c.id).where(s.term.c.id.in_(terms)))
    }
    _require(
        terms <= existing_terms,
        "UNKNOWN_TERM",
        "All owner_term_id bindings must identify existing glossary terms",
    )
    names: dict[str, str] = {}
    snapshots: list[dict[str, Any]] = []
    unique_by_source: dict[str, list[list[str]]] = {}
    connectors: dict[str, PostgresDatabase] = {}
    available = get_connectors() if live else []
    for source in model.sources:
        table = tables.get(source.catalog_table_id)
        _require(
            table is not None,
            "UNKNOWN_TABLE",
            f"Source {source.source_id}: Catalog table does not exist",
        )
        assert table is not None
        _require(
            table["database_id"] == source.datasource_id
            and table["schema_name"] == source.schema_name
            and table["name"] == source.table_name,
            "BINDING_MISMATCH",
            f"Source {source.source_id}: names/datasource do not match the Catalog IDs",
        )
        _require(
            table["dialect"] in {"postgres", "postgresql"},
            "UNSUPPORTED_DIALECT",
            "Governed queries currently require PostgreSQL",
        )
        names[source.datasource_id] = table["database_name"]
        source_columns = sorted(
            (row for row in columns if row["table_id"] == source.catalog_table_id),
            key=lambda row: row["id"],
        )
        by_id = {row["id"]: row for row in source_columns}
        unique_sets = [table["pk"]] if table["pk"] else []
        unique_sets.extend(
            [[row["name"]] for row in source_columns if row["is_unique"]]
        )
        for field in source.fields:
            column = by_id.get(field.catalog_column_id)
            _require(
                column is not None and column["name"] == field.column_name,
                "BINDING_MISMATCH",
                f"Source {source.source_id}: field {field.field_id} does not match its Catalog column",
            )
            assert column is not None
            _require(
                not (
                    field.data_type == "datetime"
                    and (column["data_type"] or "").lower().startswith("timestamp")
                    and _data_type(column["data_type"]) is None
                ),
                "NAIVE_TIMESTAMP_UNSUPPORTED",
                f"Field {field.field_id}: datetime requires PostgreSQL timestamp with time zone; naive timestamps need explicit source-timezone support",
            )
            _require(
                _data_type(column["data_type"]) == field.data_type,
                "TYPE_MISMATCH",
                f"Source {source.source_id}: field {field.field_id} type differs from Catalog",
            )
            _require(
                field.nullable or column["is_nullable"] is False,
                "NULLABILITY_MISMATCH",
                f"Field {field.field_id} is declared non-null but Catalog does not establish this",
            )
        snapshot: dict[str, Any] = {
            "source_id": source.source_id,
            "table": table,
            "columns": source_columns,
        }
        if live:
            matches = [
                candidate
                for candidate in available
                if candidate.database_name == table["database_name"]
            ]
            _require(
                len(matches) == 1 and isinstance(matches[0], PostgresDatabase),
                "CONNECTION_UNAVAILABLE",
                "A unique configured PostgreSQL connection must match the Catalog database",
            )
            connector = matches[0]
            connectors[source.datasource_id] = connector
            parameters = {"schema": source.schema_name, "table": source.table_name}
            execute_source = (readers or {}).get(source.datasource_id)
            live_frame = (
                execute_source(_COLUMNS_SQL, parameters)
                if execute_source
                else connector.execute(_COLUMNS_SQL, parameters, timeout_s=10)
            )
            live_columns = live_frame.to_dict("records")
            _require(
                bool(live_columns),
                "SOURCE_CHANGED",
                f"Source {source.source_id}: physical table does not exist",
            )
            live_by_name = {row["name"]: row for row in live_columns}
            for field in source.fields:
                physical = live_by_name.get(field.column_name)
                _require(
                    physical is not None
                    and _data_type(physical["data_type"]) == field.data_type,
                    "SOURCE_CHANGED",
                    f"Source {source.source_id}: physical column/type changed",
                )
                assert physical is not None
                _require(
                    field.nullable or not physical["nullable"],
                    "SOURCE_CHANGED",
                    f"Source {source.source_id}: non-null guarantee changed",
                )
            unique_frame = (
                execute_source(_UNIQUE_SQL, parameters)
                if execute_source
                else connector.execute(_UNIQUE_SQL, parameters, timeout_s=10)
            )
            unique_sets = [
                list(row["columns"]) for row in unique_frame.to_dict("records")
            ]
            snapshot["physical_columns"] = live_columns
            snapshot["physical_unique_keys"] = sorted(unique_sets)
        by_field = {field.field_id: field.column_name for field in source.fields}
        grain = [by_field.get(field_id, "") for field_id in source.grain]
        _require(
            bool(grain) and _unique(grain, unique_sets),
            "UNPROVEN_GRAIN",
            f"Source {source.source_id}: grain must match a complete physical unique key",
        )
        nullable_by_name = {
            column["name"]: column["is_nullable"] is not False
            for column in source_columns
        }
        if live:
            nullable_by_name = {
                column["name"]: column["nullable"] for column in live_columns
            }
            _require(
                not source.structure_fingerprint
                or source.structure_fingerprint == _hash(snapshot),
                "SOURCE_CHANGED",
                f"Source {source.source_id}: supplied structure fingerprint does not match PostgreSQL",
            )
        _require(
            all(not nullable_by_name.get(name, True) for name in grain),
            "UNPROVEN_GRAIN",
            f"Source {source.source_id}: grain columns must be non-null",
        )
        unique_by_source[source.source_id] = unique_sets
        snapshots.append(snapshot)
    sources = {source.source_id: source for source in model.sources}
    for relationship in model.relationships:
        target = sources.get(relationship.target_id)
        _require(
            target is not None, "UNKNOWN_SOURCE", "Relationship target does not exist"
        )
        assert target is not None
        fields = {field.field_id: field.column_name for field in target.fields}
        keys = [fields.get(key.target_field_id, "") for key in relationship.keys]
        _require(
            _unique(keys, unique_by_source[target.source_id]),
            "FANOUT_RISK",
            f"Relationship {relationship.relationship_id}: target keys are not physically unique",
        )
        if relationship.cardinality == "one_to_one":
            origin = sources.get(relationship.source_id)
            _require(
                origin is not None,
                "UNKNOWN_SOURCE",
                "Relationship source does not exist",
            )
            assert origin is not None
            origin_fields = {
                field.field_id: field.column_name for field in origin.fields
            }
            origin_keys = [
                origin_fields.get(key.source_field_id, "") for key in relationship.keys
            ]
            _require(
                _unique(origin_keys, unique_by_source[origin.source_id]),
                "FANOUT_RISK",
                f"Relationship {relationship.relationship_id}: one-to-one source keys are not unique",
            )
    return CatalogState(
        _hash(sorted(snapshots, key=lambda item: item["source_id"])), names, connectors
    )


def validation_report(model: GovernedModel) -> ValidationReport:
    report = validate_model(model)
    issues = list(report.issues)
    if report.valid:
        try:
            catalog_state(model)
        except ContractError as exc:
            issues.append(
                ValidationIssue(code=exc.code, message=exc.message, path=exc.path)
            )
    return ValidationReport(valid=not issues, issues=tuple(issues))


def _validate_identity(model: GovernedModel) -> None:
    _require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", model.model_id) is not None,
        "INVALID_MODEL_ID",
        "Model ID must be 1-128 URL-safe characters: start with a letter or digit, then use letters, digits, underscore, dot or hyphen",
    )
    _require(bool(model.name.strip()), "INVALID_MODEL", "Model name cannot be blank")


def create(
    model: GovernedModel, *, user_id: str, owner_id: str | None
) -> dict[str, Any]:
    _validate_identity(model)
    # Drafts may be incomplete, but may not bind invented Catalog IDs or types.
    catalog_state(model)
    return dal.create_model(
        payload=model.model_dump(mode="json"),
        fingerprint=model.fingerprint(),
        user_id=user_id,
        owner_id=owner_id,
    )


def draft(
    model_id: str, model: GovernedModel, *, expected_version: int, user_id: str
) -> dict[str, Any]:
    _validate_identity(model)
    _require(
        model.model_id == model_id,
        "MODEL_ID_MISMATCH",
        "A revision cannot change its model ID",
    )
    catalog_state(model)
    return dal.create_draft(
        model_id=model_id,
        expected_version=expected_version,
        payload=model.model_dump(mode="json"),
        fingerprint=model.fingerprint(),
        user_id=user_id,
    )


def _revision(model_id: str, revision: int) -> dict[str, Any]:
    row = dal.get_revision(model_id, revision)
    if row is None:
        raise dal.ModelNotFound(f"{model_id} revision {revision}")
    return row


def transition(
    model_id: str,
    *,
    revision: int,
    expected_version: int,
    action: str,
    user_id: str,
    evidence: str | None = None,
) -> dict[str, Any]:
    with dal.locked_model(model_id, expected_version) as parent:
        row = _revision(model_id, revision)
        model = GovernedModel.model_validate(row["payload"])
        _require(
            row["status"] != "deprecated",
            "REVISION_DEPRECATED",
            "Deprecated revisions cannot be modified or reactivated",
        )
        if action == "validate":
            _require(
                row["status"] in {"draft", "validated"},
                "INVALID_TRANSITION",
                "Certified revisions are immutable; create a new draft",
            )
            report = validation_report(model)
            dal.set_revision_status(
                row["id"],
                "validated" if report.valid else "draft",
                report.model_dump(mode="json"),
            )
        elif action == "certify":
            _require(
                row["status"] == "validated",
                "INVALID_TRANSITION",
                "Validate this revision before certification",
            )
            _require(
                bool(evidence and evidence.strip()),
                "REVIEW_EVIDENCE_REQUIRED",
                "Provide the business review and result verification evidence",
            )
            report = validation_report(model)
            _require(
                report.valid,
                "MODEL_INVALID",
                "Resolve validation issues before certification",
            )
            state = catalog_state(model, live=True)
            dal.certify(
                row["id"],
                reviewer_id=user_id,
                evidence=evidence.strip(),
                model_fingerprint=row["fingerprint"],
                catalog_fingerprint=state.fingerprint,
            )
        elif action == "activate":
            _check_certificate(model, row)
            dal.set_active(model_id, revision)
        elif action == "deprecate":
            dal.set_revision_status(row["id"], "deprecated")
            if parent["active_revision"] == revision:
                dal.set_active(model_id, None)
        else:
            raise ContractError("INVALID_ACTION", "Unknown lifecycle transition")
    return dal.detail(model_id)


def _check_certificate(
    model: GovernedModel,
    row: dict[str, Any],
    *,
    readers: dict[str, Callable] | None = None,
) -> CatalogState:
    certificate = row.get("certification")
    _require(
        row["status"] == "certified" and certificate is not None,
        "NOT_CERTIFIED",
        "Only a certified model revision can execute",
    )
    assert certificate is not None
    _require(
        model.fingerprint() == row["fingerprint"] == certificate["model_fingerprint"],
        "REVISION_CHANGED",
        "The certified model fingerprint no longer matches",
    )
    state = catalog_state(model, live=True, readers=readers)
    _require(
        state.fingerprint == certificate["catalog_fingerprint"],
        "SOURCE_CHANGED",
        "Source structure changed after review; create and certify a new revision",
    )
    return state


def executable_model(
    model_id: str, revision: int | None = None
) -> tuple[GovernedModel, dict[str, Any], dict[str, Any], CatalogState]:
    require_enabled()
    parent = dal.get_model(model_id)
    if parent is None or parent["archived"]:
        raise dal.ModelNotFound(model_id)
    active = parent["active_revision"]
    _require(
        active is not None and (revision is None or revision == active),
        "NOT_ACTIVE",
        "Standard queries must use the active model revision",
    )
    row = _revision(model_id, active)
    model = GovernedModel.model_validate(row["payload"])
    state = _check_certificate(model, row)
    return model, parent, row, state


def compile_model(
    model_id: str,
    query: MetricQueryRequest,
    *,
    revision: int | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    require_enabled()
    if preview:
        parent = dal.get_model(model_id)
        if parent is None or parent["archived"]:
            raise dal.ModelNotFound(model_id)
        row = _revision(model_id, revision or parent["latest_revision"])
        _require(
            row["status"] != "deprecated",
            "REVISION_DEPRECATED",
            "Cannot preview a deprecated revision",
        )
        model = GovernedModel.model_validate(row["payload"])
        catalog_state(model)
    else:
        model, _, row, _ = executable_model(model_id, revision)
    plan = compile_query(model, query)
    return {
        "revision": row["revision"],
        "trusted": not preview,
        "plan": plan.model_dump(mode="json"),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > 2**53 - 1
    ):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def serialize_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """Preserve exact decimal/integer values instead of rounding through JSON floats."""
    records = frame.to_dict("records")
    metadata = []
    for column in frame.columns:
        values = [record[column] for record in records if record[column] is not None]
        encoding = (
            "decimal-string"
            if any(isinstance(value, Decimal) for value in values)
            else "integer-string"
            if any(
                isinstance(value, int)
                and not isinstance(value, bool)
                and abs(value) > 2**53 - 1
                for value in values
            )
            else "native"
        )
        metadata.append(
            {
                "name": column,
                "postgres_type_oid": frame.attrs.get("postgres_types", {}).get(column),
                "json_encoding": encoding,
            }
        )
    return {
        "columns": list(frame.columns),
        "column_metadata": metadata,
        "rows": [
            {key: _json_value(value) for key, value in row.items()} for row in records
        ],
        "row_count": len(frame),
    }


def execute(
    model_id: str, query: MetricQueryRequest, *, revision: int | None = None
) -> dict[str, Any]:
    require_enabled()
    # Lifecycle writers cannot revoke the selected revision midway through a query.
    with dal.read_snapshot(model_id):
        model, _, row, state = executable_model(model_id, revision)
        plan: Plan = compile_query(model, query)
        with ExitStack() as stack:
            readers = {
                datasource_id: stack.enter_context(
                    connector.read_transaction(timeout_s=30)
                )
                for datasource_id, connector in sorted(state.connectors.items())
            }
            # Lock physical relations before inspecting them, then recheck the
            # certified fingerprint and execute under the same repeatable-read
            # snapshot. DDL and concurrent duplicate rows cannot slip between
            # validation and execution through separate pool connections.
            for source in sorted(
                model.sources,
                key=lambda source: (
                    source.datasource_id,
                    source.schema_name,
                    source.table_name,
                ),
            ):
                lock = (
                    pg_sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE")
                    .format(pg_sql.Identifier(source.schema_name, source.table_name))
                    .as_string()
                )
                readers[source.datasource_id](lock, None)
            _check_certificate(model, row, readers=readers)
            frame = readers[plan.datasource_id](plan.sql, plan.params)
        return {
            "revision": row["revision"],
            "trusted": True,
            "plan": plan.model_dump(mode="json"),
            **serialize_frame(frame),
        }
