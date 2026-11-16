# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prompt minimization and candidate discovery preserve the full compiler boundary."""

import json
from unittest.mock import Mock

from auto_ontology.semantic.contracts.models import MetricQueryRequest
from auto_ontology.semantic.contracts.tests.fixtures import f0_model
from auto_ontology.server.metrics import interpretation as module


def large_model():
    model = f0_model()
    template = next(
        metric
        for metric in model.metrics
        if metric.metric_id == "confirmed_order_count"
    )
    return model.model_copy(
        update={
            "metrics": model.metrics
            + tuple(
                template.model_copy(
                    update={
                        "metric_id": f"unrelated_{index}",
                        "name": f"Unrelated {index}",
                        "evidence": (
                            "UNRELATED_PRIVATE_EVIDENCE_" + str(index) + "x" * 1000,
                        ),
                    }
                )
                for index in range(40)
            )
        }
    )


def test_explicit_metric_excludes_unrelated_definitions_evidence_and_physical_identifiers(
    monkeypatch,
):
    model = large_model()
    query = MetricQueryRequest(metric_ids=("confirmed_order_count",), all_time=True)
    invoke = Mock(return_value=module.InterpretationCandidate(query=query))
    monkeypatch.setattr(module, "invoke_with_structured_output", invoke)
    result = module.interpret_question(
        model,
        module.InterpretRequest(
            question="所有时间确认订单数", selected_metric_ids=query.metric_ids
        ),
        llm=Mock(),
    )
    assert result.status == "ready"
    prompt = invoke.call_args.args[1][1].content
    assert "UNRELATED_PRIVATE_EVIDENCE" not in prompt
    assert "unrelated_" not in prompt
    assert (
        "catalog_column_id" not in prompt
        and "structure_fingerprint" not in prompt
        and "table_name" not in prompt
    )
    assert [
        metric["metric_id"] for metric in json.loads(prompt)["model"]["metrics"]
    ] == ["confirmed_order_count"]
    assert len(prompt) < len(model.model_dump_json()) / 2
    assert result.plan.model_fingerprint == model.fingerprint()


def test_large_catalog_loads_only_retrieved_candidates_and_rejects_out_of_scope_output(
    monkeypatch,
):
    model = large_model()
    invoke = Mock(
        side_effect=[
            module.MetricCandidates(metric_ids=("confirmed_order_count",)),
            module.InterpretationCandidate(
                query=MetricQueryRequest(metric_ids=("unrelated_1",), all_time=True)
            ),
        ]
    )
    monkeypatch.setattr(module, "invoke_with_structured_output", invoke)
    result = module.interpret_question(
        model, module.InterpretRequest(question="所有时间确认订单数"), llm=Mock()
    )
    assert result.code == "unretrieved_metric" and result.plan is None
    inventory = invoke.call_args_list[0].args[1][1].content
    detail = invoke.call_args_list[1].args[1][1].content
    assert "UNRELATED_PRIVATE_EVIDENCE" not in inventory + detail
    assert "unrelated_1" in inventory and "unrelated_1" not in detail


def test_candidate_discovery_error_never_authorizes_free_sql(monkeypatch):
    invoke = Mock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(module, "invoke_with_structured_output", invoke)
    result = module.interpret_question(
        large_model(), module.InterpretRequest(question="未知指标"), llm=Mock()
    )
    assert (
        result.status == "rejected" and result.code == "candidate_retrieval_unavailable"
    )
    assert result.query is None and result.plan is None


def test_candidate_retrieval_retains_family_ambiguity(monkeypatch):
    model = large_model()
    model = model.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(update={"family_id": "amount"})
                if metric.metric_id in {"confirmed_amount", "cancelled_amount"}
                else metric
                for metric in model.metrics
            )
        }
    )
    invoke = Mock(
        side_effect=[
            module.MetricCandidates(metric_ids=("confirmed_amount",)),
            module.InterpretationCandidate(
                query=MetricQueryRequest(
                    metric_ids=("confirmed_amount",),
                    dimensions=("currency_code",),
                    all_time=True,
                )
            ),
        ]
    )
    monkeypatch.setattr(module, "invoke_with_structured_output", invoke)
    result = module.interpret_question(
        model, module.InterpretRequest(question="所有时间金额"), llm=Mock()
    )
    assert result.status == "clarification" and result.code == "ambiguous_metric_family"
    assert {option.metric_id for option in result.options} == {
        "confirmed_amount",
        "cancelled_amount",
    }
    assert result.plan is None
