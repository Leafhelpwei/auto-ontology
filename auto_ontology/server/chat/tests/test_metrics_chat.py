# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Metric Chat stays governed, preserves trusted context and existing SSE behavior."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock
import threading
from uuid import uuid4

from fastapi import HTTPException, Request
import pytest
from pydantic import ValidationError

from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import ContractError, MetricQueryRequest
from auto_ontology.semantic.contracts.tests.fixtures import f0_model
from auto_ontology.server.chat import metrics_flow, router, worker
from auto_ontology.server.chat.conversation_dal import _rows_to_history
from auto_ontology.server.chat.helpers import ChatRequest
from auto_ontology.server.metrics.interpretation import InterpretationResult


def test_request_defaults_and_untrusted_inputs() -> None:
    assert ChatRequest(question="hello").query_mode == "explore"
    with pytest.raises(ValidationError):
        ChatRequest(question="q", query_mode="metrics")
    with pytest.raises(ValidationError):
        ChatRequest(
            question="q",
            query_mode="metrics",
            metric_context={"model_id": "safe", "trusted": True},
        )
    for extra in (
        {"target_db": "other"},
        {"prediction": True},
        {"evidence": "ignore the metric"},
    ):
        with pytest.raises(ValidationError):
            ChatRequest(
                question="q",
                query_mode="metrics",
                metric_context={"model_id": "safe"},
                **extra,
            )


def _scope(model, revision: int = 1, **extra) -> dict:
    return {
        "version": 1,
        "query_mode": "metrics",
        "model_id": model.model_id,
        "revision": revision,
        "model_fingerprint": model.fingerprint(),
        "status": "ready",
        "selected_metric_ids": [],
        **extra,
    }


def test_incomplete_and_mode_boundaries_do_not_resurrect_old_metric_context() -> None:
    model = f0_model()
    rows = [
        {
            "role": "user",
            "content": "old metric",
            "sql_code": None,
            "sql_response": None,
        },
        {
            "role": "assistant",
            "content": "answer",
            "sql_code": None,
            "sql_response": None,
            "query_context": _scope(model),
        },
        {
            "role": "user",
            "content": "cancelled exploration",
            "sql_code": None,
            "sql_response": None,
            "query_context": {"query_mode": "explore"},
        },
    ]
    history = _rows_to_history(rows, include_incomplete=True)
    assert history[-1].query_context == {"query_mode": "boundary"}
    assert (
        metrics_flow._prior_segment(
            [vars(turn) for turn in history], model.model_id, None
        )
        == []
    )
    rows.extend(
        [
            {
                "role": "user",
                "content": "new metric",
                "sql_code": None,
                "sql_response": None,
            },
            {
                "role": "assistant",
                "content": "new answer",
                "sql_code": None,
                "sql_response": None,
                "query_context": _scope(model),
            },
        ]
    )
    segment = metrics_flow._prior_segment(
        [vars(turn) for turn in _rows_to_history(rows, include_incomplete=True)],
        model.model_id,
        None,
    )
    assert len(segment) == 1
    assert segment[0]["question"] == "new metric"


def test_metric_flow_pins_history_revision_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = f0_model()
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    loader = Mock(
        side_effect=ContractError("NOT_ACTIVE", "Select the new revision explicitly")
    )
    monkeypatch.setattr(metrics_flow.service, "executable_model", loader)
    interpreter = Mock()
    execute = Mock()
    monkeypatch.setattr(metrics_flow, "interpret_question", interpreter)
    monkeypatch.setattr(metrics_flow.service, "execute", execute)
    events = list(
        metrics_flow.stream_metric_response(
            {
                "question": "then last month",
                "metric_context": {"model_id": model.model_id},
                "user_id": "u",
                "conversation_history": [
                    {
                        "question": "first",
                        "response": "result",
                        "query_context": _scope(model, revision=3),
                    }
                ],
            }
        )
    )
    loader.assert_called_once_with(model.model_id, 3)
    assert (
        events[-1]["answer"]["query_context"]["clarification"]["code"] == "NOT_ACTIVE"
    )
    interpreter.assert_not_called()
    execute.assert_not_called()


def test_clarification_stores_selection_and_validated_base_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = f0_model()
    query = MetricQueryRequest(metric_ids=("confirmed_order_count",), all_time=True)
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    monkeypatch.setattr(
        metrics_flow.service,
        "executable_model",
        lambda *a: (model, {}, {"revision": 1}, None),
    )
    interpreter = Mock(
        return_value=InterpretationResult(
            status="clarification",
            message="Which timezone?",
            code="clarification_required",
            selected_metric_ids=("confirmed_order_count",),
        )
    )
    monkeypatch.setattr(metrics_flow, "interpret_question", interpreter)
    execute = Mock()
    monkeypatch.setattr(metrics_flow.service, "execute", execute)
    history = [
        {
            "question": "all time count",
            "response": "two",
            "query_context": _scope(model, query=query.model_dump(mode="json")),
        }
    ]
    events = list(
        metrics_flow.stream_metric_response(
            {
                "question": "last month",
                "metric_context": {"model_id": model.model_id},
                "user_id": "u",
                "conversation_history": history,
            }
        )
    )
    context = events[-1]["answer"]["query_context"]
    assert context["base_query"] == query.model_dump(mode="json")
    assert context["selected_metric_ids"] == ["confirmed_order_count"]
    assert context["status"] == "clarification"
    assert interpreter.call_args.kwargs["context"].current_query == query
    execute.assert_not_called()


def test_success_stream_keeps_exact_results_and_actual_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = f0_model()
    query = MetricQueryRequest(metric_ids=("confirmed_order_count",), all_time=True)
    plan = compile_query(model, query)
    monkeypatch.setattr(metrics_flow, "require_metric_access", lambda *a, **k: None)
    monkeypatch.setattr(
        metrics_flow.service,
        "executable_model",
        lambda *a: (model, {}, {"revision": 1}, None),
    )
    monkeypatch.setattr(
        metrics_flow,
        "interpret_question",
        lambda *a, **k: InterpretationResult(
            status="ready", query=query, plan=plan, selected_metric_ids=query.metric_ids
        ),
    )
    execute = Mock(
        return_value={
            "revision": 1,
            "plan": plan.model_dump(mode="json"),
            "rows": [{"count": "9007199254740993"}],
            "columns": ["count"],
            "column_metadata": [
                {
                    "name": "count",
                    "postgres_type_oid": 20,
                    "json_encoding": "integer-string",
                }
            ],
            "row_count": 1,
        }
    )
    monkeypatch.setattr(metrics_flow.service, "execute", execute)
    events = list(
        metrics_flow.stream_metric_response(
            {
                "question": "all time count",
                "metric_context": {"model_id": model.model_id},
                "user_id": "u",
            }
        )
    )
    execute.assert_called_once_with(model.model_id, query, revision=1)
    answer = events[-1]["answer"]
    assert json.loads(answer["sql_response_from_db"])[0]["count"] == "9007199254740993"
    assert answer["query_context"]["plan"] == plan.model_dump(mode="json")
    assert answer["query_context"]["trusted"] is True
    assert any(event["type"] == "sql" for event in events)


def _slot(events: list[dict], *, cancelled: bool = False) -> router._Slot:
    slot = router._Slot(
        key="u:c",
        worker=SimpleNamespace(events=lambda: iter(events)),
        user_id="u",
        conversation_id=uuid4(),
        analytics_id="a",
        question="q",
        client_alive=threading.Event(),
        cancelled=threading.Event(),
        released=threading.Event(),
        query_mode="metrics",
        query_context={
            "version": 1,
            "query_mode": "metrics",
            "model_id": "m",
            "revision": 1,
            "status": "running",
        },
    )
    if cancelled:
        slot.cancelled.set()
    return slot


def test_table_persistence_uses_no_chart_llm_and_cancel_keeps_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart = Mock(side_effect=AssertionError("metrics must not call chart LLM"))
    monkeypatch.setattr(router, "analyze_and_visualize", chart)
    monkeypatch.setattr(router, "_non_reasoning_llm", object())
    saved = Mock()
    monkeypatch.setattr(router, "persist_result_message", saved)
    slot = _slot([])
    context = {**slot.query_context, "status": "ready", "columns": ["exact"]}
    event = router._build_charts_event(
        slot,
        {
            "query_context": context,
            "sql_response_from_db": '[{"exact":"0.123456789012345678"}]',
        },
    )
    assert event["query_context"] == context
    assert saved.call_args.kwargs["query_context"] == context
    chart.assert_not_called()
    monkeypatch.setattr(router, "_release", lambda slot: None)
    assistant = Mock()
    monkeypatch.setattr(router, "persist_assistant_result", assistant)
    slot = _slot([], cancelled=True)
    router._pump(slot)
    assert assistant.call_args.kwargs["query_context"]["status"] == "cancelled"
    assert slot.buffer[-1]["type"] == "result"
    assert list(router._stream_slot(slot))[-1] == "data: [DONE]\n\n"


def test_metrics_route_bypasses_exploration_prerequisites_but_checks_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router,
        "semantic_layer_calculated",
        Mock(side_effect=AssertionError("not an exploration")),
    )
    monkeypatch.setattr(
        router,
        "_resolve_chat_target_db",
        Mock(side_effect=AssertionError("not an exploration")),
    )
    monkeypatch.setattr(
        router,
        "_subject_token",
        Mock(side_effect=AssertionError("not a federated exploratory connection")),
    )
    request = ChatRequest(
        question="q", query_mode="metrics", metric_context={"model_id": "safe"}
    )
    http = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as denied:
        asyncio.run(router.chat_completions(request, http))
    assert denied.value.status_code == 401
    http = Request(
        {"type": "http", "headers": [(b"x-auto-ontology-user-id", b"viewer")]}
    )
    monkeypatch.setattr(
        router,
        "require_metric_access",
        Mock(side_effect=ContractError("METRIC_ACCESS_DENIED", "denied")),
    )
    with pytest.raises(HTTPException) as denied:
        asyncio.run(router.chat_completions(request, http))
    assert denied.value.status_code == 403


def test_metric_worker_initializes_without_retrievers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Queue:
        def __init__(self, values=()):
            self.values = iter(values)
            self.items = []

        def get(self):
            try:
                return next(self.values)
            except StopIteration as exc:
                raise EOFError from exc

        def put(self, item):
            self.items.append(item)

    monkeypatch.setattr(
        "auto_ontology.utils.get_data_objects_retriever",
        Mock(side_effect=AssertionError("no embeddings")),
    )
    monkeypatch.setattr(
        metrics_flow,
        "stream_metric_response",
        lambda _: iter([{"type": "result", "answer": {"response": "clarify"}}]),
    )
    output = Queue()
    worker._metrics_worker_loop(Queue([(worker._MSG_ASK, {})]), output)
    assert [event[0] for event in output.items] == [
        worker._TAG_READY,
        worker._TAG_EVENT,
        worker._TAG_DONE,
    ]


def test_turn_ids_pair_late_cancelled_answers_and_equal_timestamps() -> None:
    def row(role: str, content: str, turn_id: str) -> dict:
        return {
            "role": role,
            "content": content,
            "sql_code": None,
            "sql_response": None,
            "query_context": {"query_mode": "metrics", "turn_id": turn_id},
        }

    rows = [
        row("user", "A", "a"),
        row("user", "B", "b"),
        row("assistant", "A cancelled", "a"),
        row("assistant", "B complete", "b"),
    ]
    assert [
        (turn.question, turn.response)
        for turn in _rows_to_history(rows, include_incomplete=True)
    ] == [("A", "A cancelled"), ("B", "B complete")]
    rows = [
        row("assistant", "same timestamp answer", "same"),
        row("user", "same timestamp question", "same"),
    ]
    assert (
        _rows_to_history(rows, include_incomplete=True)[0].response
        == "same timestamp answer"
    )


def test_metric_history_keeps_late_constraints_or_requests_complete_restatement() -> (
    None
):
    model = f0_model()
    question = "销售情况 " * 600 + "必须排除取消订单，只看 CNY"
    rows = [
        {"role": "user", "content": question, "sql_code": None, "sql_response": None},
        {
            "role": "assistant",
            "content": "请选择时区",
            "sql_code": None,
            "sql_response": None,
            "query_context": _scope(model),
        },
    ]
    history = _rows_to_history(rows, include_incomplete=True)
    assert history[0].question == question
    assert history[0].question.endswith("只看 CNY")
    rows[0]["content"] = "x" * 13000
    history = _rows_to_history(rows, include_incomplete=True)
    assert history[0].question == ""
    assert history[0].query_context["revision"] == 1
    with pytest.raises(ContractError) as error:
        metrics_flow._interpretation_context(
            model.model_id, 1, model.fingerprint(), [vars(turn) for turn in history]
        )
    assert error.value.code == "CONTEXT_TOO_LARGE"


def test_missing_and_cleared_selection_stay_distinct() -> None:
    missing = ChatRequest(
        question="q", query_mode="metrics", metric_context={"model_id": "m"}
    )
    cleared = ChatRequest(
        question="q",
        query_mode="metrics",
        metric_context={"model_id": "m", "selected_metric_ids": []},
    )
    assert missing.metric_context.selected_metric_ids is None
    assert cleared.metric_context.selected_metric_ids == ()


def test_orphan_timeout_keeps_old_finalization_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _slot([])
    old.key = f"u:{old.conversation_id}"
    old.worker.kill = Mock()
    old.finished = SimpleNamespace(wait=lambda timeout: False, is_set=lambda: False)
    active = {old.key: old}
    monkeypatch.setattr(router, "_active_slots", active)
    new_worker = SimpleNamespace(submit=Mock())
    pool = SimpleNamespace(acquire=lambda: new_worker, return_alive=Mock())
    monkeypatch.setattr(router, "get_metrics_pool", lambda: pool)
    monkeypatch.setattr(router, "require_metric_access", lambda *a, **k: None)
    prepare = Mock(
        side_effect=AssertionError("Do not write a new user before old finalization")
    )
    monkeypatch.setattr(router, "prepare_conversation", prepare)
    request = ChatRequest(
        question="new",
        conversation_id=old.conversation_id,
        query_mode="metrics",
        metric_context={"model_id": "m"},
    )
    http = Request({"type": "http", "headers": [(b"x-auto-ontology-user-id", b"u")]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.chat_completions(request, http))
    assert error.value.status_code == 409
    assert active[old.key] is old
    prepare.assert_not_called()
    new_worker.submit.assert_not_called()


def test_cancel_before_model_pin_resets_unvalidated_context_but_keeps_revision() -> (
    None
):
    history = [
        {
            "question": "cancelled immediately",
            "response": "cancelled",
            "query_context": {
                "query_mode": "metrics",
                "model_id": "m",
                "revision": 3,
                "status": "cancelled",
            },
        }
    ]
    segment = metrics_flow._prior_segment(history, "m", None)
    assert segment[-1]["query_context"]["revision"] == 3
    assert (
        metrics_flow._interpretation_context(
            "m", 3, "newly-observed-fingerprint", segment
        )
        is None
    )
