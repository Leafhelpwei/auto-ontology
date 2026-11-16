# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Minimal logical views for interpretation; the certified model stays on the server."""

from __future__ import annotations

from typing import Any

from auto_ontology.semantic.contracts.models import GovernedModel
from auto_ontology.semantic.contracts.validator import metric_closure


def metric_inventory(model: GovernedModel) -> list[dict[str, Any]]:
    """Expose business names for candidate retrieval, never evidence or fixed literals."""
    return [
        {
            key: metric.model_dump(mode="json")[key]
            for key in (
                "metric_id",
                "name",
                "description",
                "family_id",
                "default_of_family",
                "unit",
                "time_role",
                "supported_dimensions",
            )
        }
        for metric in model.metrics
    ]


def interpretation_view(model: GovernedModel, ids: tuple[str, ...]) -> dict[str, Any]:
    """Project the chosen dependency closure and reachable logical fields only."""
    metrics = metric_closure(model, ids)
    source_ids = {source for metric in metrics for source in metric.source_ids}
    relationships = []
    pending = list(source_ids)
    while pending:
        source_id = pending.pop()
        for relation in model.relationships:
            if relation.source_id != source_id or relation.cardinality not in {
                "many_to_one",
                "one_to_one",
            }:
                continue
            relationships.append(relation)
            if relation.target_id not in source_ids:
                source_ids.add(relation.target_id)
                pending.append(relation.target_id)
    return {
        "model_id": model.model_id,
        "name": model.name,
        "metrics": [
            metric.model_dump(mode="json", exclude={"evidence", "owner_term_id"})
            for metric in metrics
        ],
        "sources": [
            {
                "source_id": source.source_id,
                "grain": source.grain,
                "dimensions": [
                    dimension.model_dump(mode="json") for dimension in source.dimensions
                ],
                "required_filters": [
                    predicate.model_dump(mode="json")
                    for predicate in source.required_filters
                ],
                "fields": [
                    field.model_dump(
                        mode="json", exclude={"catalog_column_id", "column_name"}
                    )
                    for field in source.fields
                ],
            }
            for source in model.sources
            if source.source_id in source_ids
        ],
        "relationships": [
            relation.model_dump(mode="json") for relation in relationships
        ],
    }
