# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Governed natural-language requests cannot bypass contract validation."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from auto_ontology.semantic.contracts.models import GovernedModel, MetricQueryRequest
from auto_ontology.semantic.contracts.tests.fixtures import f0_model
from auto_ontology.server.metrics.interpretation import (
    InterpretationCandidate,
    InterpretRequest,
    interpret_question,
    validate_interpretation,
)


def _family_model(default: bool = False) -> GovernedModel:
    model = f0_model()
    metrics = tuple(
        m.model_copy(
            update={"family_id": "amount", "default_of_family": default and i == 0}
        )
        if i < 2
        else m
        for i, m in enumerate(model.metrics)
    )
    return model.model_copy(update={"metrics": metrics})


def _candidate(
    ids: tuple[str, ...] = ("confirmed_amount",),
    dimensions: tuple[str, ...] = ("currency_code",),
) -> InterpretationCandidate:
    return InterpretationCandidate(
        query=MetricQueryRequest(metric_ids=ids, dimensions=dimensions, all_time=True),
        clarification=None,
    )


def test_family_without_default_requires_explicit_choice() -> None:
    result = validate_interpretation(
        _family_model(),
        InterpretRequest(question="所有时间的金额，按币种"),
        _candidate(),
    )
    assert result.status == "clarification"
    assert {option.metric_id for option in result.options} == {
        "confirmed_amount",
        "cancelled_amount",
    }
    assert result.plan is None


def test_explicit_variant_produces_the_shared_parameterized_plan() -> None:
    result = validate_interpretation(
        _family_model(),
        InterpretRequest(
            question="所有时间的金额，按币种", selected_metric_ids=("confirmed_amount",)
        ),
        _candidate(),
    )
    assert result.status == "ready"
    assert result.plan is not None
    assert "CONFIRMED" in result.plan.params.values()
    assert "CONFIRMED" not in result.plan.sql


def test_unique_family_default_is_allowed() -> None:
    result = validate_interpretation(
        _family_model(default=True),
        InterpretRequest(question="所有时间金额，按币种"),
        _candidate(),
    )
    assert result.status == "ready"


def test_nondefault_variant_is_not_silently_selected() -> None:
    result = validate_interpretation(
        _family_model(default=True),
        InterpretRequest(question="金额"),
        _candidate(("cancelled_amount",)),
    )
    assert result.status == "clarification"


def test_llm_cannot_drop_or_replace_explicit_metrics() -> None:
    result = validate_interpretation(
        f0_model(),
        InterpretRequest(
            question="订单金额和订单数",
            selected_metric_ids=("confirmed_amount", "confirmed_order_count"),
        ),
        _candidate(),
    )
    assert result.code == "metric_selection_mismatch"
    assert result.plan is None


def test_unknown_selection_is_rejected() -> None:
    result = validate_interpretation(
        f0_model(),
        InterpretRequest(question="paid", selected_metric_ids=("paid",)),
        _candidate(),
    )
    assert result.code == "unknown_metric_selection"


def test_generated_cross_currency_total_is_rejected_by_real_compiler() -> None:
    result = validate_interpretation(
        f0_model(),
        InterpretRequest(question="Sum all currencies"),
        _candidate(dimensions=()),
    )
    assert result.status == "rejected"
    assert result.plan is None


def test_clarification_never_contains_an_executable_plan() -> None:
    candidate = _candidate().model_copy(update={"clarification": "请提供日期范围"})
    result = validate_interpretation(
        f0_model(), InterpretRequest(question="金额"), candidate
    )
    assert result.status == "clarification"
    assert result.query is None and result.plan is None


def test_llm_unavailable_returns_error_without_free_sql_fallback() -> None:
    with patch(
        "auto_ontology.server.metrics.interpretation.get_llm_client",
        side_effect=EnvironmentError("credential"),
    ):
        result = interpret_question(f0_model(), InterpretRequest(question="金额"))
    assert result.code == "interpretation_unavailable"
    assert result.plan is None
    assert "credential" not in result.message


def test_generated_sql_field_is_not_an_accepted_escape_hatch() -> None:
    with pytest.raises(ValidationError):
        InterpretationCandidate.model_validate(
            {"query": None, "clarification": None, "sql": "SELECT * FROM secret"}
        )
