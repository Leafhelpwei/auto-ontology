# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Incomplete history and legal-but-wrong LLM proposals cannot silently execute."""

from unittest.mock import Mock

import pytest

from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import MetricQueryRequest
from auto_ontology.semantic.contracts.tests.fixtures import f0_model
from auto_ontology.server.chat import metrics_flow
from auto_ontology.server.chat.conversation_dal import _rows_to_history
from auto_ontology.server.metrics.interpretation import (
    InterpretRequest,
    InterpretationResult,
    interpret_question,
)


@pytest.mark.parametrize("kind", ["six_turns", "cumulative_chars", "long_answer"])
def test_unresolved_constraints_are_not_lost_when_history_is_bounded(kind, monkeypatch):
    model = f0_model()
    rows = []
    for index in range(
        6 if kind == "six_turns" else 3 if kind == "cumulative_chars" else 1
    ):
        question = (
            "排除客户 Alice，只看 CNY，日期待确定" if index == 0 else "再补充条件"
        )
        response = "请给出时区" + "x" * (
            4500 if kind == "cumulative_chars" else 9000 if kind == "long_answer" else 0
        )
        rows.extend(
            [
                {
                    "role": "user",
                    "content": question,
                    "sql_code": None,
                    "sql_response": None,
                },
                {
                    "role": "assistant",
                    "content": response,
                    "sql_code": None,
                    "sql_response": None,
                    "query_context": {
                        "query_mode": "metrics",
                        "model_id": model.model_id,
                        "revision": 1,
                        "model_fingerprint": model.fingerprint(),
                        "status": "clarification",
                    },
                },
            ]
        )
    history = [vars(turn) for turn in _rows_to_history(rows, include_incomplete=True)]
    llm = Mock()
    monkeypatch.setattr(
        "auto_ontology.server.metrics.interpretation.invoke_with_structured_output", llm
    )
    try:
        context = metrics_flow._interpretation_context(
            model.model_id, 1, model.fingerprint(), history
        )
    except Exception as error:
        assert error.code == "CONTEXT_TOO_LARGE"
    else:
        result = interpret_question(
            model,
            InterpretRequest(question="Asia/Shanghai"),
            revision=1,
            context=context,
            llm=Mock(),
        )
        assert result.code == "CONTEXT_TOO_LARGE"
    llm.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "昨天上海时区已支付金额，不要所有历史",
        "查询支付订单数，不是确认订单数",
        "全部时间确认订单数，但必须排除客户 Alice",
    ],
)
def test_legal_wrong_candidate_requires_review_and_exact_current_confirmation(
    question, monkeypatch
):
    model = f0_model()
    query = MetricQueryRequest(metric_ids=("confirmed_order_count",), all_time=True)
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    monkeypatch.setattr(
        metrics_flow.service,
        "executable_model",
        lambda *a, **k: (model, {}, {"revision": 1}, None),
    )
    interpreter = Mock(
        return_value=InterpretationResult(
            status="ready", query=query, plan=compile_query(model, query)
        )
    )
    executor = Mock()
    monkeypatch.setattr(metrics_flow, "interpret_question", interpreter)
    monkeypatch.setattr(metrics_flow.service, "execute", executor)
    payload = {
        "question": question,
        "user_id": "u",
        "metric_context": {"model_id": model.model_id},
    }
    answer = list(metrics_flow.stream_metric_response(payload))[-1]["answer"]
    context = answer["query_context"]
    assert context["status"] == "confirmation_required"
    assert context["pending_query"]["all_time"] is True
    assert context["pending_query"]["filters"] == []
    assert context["intent_confirmed"] is False and context["trusted"] is False
    executor.assert_not_called()
    for mutation in ("token", "scope", "replay", "selection"):
        latest = dict(context)
        token = context["confirmation_fingerprint"]
        if mutation == "token":
            token = "0" * 64
        elif mutation == "scope":
            latest["model_fingerprint"] = "changed"
        elif mutation == "replay":
            latest["status"] = "ready"
        selection = {"model_id": model.model_id, "confirm_query_fingerprint": token}
        if mutation == "selection":
            selection["selected_metric_ids"] = []
        events = list(
            metrics_flow.stream_metric_response(
                {
                    **payload,
                    "metric_context": selection,
                    "conversation_history": [
                        {
                            "question": question,
                            "response": answer["response"],
                            "query_context": latest,
                        }
                    ],
                }
            )
        )
        assert events[-1]["answer"]["query_context"]["status"] == "error"
    assert interpreter.call_count == 1
    executor.assert_not_called()


def test_previous_success_does_not_summarize_later_unresolved_exclusions(monkeypatch):
    # The first later clarification still matters even when a base query exists.
    model = f0_model()
    query = MetricQueryRequest(metric_ids=("confirmed_order_count",), all_time=True)
    rows = []
    for i in range(6):
        context = {
            "query_mode": "metrics",
            "model_id": model.model_id,
            "revision": 1,
            "model_fingerprint": model.fingerprint(),
            "status": "clarification",
            "base_query": query.model_dump(mode="json"),
        }
        rows.extend(
            [
                {
                    "role": "user",
                    "content": "排除 Alice" if i == 0 else "稍后补充",
                    "sql_code": None,
                    "sql_response": None,
                },
                {
                    "role": "assistant",
                    "content": "哪个时区？",
                    "sql_code": None,
                    "sql_response": None,
                    "query_context": context,
                },
            ]
        )
    history = _rows_to_history(rows, include_incomplete=True)
    assert history[-1].query_context["context_too_large"] is True


def test_explicit_restatement_creates_a_persistent_context_boundary(monkeypatch):
    model = f0_model()
    old = {
        "question": "Old unretained constraints",
        "response": "Restate",
        "query_context": {
            "query_mode": "metrics",
            "model_id": model.model_id,
            "revision": 1,
            "model_fingerprint": model.fingerprint(),
            "context_too_large": True,
        },
    }
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    monkeypatch.setattr(
        metrics_flow.service,
        "executable_model",
        lambda *a, **k: (model, {}, {"revision": 1}, None),
    )
    interpreter = Mock(
        return_value=InterpretationResult(status="clarification", message="Timezone?")
    )
    monkeypatch.setattr(metrics_flow, "interpret_question", interpreter)
    answer = list(
        metrics_flow.stream_metric_response(
            {
                "question": "Complete new question",
                "user_id": "u",
                "conversation_history": [old],
                "metric_context": {"model_id": model.model_id, "reset_context": True},
            }
        )
    )[-1]["answer"]
    assert interpreter.call_args.kwargs["context"] is None
    assert answer["query_context"]["context_reset"] is True
    latest = {
        "question": "Complete new question",
        "response": answer["response"],
        "query_context": answer["query_context"],
    }
    assert metrics_flow._prior_segment([old, latest], model.model_id, 1) == [latest]
