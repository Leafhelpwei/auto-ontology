# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prove exact MySQL expression scale without casting every value to a fixed type."""

from decimal import Decimal
import re
from typing import Any

from auto_ontology.semantic.contracts.models import (
    BinaryExpression,
    ContractError,
    FieldExpression,
    GovernedModel,
    MetricExpression,
    NumberExpression,
)


def validate_mysql_arithmetic(
    model: GovernedModel,
    physical: dict[str, dict[str, dict[str, Any]]],
    *,
    metric_ids: set[str] | None = None,
) -> None:
    """Reject operations that silently discard exact fractional digits in MySQL 5.7.

    Integer width is left to strict engine range checks, permitting actual values
    that fit even when their declared maximums could overflow. Division explicitly
    uses the governed 30-place scale; later multiplication cannot silently reduce it.
    """
    metrics = {metric.metric_id: metric for metric in model.metrics}
    active: set[str] = set()
    scales: dict[str, int | None] = {}

    def expression_scale(expression, source=None):
        if isinstance(expression, FieldExpression):
            field = next(
                item for item in source.fields if item.field_id == expression.field_id
            )
            native = physical[source.source_id][field.column_name]["data_type"].lower()
            match = re.match(r"(?:decimal|numeric)\((\d+),(\d+)\)", native)
            if match:
                return int(match.group(2))
            if native.startswith(("double", "float", "real")):
                return None  # explicitly approximate native values
            return 0
        if isinstance(expression, NumberExpression):
            return max(0, -Decimal(expression.value).as_tuple().exponent)
        if isinstance(expression, MetricExpression):
            return metric_scale(expression.metric_id)
        if isinstance(expression, BinaryExpression):
            left, right = (
                expression_scale(expression.left, source),
                expression_scale(expression.right, source),
            )
            if left is None or right is None:
                return None
            if expression.operator == "multiply":
                scale = left + right
            elif expression.operator == "divide":
                scale = 30
            else:
                scale = max(left, right)
            if scale > 30:
                raise ContractError(
                    "NUMERIC_SCALE_UNSUPPORTED",
                    "This exact MySQL expression requires more than 30 fractional digits; define an explicit reviewed rounding stage or use a narrower source type",
                )
            return scale
        return 0

    def metric_scale(metric_id):
        if metric_id in scales:
            return scales[metric_id]
        if metric_id in active:
            raise ContractError("METRIC_CYCLE", "Metric dependency cycle")
        active.add(metric_id)
        metric = metrics[metric_id]
        if metric.kind == "atomic":
            alternatives = (
                [
                    expression_scale(metric.expression, source)
                    for source in model.sources
                    if source.source_id in metric.source_ids
                    and source.source_id in physical
                ]
                if metric.aggregation == "sum"
                else [0]
            )
            value = None if None in alternatives else max(alternatives, default=0)
        else:
            value = expression_scale(metric.expression)
        active.remove(metric_id)
        scales[metric_id] = value
        return value

    for metric_id in metric_ids if metric_ids is not None else metrics:
        metric_scale(metric_id)
