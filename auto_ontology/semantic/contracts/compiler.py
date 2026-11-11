# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic emission from the closed metric expression/relationship AST.

All data is bound through psycopg named parameters. Only registered identifiers
and fixed compiler tokens enter SQL. Each atomic metric aggregates in its own
scope; grouping keys are unioned and joined with NULL-safe equality afterwards.
"""

from __future__ import annotations

from datetime import datetime, time as clock_time
from decimal import Decimal
from zoneinfo import ZoneInfo

from .models import (
    BinaryExpression,
    ContractError,
    Expression,
    FieldExpression,
    FieldRef,
    GovernedModel,
    Metric,
    MetricExpression,
    MetricQueryRequest,
    NumberExpression,
    Plan,
    Predicate,
    Relationship,
    Scalar,
    SourceBinding,
    content_fingerprint,
)
from .validator import (
    currency_ref,
    dimension_path,
    expression_type,
    fail,
    relationship_path,
    numeric_value,
    logical_field_ref,
    resolve_field,
    validate_query,
)


def quote(identifier: str) -> str:
    """Quote one PostgreSQL identifier (never a dotted SQL fragment)."""
    if not identifier or "\x00" in identifier:
        fail("INVALID_IDENTIFIER", "An identifier must be nonempty and contain no NUL")
    # psycopg's pyformat parser also reads quoted identifiers when parameters
    # are supplied, so a literal percent must be doubled at that boundary.
    return '"' + identifier.replace('"', '""').replace("%", "%%") + '"'


def table(source: SourceBinding) -> str:
    return f"{quote(source.schema_name)}.{quote(source.table_name)}"


class Parameters:
    def __init__(self) -> None:
        self.values: dict[str, Scalar | Decimal | datetime] = {}

    def bind(self, value: Scalar | Decimal | datetime) -> str:
        name = f"p{len(self.values)}"
        self.values[name] = value
        return f"%({name})s"


class FactScope:
    def __init__(
        self,
        model: GovernedModel,
        source: SourceBinding,
        metric: Metric,
        request: MetricQueryRequest,
        parameters: Parameters,
    ) -> None:
        self.model = model
        self.source = source
        self.metric = metric
        self.request = request
        self.parameters = parameters
        self.sources = {item.source_id: item for item in model.sources}
        self.aliases: dict[tuple[str, ...], str] = {(): "f"}
        self.joins: list[str] = []
        self.used_sources = {source.source_id}
        self.join_evidence: list[dict[str, object]] = []
        self.currency_scope: tuple[str, ...] | None = None

    def remap(self, ref: FieldRef) -> FieldRef:
        # Alternative fact bindings share logical field IDs. A predicate naming
        # one of the approved alternatives is rebound only within that same fact.
        if ref.source_id in self.metric.source_ids:
            return FieldRef(source_id=self.source.source_id, field_id=ref.field_id)
        return ref

    def column(self, ref: FieldRef, alias: str) -> str:
        field = resolve_field(self.model, ref)
        return f"{alias}.{quote(field.column_name)}"

    def add_path(self, path: tuple[Relationship, ...]) -> str:
        key: tuple[str, ...] = ()
        for relation in path:
            parent = key
            key = (*key, relation.relationship_id)
            if key in self.aliases:
                continue
            if relation.cardinality not in ("many_to_one", "one_to_one"):
                fail(
                    "FANOUT_RISK",
                    f"Relationship {relation.relationship_id} expands the fact",
                )
            alias = f"d{len(self.aliases) - 1}"
            self.aliases[key] = alias
            previous = self.aliases[parent]
            target = self.sources[relation.target_id]
            if target.pre_aggregated:
                fail(
                    "UNSUPPORTED_PREAGGREGATION",
                    "Preaggregated join sources are unsupported",
                )
            if target.datasource_id != self.source.datasource_id:
                fail("CROSS_DATASOURCE", "Cross-datasource joins are unsupported")
            endpoint_aliases = {relation.source_id: previous, relation.target_id: alias}
            conditions = [
                f"{self.column(FieldRef(source_id=relation.source_id, field_id=item.source_field_id), previous)} = "
                f"{self.column(FieldRef(source_id=relation.target_id, field_id=item.target_field_id), alias)}"
                for item in relation.keys
            ]
            for predicate in (*relation.predicates, *target.required_filters):
                conditions.append(self.predicate(predicate, endpoint_aliases))
            self.joins.append(
                f"{relation.join_type.upper()} JOIN {table(target)} {alias} ON "
                + " AND ".join(f"({item})" for item in conditions)
            )
            self.used_sources.add(target.source_id)
            self.join_evidence.append(
                {
                    "relationship_id": relation.relationship_id,
                    "role": relation.role,
                    "join_type": relation.join_type,
                    "keys": [item.model_dump(mode="json") for item in relation.keys],
                    "predicates": [
                        item.model_dump(mode="json") for item in relation.predicates
                    ],
                    "target_filters": [
                        item.model_dump(mode="json") for item in target.required_filters
                    ],
                }
            )
        return self.aliases[key]

    def use_ref(
        self, original: FieldRef, path: tuple[Relationship, ...] | None = None
    ) -> str:
        ref = self.remap(original) if path is None else original
        if path is None:
            path = relationship_path(self.model, self.source.source_id, ref.source_id)
        return self.column(ref, self.add_path(path))

    def logical_ref(self, field_id: str) -> FieldRef:
        return logical_field_ref(self.model, self.source, field_id)

    def predicate(
        self, predicate: Predicate, aliases: dict[str, str] | None = None
    ) -> str:
        ref = self.remap(predicate.field) if aliases is None else predicate.field
        column = (
            self.column(ref, aliases[ref.source_id]) if aliases else self.use_ref(ref)
        )
        if predicate.operator in ("is_null", "is_not_null"):
            return f"{column} IS {'NOT ' if predicate.operator == 'is_not_null' else ''}NULL"
        field = resolve_field(self.model, ref)

        def value_sql(value: Scalar) -> str:
            if field.data_type == "number":
                value = numeric_value(value)
            bound = self.parameters.bind(value)
            if field.data_type == "date":
                return f"CAST({bound} AS DATE)"
            if field.data_type == "datetime":
                return f"CAST({bound} AS TIMESTAMPTZ)"
            return bound

        if predicate.operator in ("in", "not_in"):
            values = ", ".join(value_sql(item) for item in predicate.value)
            return f"{column} {'NOT IN' if predicate.operator == 'not_in' else 'IN'} ({values})"
        operators = {
            "eq": "=",
            "ne": "<>",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }
        return f"{column} {operators[predicate.operator]} {value_sql(predicate.value)}"

    def row_expression(self, expression: Expression) -> str:
        if isinstance(expression, FieldExpression):
            return self.column(
                FieldRef(source_id=self.source.source_id, field_id=expression.field_id),
                "f",
            )
        if isinstance(expression, NumberExpression):
            return self.parameters.bind(expression.value)
        if isinstance(expression, BinaryExpression):
            return binary_sql(
                expression,
                self.row_expression(expression.left),
                self.row_expression(expression.right),
                self.metric.zero_division,
            )
        fail("INVALID_ATOMIC", "Atomic expressions cannot reference other metrics")

    def currency_check(self) -> None:
        if self.metric.fixed_currency:
            self.currency_scope = ("fixed", self.metric.fixed_currency)
        ref = currency_ref(self.model, self.source, self.metric)
        if ref is None:
            return
        if resolve_field(self.model, ref).data_type != "string":
            fail("INVALID_CURRENCY", "Currency fields must be strings")
        grouped = any(
            dimension_path(self.model, self.source, dimension)[0] == ref
            for dimension in self.request.dimensions
        )
        predicates = (
            *self.source.required_filters,
            *self.metric.required_filters,
            *self.request.filters,
        )
        fixed = any(
            self.remap(predicate.field) == ref
            and (
                predicate.operator == "eq"
                and isinstance(predicate.value, str)
                or predicate.operator == "in"
                and isinstance(predicate.value, tuple)
                and len(predicate.value) == 1
                and isinstance(predicate.value[0], str)
            )
            for predicate in predicates
        )
        if not grouped and not fixed:
            fail(
                "UNIT_MISMATCH",
                f"Metric {self.metric.metric_id} requires currency grouping or a single-currency filter",
            )
        if grouped:
            non_null_operators = {
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "is_not_null",
            }
            direct_non_null = any(
                self.remap(predicate.field) == ref
                and predicate.operator in non_null_operators
                for predicate in predicates
            )
            paths = [
                dimension_path(self.model, self.source, dimension)[1]
                for dimension in self.request.dimensions
                if dimension_path(self.model, self.source, dimension)[0] == ref
            ]
            target_present = all(
                relation.join_type == "inner" for path in paths for relation in path
            ) or any(
                self.remap(predicate.field).source_id == ref.source_id
                and predicate.operator in non_null_operators
                for predicate in predicates
            )
            if not direct_non_null and (
                resolve_field(self.model, ref).nullable or not target_present
            ):
                fail(
                    "UNKNOWN_CURRENCY",
                    "Currency groups must be non-null; declare an explicit non-null/single-currency scope or repair missing currency data",
                )
            self.currency_scope = ("group", ref.source_id, ref.field_id)
        else:
            values = {
                predicate.value if predicate.operator == "eq" else predicate.value[0]
                for predicate in predicates
                if self.remap(predicate.field) == ref
                and (
                    predicate.operator == "eq"
                    and isinstance(predicate.value, str)
                    or predicate.operator == "in"
                    and isinstance(predicate.value, tuple)
                    and len(predicate.value) == 1
                    and isinstance(predicate.value[0], str)
                )
            }
            self.currency_scope = ("fixed", *sorted(values))

    def compile(self) -> tuple[str, list[str]]:
        if self.metric.expression:
            result_type = expression_type(self.metric.expression, self.source)
            if self.metric.aggregation == "sum" and result_type != "number":
                fail(
                    "EXPRESSION_TYPE_MISMATCH", "SUM expressions require numeric fields"
                )
        self.currency_check()
        dimension_sql: list[str] = []
        output_dimensions = list(self.request.dimensions)
        for dimension_id in self.request.dimensions:
            ref, path = dimension_path(self.model, self.source, dimension_id)
            dimension_sql.append(self.use_ref(ref, path))
        where = [
            self.predicate(predicate)
            for predicate in (
                *self.source.required_filters,
                *self.metric.required_filters,
                *self.request.filters,
            )
        ]
        if self.request.time:
            time = self.request.time
            ref = self.logical_ref(self.metric.time_field_id)
            field = resolve_field(self.model, ref)
            if field.data_type not in ("datetime", "date"):
                fail("INVALID_TIME_FIELD", "Time fields must be date/datetime")
            column = self.use_ref(ref)
            if field.data_type == "date":
                zone = ZoneInfo(time.timezone)
                start, end = (
                    time.start.astimezone(zone),
                    time.end_exclusive.astimezone(zone),
                )
                if start.time() != clock_time() or end.time() != clock_time():
                    fail(
                        "DATE_RANGE_ALIGNMENT",
                        "DATE fields require local-midnight bounds",
                    )
                start_sql = (
                    f"CAST({self.parameters.bind(start.date().isoformat())} AS DATE)"
                )
                end_sql = (
                    f"CAST({self.parameters.bind(end.date().isoformat())} AS DATE)"
                )
                grouping_time = column
            else:
                start_sql = f"CAST({self.parameters.bind(time.start)} AS TIMESTAMPTZ)"
                end_sql = (
                    f"CAST({self.parameters.bind(time.end_exclusive)} AS TIMESTAMPTZ)"
                )
                grouping_time = (
                    f"({column} AT TIME ZONE {self.parameters.bind(time.timezone)})"
                    if time.granularity
                    else column
                )
            where.extend([f"{column} >= {start_sql}", f"{column} < {end_sql}"])
            if time.granularity:
                dimension_sql.append(
                    f"DATE_TRUNC({self.parameters.bind(time.granularity)}, {grouping_time})"
                )
                output_dimensions.append("__time")
        expression = (
            self.row_expression(self.metric.expression)
            if self.metric.expression
            else "*"
        )
        if self.metric.aggregation == "count_distinct":
            aggregate = f"COUNT(DISTINCT {expression})"
        else:
            aggregate = f"{self.metric.aggregation.upper()}({expression})"
        select = [f"{item} AS d{index}" for index, item in enumerate(dimension_sql)] + [
            f"{aggregate} AS v"
        ]
        sql = f"SELECT {', '.join(select)} FROM {table(self.source)} f"
        if self.joins:
            sql += " " + " ".join(self.joins)
        if where:
            sql += " WHERE " + " AND ".join(f"({item})" for item in where)
        if dimension_sql:
            sql += " GROUP BY " + ", ".join(
                str(index + 1) for index in range(len(dimension_sql))
            )
        return sql, output_dimensions


def binary_sql(
    expression: BinaryExpression, left: str, right: str, zero_division: str
) -> str:
    if expression.operator == "divide":
        denominator = (
            f"NULLIF(({right}), 0)" if zero_division == "null" else f"({right})"
        )
        return f"(CAST(({left}) AS NUMERIC) / {denominator})"
    operator = {"add": "+", "subtract": "-", "multiply": "*"}[expression.operator]
    return f"(({left}) {operator} ({right}))"


def _compile_source(
    model: GovernedModel,
    request: MetricQueryRequest,
    closure: list[Metric],
    source: SourceBinding,
) -> Plan:
    if source.pre_aggregated:
        fail(
            "UNSUPPORTED_PREAGGREGATION",
            "Preaggregated sources are not supported in this release",
        )
    parameters = Parameters()
    atomics = [metric for metric in closure if metric.kind == "atomic"]
    metrics = {metric.metric_id: metric for metric in closure}
    atomic_aliases = {
        metric.metric_id: f"m{index}" for index, metric in enumerate(atomics)
    }
    ctes: list[str] = []
    scopes: list[FactScope] = []
    output_dimensions: list[str] = []
    for metric in atomics:
        scope = FactScope(model, source, metric, request, parameters)
        sql, output_dimensions = scope.compile()
        scopes.append(scope)
        ctes.append(f"{atomic_aliases[metric.metric_id]} AS ({sql})")

    atomic_currencies = {
        scope.metric.metric_id: scope.currency_scope for scope in scopes
    }

    def effective_currency(metric_id: str) -> tuple[str, ...] | None:
        metric = metrics[metric_id]
        if metric.kind == "atomic":
            return atomic_currencies[metric_id]

        def infer(expression: Expression) -> tuple[str, ...] | None:
            if isinstance(expression, NumberExpression):
                return None
            if isinstance(expression, MetricExpression):
                return effective_currency(expression.metric_id)
            left, right = infer(expression.left), infer(expression.right)
            if expression.operator in ("add", "subtract"):
                if left != right:
                    fail(
                        "UNIT_MISMATCH",
                        "Composite operands have different effective currency scopes",
                    )
                return left
            if (
                expression.operator == "divide"
                and left is not None
                and right is not None
            ):
                if left != right:
                    fail(
                        "UNIT_MISMATCH",
                        "A ratio cannot cancel different effective currencies",
                    )
                return None
            return left or right

        return infer(metric.expression)

    for metric_id in request.metric_ids:
        effective_currency(metric_id)

    def metric_sql(metric_id: str) -> str:
        metric = metrics[metric_id]
        if metric.kind == "atomic":
            value = f"{atomic_aliases[metric_id]}.v"
            if (
                metric.aggregation in ("count", "count_distinct")
                or metric.empty_value == "zero"
            ):
                return f"COALESCE({value}, 0)"
            return value

        def emit(expression: Expression) -> str:
            if isinstance(expression, MetricExpression):
                return metric_sql(expression.metric_id)
            if isinstance(expression, NumberExpression):
                return parameters.bind(expression.value)
            if isinstance(expression, BinaryExpression):
                return binary_sql(
                    expression,
                    emit(expression.left),
                    emit(expression.right),
                    metric.zero_division,
                )
            fail(
                "INVALID_COMPOSITE", "Composite expressions cannot reference row fields"
            )

        return emit(metric.expression)

    selection = [
        f"k.d{index} AS {quote(dimension)}"
        for index, dimension in enumerate(output_dimensions)
    ]
    selection += [
        f"{metric_sql(metric_id)} AS {quote(metric_id)}"
        for metric_id in request.metric_ids
    ]
    if output_dimensions:
        keys = ", ".join(f"d{index}" for index in range(len(output_dimensions)))
        ctes.append(
            "group_keys AS ("
            + " UNION ".join(
                f"SELECT {keys} FROM {alias}" for alias in atomic_aliases.values()
            )
            + ")"
        )
        from_sql = "group_keys k"
        for alias in atomic_aliases.values():
            condition = " AND ".join(
                f"k.d{index} IS NOT DISTINCT FROM {alias}.d{index}"
                for index in range(len(output_dimensions))
            )
            from_sql += f" LEFT JOIN {alias} ON {condition}"
    else:
        from_sql = " CROSS JOIN ".join(atomic_aliases.values())
    sql = "WITH " + ", ".join(ctes) + f" SELECT {', '.join(selection)} FROM {from_sql}"
    ordering = [(item.field_id, item.direction.upper()) for item in request.order_by]
    ordered_fields = {item[0] for item in ordering}
    ordering += [
        (dimension, "ASC")
        for dimension in output_dimensions
        if dimension not in ordered_fields
    ]
    if ordering:
        sql += " ORDER BY " + ", ".join(
            f"{quote(field_id)} {direction} NULLS LAST"
            for field_id, direction in ordering
        )
    sql += f" LIMIT {parameters.bind(request.limit)}"
    used_sources = tuple(sorted(set().union(*(scope.used_sources for scope in scopes))))
    return Plan(
        sql=sql,
        params=parameters.values,
        datasource_id=source.datasource_id,
        source_ids=used_sources,
        metric_fingerprints={
            metric.metric_id: metric.fingerprint() for metric in closure
        },
        model_fingerprint=model.fingerprint(),
        request_fingerprint=content_fingerprint(request),
        evidence={
            "selected_source_id": source.source_id,
            "source_priority": source.priority,
            "metrics": [
                {
                    "metric_id": metric.metric_id,
                    "unit": metric.unit,
                    "currency_field_id": metric.currency_field_id,
                    "fixed_currency": metric.fixed_currency,
                    "filters": [
                        predicate.model_dump(mode="json")
                        for predicate in metric.required_filters
                    ],
                }
                for metric in closure
            ],
            "source_filters": [
                predicate.model_dump(mode="json")
                for predicate in source.required_filters
            ],
            "joins": [item for scope in scopes for item in scope.join_evidence],
            "time": request.time.model_dump(mode="json")
            if request.time
            else {"all_time": True},
            "group_set": "union",
            "null_group_match": "is_not_distinct_from",
        },
    )


def compile_query(model: GovernedModel, request: MetricQueryRequest) -> Plan:
    """Validate and compile a model snapshot. Never execute or certify it."""
    closure = validate_query(model, request)
    common = set.intersection(*(set(metric.source_ids) for metric in closure))
    if not common:
        fail(
            "UNSUPPORTED_MULTI_FACT",
            "No single approved fact source covers every metric; separate fact aggregation is unsupported",
        )
    candidates = sorted(
        (source for source in model.sources if source.source_id in common),
        key=lambda source: (source.priority, source.source_id),
    )
    rejected: list[dict[str, str]] = []
    viable: list[tuple[SourceBinding, Plan]] = []
    for source in candidates:
        try:
            plan = _compile_source(model, request, closure, source)
        except ContractError as error:
            rejected.append(
                {
                    "source_id": source.source_id,
                    "code": error.code,
                    "reason": error.message,
                }
            )
            continue
        viable.append((source, plan))
    if not viable:
        if len(rejected) == 1:
            fail(rejected[0]["code"], rejected[0]["reason"])
        fail("NO_COMPATIBLE_SOURCE", f"No compatible source: {rejected}")
    best_source, best_plan = viable[0]
    if sum(source.priority == best_source.priority for source, _ in viable) > 1:
        fail(
            "AMBIGUOUS_SOURCE", "Multiple compatible sources share the highest priority"
        )
    evidence = {
        **best_plan.evidence,
        "excluded_sources": rejected,
        "selection_rule": "compatibility_then_priority",
    }
    return best_plan.model_copy(update={"evidence": evidence})
