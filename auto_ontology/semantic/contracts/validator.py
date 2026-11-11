# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure structural and query validation; external authorization stays server-side."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    BinaryExpression,
    ContractError,
    Expression,
    FieldBinding,
    FieldExpression,
    FieldRef,
    GovernedModel,
    Metric,
    MetricExpression,
    MetricQueryRequest,
    NumberExpression,
    Predicate,
    Relationship,
    SourceBinding,
    ValidationIssue,
    ValidationReport,
)


def fail(code: str, message: str, path: str = "") -> None:
    raise ContractError(code, message, path)


def fields(source: SourceBinding) -> dict[str, FieldBinding]:
    return {field.field_id: field for field in source.fields}


def expression_metrics(expression: Expression | None) -> set[str]:
    if isinstance(expression, MetricExpression):
        return {expression.metric_id}
    if isinstance(expression, BinaryExpression):
        return expression_metrics(expression.left) | expression_metrics(
            expression.right
        )
    return set()


def expression_fields(expression: Expression | None) -> set[str]:
    if isinstance(expression, FieldExpression):
        return {expression.field_id}
    if isinstance(expression, BinaryExpression):
        return expression_fields(expression.left) | expression_fields(expression.right)
    return set()


def expression_type(expression: Expression, source: SourceBinding) -> str:
    if isinstance(expression, NumberExpression):
        return "number"
    if isinstance(expression, FieldExpression):
        binding = fields(source).get(expression.field_id)
        if binding is None:
            fail("UNKNOWN_FIELD", f"Unknown expression field: {expression.field_id}")
        return binding.data_type
    if isinstance(expression, BinaryExpression):
        if (
            expression_type(expression.left, source) != "number"
            or expression_type(expression.right, source) != "number"
        ):
            fail("EXPRESSION_TYPE_MISMATCH", "Every arithmetic operand must be numeric")
        return "number"
    fail("INVALID_ATOMIC", "Atomic expressions cannot refer to metric aggregates")


def source_semantics(source: SourceBinding) -> tuple[object, ...]:
    normalized = []
    for predicate in source.required_filters:
        item = predicate.model_dump(mode="json")
        item["field"]["source_id"] = "$fact"
        normalized.append(json.dumps(item, sort_keys=True))
    return tuple(sorted(source.grain)), tuple(sorted(normalized))


def output_identifier(identifier: str) -> None:
    # PostgreSQL truncates longer identifiers even when quoted, potentially
    # collapsing distinct metric names into the same result column.
    if len(identifier.encode("utf-8")) > 63:
        fail(
            "IDENTIFIER_TOO_LONG",
            f"Output identifier exceeds PostgreSQL's 63-byte limit: {identifier}",
        )


def metric_closure(model: GovernedModel, metric_ids: tuple[str, ...]) -> list[Metric]:
    by_id = {metric.metric_id: metric for metric in model.metrics}
    ordered: list[Metric] = []
    visited: set[str] = set()

    def visit(metric_id: str, stack: set[str]) -> None:
        if metric_id not in by_id:
            fail("UNKNOWN_METRIC", f"Unknown metric: {metric_id}")
        if metric_id in stack:
            fail("DEPENDENCY_CYCLE", f"Cyclic metric dependency: {metric_id}")
        if metric_id in visited:
            return
        metric = by_id[metric_id]
        for dependency in sorted(expression_metrics(metric.expression)):
            visit(dependency, stack | {metric_id})
        visited.add(metric_id)
        ordered.append(metric)

    for metric_id in metric_ids:
        visit(metric_id, set())
    return ordered


def resolve_field(model: GovernedModel, ref: FieldRef) -> FieldBinding:
    source = next(
        (item for item in model.sources if item.source_id == ref.source_id), None
    )
    if source is None or ref.field_id not in fields(source):
        fail("UNKNOWN_FIELD", f"Unknown field: {ref.source_id}.{ref.field_id}")
    return fields(source)[ref.field_id]


def relationship_path(
    model: GovernedModel, source_id: str, target_id: str
) -> tuple[Relationship, ...]:
    """Resolve one directed path only. Business-role ambiguity is never guessed."""
    if source_id == target_id:
        return ()
    paths: list[tuple[Relationship, ...]] = []

    def walk(node: str, path: tuple[Relationship, ...], seen: set[str]) -> None:
        if len(paths) > 1:
            return
        for relation in model.relationships:
            if relation.source_id != node or relation.target_id in seen:
                continue
            next_path = (*path, relation)
            if relation.target_id == target_id:
                paths.append(next_path)
            else:
                walk(relation.target_id, next_path, seen | {relation.target_id})

    walk(source_id, (), {source_id})
    if not paths:
        fail(
            "NO_RELATIONSHIP_PATH",
            f"No directed relationship from {source_id} to {target_id}",
        )
    if len(paths) > 1:
        fail(
            "AMBIGUOUS_RELATIONSHIP",
            f"Multiple roles connect {source_id} to {target_id}; declare an explicit dimension path",
        )
    ensure_safe_path(paths[0])
    return paths[0]


def ensure_safe_path(path: tuple[Relationship, ...]) -> None:
    for relation in path:
        if relation.cardinality not in ("many_to_one", "one_to_one"):
            fail(
                "FANOUT_RISK",
                f"Relationship {relation.relationship_id} can expand fact rows",
            )


def dimension_path(
    model: GovernedModel, source: SourceBinding, dimension_id: str
) -> tuple[FieldRef, tuple[Relationship, ...]]:
    dimension = next(
        (item for item in source.dimensions if item.dimension_id == dimension_id), None
    )
    if dimension is None:
        fail(
            "UNSUPPORTED_DIMENSION",
            f"Source {source.source_id} has no dimension {dimension_id}",
        )
    by_id = {relation.relationship_id: relation for relation in model.relationships}
    path: list[Relationship] = []
    current = source.source_id
    for relationship_id in dimension.relationship_path:
        relation = by_id.get(relationship_id)
        if relation is None or relation.source_id != current:
            fail(
                "INVALID_RELATIONSHIP_PATH",
                f"Invalid path for dimension {dimension_id}",
            )
        path.append(relation)
        current = relation.target_id
    if current != dimension.field.source_id:
        fail(
            "INVALID_RELATIONSHIP_PATH",
            f"Dimension {dimension_id} does not end at its field source",
        )
    ensure_safe_path(tuple(path))
    resolve_field(model, dimension.field)
    return dimension.field, tuple(path)


def currency_ref(
    model: GovernedModel, source: SourceBinding, metric: Metric
) -> FieldRef | None:
    if not metric.currency_field_id:
        return None
    if metric.currency_field_id in fields(source):
        return FieldRef(source_id=source.source_id, field_id=metric.currency_field_id)
    matching = [
        dimension
        for dimension in source.dimensions
        if dimension.field.field_id == metric.currency_field_id
    ]
    refs = {
        (dimension.field.source_id, dimension.field.field_id) for dimension in matching
    }
    if (
        len(refs) != 1
        or len({dimension.relationship_path for dimension in matching}) > 1
    ):
        fail(
            "AMBIGUOUS_CURRENCY",
            f"Metric {metric.metric_id} needs one registered currency field",
        )
    source_id, field_id = next(iter(refs))
    return FieldRef(source_id=source_id, field_id=field_id)


def logical_field_ref(
    model: GovernedModel, source: SourceBinding, field_id: str
) -> FieldRef:
    if field_id in fields(source):
        return FieldRef(source_id=source.source_id, field_id=field_id)
    candidates = []
    for target in model.sources:
        if field_id not in fields(target):
            continue
        try:
            relationship_path(model, source.source_id, target.source_id)
        except ContractError as error:
            if error.code == "NO_RELATIONSHIP_PATH":
                continue
            raise
        candidates.append(FieldRef(source_id=target.source_id, field_id=field_id))
    if len(candidates) != 1:
        fail("AMBIGUOUS_FIELD", f"Field {field_id} must have one reachable binding")
    return candidates[0]


def numeric_value(value: object) -> int | float | Decimal:
    if isinstance(value, bool):
        fail("FILTER_TYPE_MISMATCH", "Boolean values are not numeric filter values")
    if isinstance(value, (int, float, Decimal)):
        # Binding a Python float as PostgreSQL float8 can coerce an exact numeric
        # column to float8 during comparison. Preserve its decimal representation
        # as a numeric parameter instead; clients needing more digits use text.
        parsed = Decimal(str(value)) if isinstance(value, float) else value
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            fail("FILTER_TYPE_MISMATCH", "Expected an exact decimal string")
    else:
        fail("FILTER_TYPE_MISMATCH", "Expected a numeric filter value")
    if not Decimal(str(parsed)).is_finite():
        fail("FILTER_TYPE_MISMATCH", "Numeric filter values must be finite")
    return parsed


def validate_predicate(model: GovernedModel, predicate: Predicate) -> None:
    field = resolve_field(model, predicate.field)
    value = predicate.value
    if predicate.operator in ("is_null", "is_not_null"):
        if value is not None:
            fail("INVALID_FILTER", "NULL tests do not accept a value")
        return
    if predicate.operator in ("in", "not_in"):
        if not isinstance(value, tuple) or not value:
            fail("INVALID_FILTER", "IN requires a nonempty value list")
        values = value
    else:
        if isinstance(value, tuple):
            fail("INVALID_FILTER", "This comparison accepts one value")
        values = (value,)
    for item in values:
        if item is None:
            fail(
                "INVALID_FILTER", "Use is_null/is_not_null instead of NULL comparisons"
            )
        if field.data_type == "number":
            numeric_value(item)
        if field.data_type == "boolean" and not isinstance(item, bool):
            fail("FILTER_TYPE_MISMATCH", f"Boolean value required for {field.field_id}")
        if field.data_type == "string" and not isinstance(item, str):
            fail("FILTER_TYPE_MISMATCH", f"String value required for {field.field_id}")
        if field.data_type in ("date", "datetime"):
            try:
                if not isinstance(item, str):
                    raise ValueError("Expected ISO text")
                parsed = (
                    date.fromisoformat(item)
                    if field.data_type == "date"
                    else datetime.fromisoformat(item)
                )
                if isinstance(parsed, datetime) and parsed.utcoffset() is None:
                    raise ValueError("Timezone required")
            except ValueError:
                fail(
                    "FILTER_TYPE_MISMATCH",
                    f"Explicit ISO {field.data_type} required for {field.field_id}",
                )


def _unique(values: list[str], label: str) -> None:
    if any(not item or "\x00" in item for item in values) or len(values) != len(
        set(values)
    ):
        fail("INVALID_ID", f"{label} must be nonempty, unique and contain no NUL")


def _unit(
    expression: Expression, metrics: dict[str, Metric]
) -> tuple[str, str | None, str | None]:
    if isinstance(expression, NumberExpression):
        return "number", None, None
    if isinstance(expression, MetricExpression):
        metric = metrics[expression.metric_id]
        return metric.unit, metric.currency_field_id, metric.fixed_currency
    if not isinstance(expression, BinaryExpression):
        fail(
            "INVALID_COMPOSITE",
            "Composite expressions may only reference metrics and constants",
        )
    left, right = _unit(expression.left, metrics), _unit(expression.right, metrics)
    if expression.operator in ("add", "subtract"):
        if left != right:
            fail(
                "UNIT_MISMATCH",
                "Addition/subtraction requires identical units and currency semantics",
            )
        return left
    if left[1:] != (None, None) and right[1:] != (None, None):
        if expression.operator == "divide" and left == right:
            return "number", None, None
        fail("UNIT_MISMATCH", "Compound currency operations are not supported")
    if expression.operator == "divide" and right[1:] != (None, None):
        fail("UNIT_MISMATCH", "Reciprocal currency units are unsupported")
    if expression.operator == "multiply":
        symbol = (
            right[0]
            if left[0] == "number"
            else left[0]
            if right[0] == "number"
            else f"{left[0]}*{right[0]}"
        )
    else:
        symbol = (
            left[0]
            if right[0] == "number"
            else "number"
            if left[0] == right[0]
            else f"{left[0]}/{right[0]}"
        )
    money = left[1:] if left[1:] != (None, None) else right[1:]
    return symbol, money[0], money[1]


def _validate_model(model: GovernedModel) -> None:
    _unique([source.source_id for source in model.sources], "Source IDs")
    _unique([metric.metric_id for metric in model.metrics], "Metric IDs")
    _unique(
        [relation.relationship_id for relation in model.relationships],
        "Relationship IDs",
    )
    sources = {source.source_id: source for source in model.sources}
    metrics = {metric.metric_id: metric for metric in model.metrics}
    if not sources or not metrics:
        fail("EMPTY_MODEL", "A governed model requires sources and metrics")
    for source in model.sources:
        _unique([field.field_id for field in source.fields], "Field IDs")
        _unique(
            [dimension.dimension_id for dimension in source.dimensions], "Dimension IDs"
        )
        for dimension in source.dimensions:
            output_identifier(dimension.dimension_id)
        _unique(list(source.grain), "Grain fields")
        if not source.grain or not set(source.grain) <= fields(source).keys():
            fail(
                "INVALID_GRAIN",
                f"Source {source.source_id} requires a complete registered grain",
            )
        for identifier in (
            source.schema_name,
            source.table_name,
            *(field.column_name for field in source.fields),
        ):
            if (
                not identifier
                or "\x00" in identifier
                or len(identifier.encode("utf-8")) > 63
            ):
                fail(
                    "INVALID_IDENTIFIER",
                    "Physical identifiers must be nonempty, at most 63 UTF-8 bytes, and contain no NUL",
                )
        for predicate in source.required_filters:
            if predicate.field.source_id != source.source_id:
                fail(
                    "INVALID_SOURCE_FILTER", "Source filters must refer to that source"
                )
            validate_predicate(model, predicate)
    for relation in model.relationships:
        if relation.source_id == relation.target_id:
            fail(
                "UNSUPPORTED_SELF_RELATIONSHIP",
                "Self relationships require separate role-specific source bindings",
            )
        if (
            relation.source_id not in sources
            or relation.target_id not in sources
            or not relation.keys
        ):
            fail(
                "INVALID_RELATIONSHIP",
                f"Invalid endpoints/keys: {relation.relationship_id}",
            )
        source, target = sources[relation.source_id], sources[relation.target_id]
        if source.datasource_id != target.datasource_id:
            fail("CROSS_DATASOURCE", "Cross-datasource joins are unsupported")
        _unique([key.source_field_id for key in relation.keys], "Source join keys")
        _unique([key.target_field_id for key in relation.keys], "Target join keys")
        for key in relation.keys:
            left = resolve_field(
                model,
                FieldRef(source_id=source.source_id, field_id=key.source_field_id),
            )
            right = resolve_field(
                model,
                FieldRef(source_id=target.source_id, field_id=key.target_field_id),
            )
            if left.data_type != right.data_type:
                fail(
                    "JOIN_TYPE_MISMATCH",
                    f"Mismatched key types in {relation.relationship_id}",
                )
        if relation.cardinality in ("many_to_one", "one_to_one") and not set(
            target.grain
        ) <= {key.target_field_id for key in relation.keys}:
            fail(
                "INCOMPLETE_JOIN_KEY",
                f"Relationship {relation.relationship_id} does not cover target grain",
            )
        for predicate in relation.predicates:
            if predicate.field.source_id not in (
                relation.source_id,
                relation.target_id,
            ):
                fail(
                    "INVALID_RELATIONSHIP_FILTER",
                    "Relationship predicates must refer to their endpoints",
                )
            validate_predicate(model, predicate)
    for metric in model.metrics:
        output_identifier(metric.metric_id)
        if not metric.source_ids or not set(metric.source_ids) <= sources.keys():
            fail("UNKNOWN_SOURCE", f"Invalid sources for {metric.metric_id}")
        _unique(list(metric.source_ids), "Metric source IDs")
        alternatives = [
            sources[source_id]
            for source_id in metric.source_ids
            if not sources[source_id].pre_aggregated
        ]
        if len({source_semantics(source) for source in alternatives}) > 1:
            fail(
                "INCOMPATIBLE_SOURCE_SEMANTICS",
                "Alternative fact sources require identical logical grain and mandatory row scope",
            )
        logical_types: dict[str, str] = {}
        dimension_types: dict[str, str] = {}
        for source in alternatives:
            for field in source.fields:
                if (
                    field.field_id in logical_types
                    and logical_types[field.field_id] != field.data_type
                ):
                    fail(
                        "INCOMPATIBLE_SOURCE_SEMANTICS",
                        "Alternative sources cannot change a logical field's data type",
                    )
                logical_types[field.field_id] = field.data_type
            for dimension in source.dimensions:
                dimension_type = resolve_field(model, dimension.field).data_type
                if (
                    dimension.dimension_id in dimension_types
                    and dimension_types[dimension.dimension_id] != dimension_type
                ):
                    fail(
                        "INCOMPATIBLE_SOURCE_SEMANTICS",
                        "Alternative sources cannot change a dimension's data type",
                    )
                dimension_types[dimension.dimension_id] = dimension_type
        if metric.currency_field_id and metric.fixed_currency:
            fail(
                "INVALID_CURRENCY", "Use a currency field or a fixed currency, not both"
            )
        if "currency" in metric.unit.split("/")[0].split("*") and not (
            metric.currency_field_id or metric.fixed_currency
        ):
            fail(
                "INVALID_CURRENCY",
                "Currency units require a currency field or fixed currency",
            )
        if (metric.time_field_id is None) != (metric.time_role is None):
            fail("INVALID_TIME", "Time field and time role must be configured together")
        if metric.supported_granularities and not metric.time_field_id:
            fail("INVALID_TIME", "Time granularities require a configured time field")
        if not alternatives:
            fail(
                "UNSUPPORTED_PREAGGREGATION",
                f"Metric {metric.metric_id} requires an approved detail source",
            )
        for dimension_id in metric.supported_dimensions:
            errors = []
            for source in alternatives:
                try:
                    dimension_path(model, source, dimension_id)
                    break
                except ContractError as error:
                    errors.append(error)
            else:
                raise errors[0]
        if metric.time_field_id:
            errors = []
            for source in alternatives:
                try:
                    ref = logical_field_ref(model, source, metric.time_field_id)
                    if resolve_field(model, ref).data_type not in ("date", "datetime"):
                        fail(
                            "INVALID_TIME_FIELD",
                            "Time fields must have date/datetime type",
                        )
                    break
                except ContractError as error:
                    errors.append(error)
            else:
                raise errors[0]
        if metric.currency_field_id:
            errors = []
            for source in alternatives:
                try:
                    ref = currency_ref(model, source, metric)
                    if resolve_field(model, ref).data_type != "string":
                        fail(
                            "INVALID_CURRENCY", "Currency fields must have string type"
                        )
                    break
                except ContractError as error:
                    errors.append(error)
            else:
                raise errors[0]
        if metric.kind == "atomic":
            if metric.aggregation is None or expression_metrics(metric.expression):
                fail(
                    "INVALID_ATOMIC",
                    "Atomic metrics require aggregation and cannot reference metrics",
                )
            if metric.aggregation != "count" and metric.expression is None:
                fail("INVALID_ATOMIC", "SUM and COUNT DISTINCT require an expression")
            compatible = []
            failures = []
            for source_id in metric.source_ids:
                try:
                    result_type = (
                        expression_type(metric.expression, sources[source_id])
                        if metric.expression
                        else "number"
                    )
                    if metric.aggregation == "sum" and result_type != "number":
                        fail(
                            "EXPRESSION_TYPE_MISMATCH",
                            "SUM expressions require numeric fields",
                        )
                    compatible.append(source_id)
                except ContractError as error:
                    failures.append(error)
            if not compatible:
                raise failures[0]
            for predicate in metric.required_filters:
                validate_predicate(model, predicate)
        else:
            if metric.empty_value != "null":
                fail(
                    "UNSUPPORTED_EMPTY_VALUE",
                    "Composite empty-value overrides are unsupported; configure atomic dependencies",
                )
            if (
                metric.aggregation is not None
                or metric.expression is None
                or not expression_metrics(metric.expression)
                or expression_fields(metric.expression)
            ):
                fail(
                    "INVALID_COMPOSITE",
                    "Composite metrics require a metric expression and no aggregation/field references",
                )
            if metric.required_filters:
                fail(
                    "INVALID_COMPOSITE",
                    "Composite filters must be defined on the atomic dependencies",
                )
    metric_closure(model, tuple(metrics))
    for metric in model.metrics:
        if metric.kind == "composite":
            expected = _unit(metric.expression, metrics)
            if expected != (
                metric.unit,
                metric.currency_field_id,
                metric.fixed_currency,
            ):
                fail(
                    "UNIT_MISMATCH",
                    f"Composite {metric.metric_id} must declare unit/currency {expected}",
                )
            children = [
                metrics[child] for child in expression_metrics(metric.expression)
            ]
            if not set.intersection(
                set(metric.source_ids), *(set(child.source_ids) for child in children)
            ):
                fail(
                    "UNSUPPORTED_MULTI_FACT",
                    "Composite dependencies require a shared fact source",
                )
            if any(
                not set(metric.supported_dimensions) <= set(child.supported_dimensions)
                for child in children
            ):
                fail(
                    "UNSUPPORTED_DIMENSION",
                    "Composite dimensions must be supported by every dependency",
                )
            if any(
                (child.time_role, child.time_field_id)
                != (metric.time_role, metric.time_field_id)
                for child in children
            ):
                fail(
                    "TIME_ROLE_MISMATCH",
                    "Composite dependencies require matching time semantics",
                )
    defaults: set[str] = set()
    for metric in model.metrics:
        if metric.default_of_family:
            if not metric.family_id or metric.family_id in defaults:
                fail("DEFAULT_CONFLICT", "A family can have only one default")
            defaults.add(metric.family_id)


def validate_model(model: GovernedModel) -> ValidationReport:
    try:
        _validate_model(model)
    except ContractError as error:
        return ValidationReport(
            valid=False,
            issues=(
                ValidationIssue(
                    code=error.code, message=error.message, path=error.path
                ),
            ),
        )
    return ValidationReport(valid=True)


def validate_query(model: GovernedModel, request: MetricQueryRequest) -> list[Metric]:
    _validate_model(model)
    if not request.metric_ids:
        fail("EMPTY_QUERY", "Select at least one metric")
    _unique(list(request.metric_ids), "Requested metrics")
    _unique(list(request.dimensions), "Requested dimensions")
    if set(request.metric_ids) & set(request.dimensions) or "__time" in (
        *request.metric_ids,
        *request.dimensions,
    ):
        fail(
            "OUTPUT_CONFLICT", "Output identifiers must be distinct; __time is reserved"
        )
    closure = metric_closure(model, request.metric_ids)
    for metric in closure:
        if not set(request.dimensions) <= set(metric.supported_dimensions):
            fail(
                "UNSUPPORTED_DIMENSION",
                f"Metric {metric.metric_id} does not support every requested dimension",
            )
    if (request.time is None) == (not request.all_time):
        fail(
            "TIME_RANGE_REQUIRED",
            "Choose an explicit time range or all_time, exclusively",
        )
    if request.time:
        time = request.time
        if (
            time.start.utcoffset() is None
            or time.end_exclusive.utcoffset() is None
            or time.start >= time.end_exclusive
        ):
            fail(
                "INVALID_TIME_RANGE",
                "Time bounds must be timezone-aware and start must precede end",
            )
        try:
            ZoneInfo(time.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            fail("INVALID_TIMEZONE", f"Unknown timezone: {time.timezone}")
        for metric in closure:
            if not metric.time_field_id or metric.time_role != time.time_role:
                fail(
                    "TIME_ROLE_MISMATCH",
                    f"Metric {metric.metric_id} cannot use requested time role",
                )
            if (
                time.granularity
                and time.granularity not in metric.supported_granularities
            ):
                fail(
                    "UNSUPPORTED_GRANULARITY",
                    f"Metric {metric.metric_id} does not support {time.granularity}",
                )
    for predicate in request.filters:
        validate_predicate(model, predicate)
    outputs = set(request.metric_ids) | set(request.dimensions)
    if request.time and request.time.granularity:
        outputs.add("__time")
    for ordering in request.order_by:
        if ordering.field_id not in outputs:
            fail(
                "UNKNOWN_SORT_FIELD", f"Sort field is not selected: {ordering.field_id}"
            )
    return closure
