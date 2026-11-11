# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Closed, versionable DTOs for the governed PostgreSQL metric query path.

Certification and access checks belong to the server. These immutable value objects
describe a complete model snapshot; no client-supplied SQL is executable input.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FieldRef(Contract):
    source_id: str
    field_id: str


class FieldBinding(Contract):
    field_id: str
    catalog_column_id: str
    column_name: str
    data_type: Literal["number", "string", "boolean", "date", "datetime"]
    nullable: bool = True


# Decimal remains exact for Python callers. JSON callers can send numeric filter
# values as decimal strings; numeric field validation parses/binds them exactly.
Scalar = str | int | float | bool | Decimal | None


class Predicate(Contract):
    field: FieldRef
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "is_not_null"
    ]
    value: Scalar | tuple[Scalar, ...] = None


class JoinKey(Contract):
    source_field_id: str
    target_field_id: str


class Relationship(Contract):
    relationship_id: str
    source_id: str
    target_id: str
    role: str
    keys: tuple[JoinKey, ...]
    cardinality: Literal["many_to_one", "one_to_one", "one_to_many", "many_to_many"]
    join_type: Literal["inner", "left"] = "left"
    predicates: tuple[Predicate, ...] = ()


class DimensionBinding(Contract):
    dimension_id: str
    field: FieldRef
    relationship_path: tuple[str, ...] = ()


class SourceBinding(Contract):
    source_id: str
    datasource_id: str
    catalog_table_id: str
    owner_term_id: str
    schema_name: str
    table_name: str
    grain: tuple[str, ...]
    fields: tuple[FieldBinding, ...]
    dimensions: tuple[DimensionBinding, ...] = ()
    required_filters: tuple[Predicate, ...] = ()
    priority: int = 100
    pre_aggregated: bool = False
    # Optional server-observed schema fingerprint. The service must compare a
    # supplied value with the live source; the pure compiler cannot attest it.
    structure_fingerprint: str = ""


class FieldExpression(Contract):
    kind: Literal["field"] = "field"
    field_id: str


class NumberExpression(Contract):
    kind: Literal["number"] = "number"
    value: Decimal


class MetricExpression(Contract):
    kind: Literal["metric"] = "metric"
    metric_id: str


class BinaryExpression(Contract):
    kind: Literal["binary"] = "binary"
    operator: Literal["add", "subtract", "multiply", "divide"]
    left: Expression
    right: Expression


Expression = Annotated[
    FieldExpression | NumberExpression | MetricExpression | BinaryExpression,
    Field(discriminator="kind"),
]
BinaryExpression.model_rebuild()


class Metric(Contract):
    metric_id: str
    name: str
    description: str
    owner_term_id: str
    kind: Literal["atomic", "composite"] = "atomic"
    source_ids: tuple[str, ...]
    aggregation: Literal["sum", "count", "count_distinct"] | None = None
    expression: Expression | None = None
    required_filters: tuple[Predicate, ...] = ()
    supported_dimensions: tuple[str, ...] = ()
    time_field_id: str | None = None
    time_role: str | None = None
    supported_granularities: tuple[Literal["day", "month", "year"], ...] = ()
    unit: str = "number"
    currency_field_id: str | None = None
    fixed_currency: str | None = None
    empty_value: Literal["null", "zero"] = "null"
    zero_division: Literal["null", "error"] = "null"
    family_id: str | None = None
    default_of_family: bool = False
    evidence: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        return content_fingerprint(self)


class GovernedModel(Contract):
    model_id: str
    name: str
    dialect: Literal["postgres"] = "postgres"
    sources: tuple[SourceBinding, ...]
    relationships: tuple[Relationship, ...] = ()
    metrics: tuple[Metric, ...]

    def fingerprint(self) -> str:
        return content_fingerprint(self)


class TimeRange(Contract):
    start: datetime
    end_exclusive: datetime
    timezone: str
    time_role: str
    granularity: Literal["day", "month", "year"] | None = None


class OrderBy(Contract):
    field_id: str
    direction: Literal["asc", "desc"] = "asc"


class MetricQueryRequest(Contract):
    metric_ids: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    filters: tuple[Predicate, ...] = ()
    time: TimeRange | None = None
    all_time: bool = False
    order_by: tuple[OrderBy, ...] = ()
    limit: int = Field(default=1000, ge=1, le=10000)


QueryRequest = MetricQueryRequest


class ValidationIssue(Contract):
    code: str
    message: str
    path: str = ""


class ValidationReport(Contract):
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


class Plan(Contract):
    sql: str
    params: dict[str, Scalar | Decimal | datetime]
    datasource_id: str
    source_ids: tuple[str, ...]
    metric_fingerprints: dict[str, str]
    model_fingerprint: str
    request_fingerprint: str
    evidence: dict[str, object]


class ContractError(ValueError):
    def __init__(self, code: str, message: str, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


def content_fingerprint(value: BaseModel) -> str:
    canonical = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
