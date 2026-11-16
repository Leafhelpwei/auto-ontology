# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Governed Chat orchestration, isolated from exploratory SQL and retrievers."""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
from typing import Any

import psycopg
import mysql.connector

from auto_ontology.connectors.base import StatementTimeout
from auto_ontology.dal.governed_models import ModelNotFound
from auto_ontology.semantic.contracts.models import (
    ContractError,
    MetricQueryRequest,
    content_fingerprint,
)
from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.server.metrics import service
from auto_ontology.server.metrics.interpretation import (
    InterpretRequest,
    InterpretationResult,
    interpret_question,
)
from auto_ontology.server.users.permissions import permissions_for_role
from auto_ontology.server.users.postgres_dal import get_user_role

logger = logging.getLogger(__name__)


def require_metric_access(user_id: str | None, *, persistent: bool = False) -> None:
    """Recheck the gateway identity using current server-side role permissions."""
    required = {"catalog:read", "chat:use"}
    if persistent:
        required.add("conversation:write")
    actual = (
        set(permissions_for_role(get_user_role(user_id) or "")) if user_id else set()
    )
    if not required <= actual:
        raise ContractError(
            "METRIC_ACCESS_DENIED",
            "Authenticated chat and catalog read permissions are required",
        )


def _prior_segment(
    history: list[dict[str, Any]], model_id: str, revision: int | None
) -> list[dict[str, Any]]:
    """Only a contiguous segment in the current explicitly selected scope applies."""
    selected: list[dict[str, Any]] = []
    pinned = revision
    for turn in reversed(history):
        context = turn.get("query_context") or {}
        if (
            context.get("query_mode") != "metrics"
            or context.get("model_id") != model_id
        ):
            break
        current = context.get("revision")
        if not isinstance(current, int):
            break
        if pinned is None:
            pinned = current
        if current != pinned:
            break
        selected.append(turn)
        if context.get("context_reset"):
            break
    return list(reversed(selected))


def _interpretation_context(
    model_id: str, revision: int, fingerprint: str, history: list[dict[str, Any]]
) -> Any:
    from auto_ontology.server.metrics.interpretation import (
        InterpretationContext,
        InterpretationTurn,
        MetricOption,
    )

    if not history:
        return None
    latest = history[-1].get("query_context") or {}
    if latest.get("context_too_large"):
        raise ContractError(
            "CONTEXT_TOO_LARGE",
            "The preceding clarification is too long to retain safely; restate the complete question and its constraints",
        )
    if not latest.get("model_fingerprint") and latest.get("status") in {
        "error",
        "cancelled",
    }:
        # An immediate cancel/disabled-feature failure can precede model pinning.
        # There is no validated interpretation to inherit. The caller has still
        # checked the retained revision against the active certified model.
        return None
    if latest.get("model_fingerprint") != fingerprint:
        raise ContractError(
            "CONTEXT_REVISION_MISMATCH",
            "The prior question used a different certified model fingerprint; explicitly select the current revision to restart",
        )
    raw_query = (
        latest.get("pending_query") or latest.get("query") or latest.get("base_query")
    )
    query = MetricQueryRequest.model_validate(raw_query) if raw_query else None
    turns = []
    for turn in history:
        context = turn.get("query_context") or {}
        if context.get("model_fingerprint") != fingerprint:
            continue
        turns.append(InterpretationTurn(role="user", content=turn["question"]))
        turns.append(InterpretationTurn(role="assistant", content=turn["response"]))
    return InterpretationContext(
        model_id=model_id,
        revision=revision,
        model_fingerprint=fingerprint,
        turns=tuple(turns),
        current_query=query,
        selected_metric_ids=tuple(latest.get("selected_metric_ids") or ()),
        pending_options=tuple(
            MetricOption.model_validate(option)
            for option in (latest.get("clarification") or {}).get("options", ())
        ),
    )


def _answer(
    context: dict[str, Any],
    response: str,
    *,
    sql: str | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    answer: dict[str, Any] = {"response": response, "query_context": dict(context)}
    if sql is not None:
        answer["sql_code"] = sql
    if rows is not None:
        answer["sql_response_from_db"] = json.dumps(
            rows, ensure_ascii=False, allow_nan=False
        )
    return {"type": "result", "answer": answer}


def _confirmed_request(
    model: Any,
    latest: dict[str, Any] | None,
    token: str,
    selected: tuple[str, ...] | None,
) -> InterpretationResult:
    """Confirm only the latest owned, pinned proposal; never parse approval from prose."""
    if (
        not latest
        or latest.get("status") != "confirmation_required"
        or not latest.get("pending_query")
    ):
        raise ContractError(
            "CONFIRMATION_EXPIRED",
            "No pending query is available; ask the complete question again.",
        )
    query = MetricQueryRequest.model_validate(latest["pending_query"])
    if token != latest.get("confirmation_fingerprint") or token != content_fingerprint(
        query
    ):
        raise ContractError(
            "CONFIRMATION_MISMATCH",
            "The query changed; review the latest proposal before confirming.",
        )
    if selected is not None and set(selected) != set(query.metric_ids):
        raise ContractError(
            "CONFIRMATION_MISMATCH",
            "The selected metrics differ from the query being confirmed.",
        )
    return InterpretationResult(
        status="ready",
        query=query,
        plan=compile_query(model, query),
        selected_metric_ids=tuple(latest.get("selected_metric_ids") or ()),
    )


def stream_metric_response(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Resolve/clarify a question and execute only a pinned certified request."""
    selection = payload["metric_context"]
    model_id = selection["model_id"]
    raw_selected = selection.get("selected_metric_ids")
    selected = tuple(raw_selected) if raw_selected is not None else None
    history = _prior_segment(
        []
        if selection.get("reset_context")
        else payload.get("conversation_history") or [],
        model_id,
        selection.get("revision"),
    )
    latest = history[-1].get("query_context") if history else None
    revision = selection.get("revision") or (latest or {}).get("revision")
    context: dict[str, Any] = {
        "version": 1,
        "query_mode": "metrics",
        "turn_id": payload.get("turn_id"),
        "model_id": model_id,
        "revision": revision,
        "selected_metric_ids": list(selected or ()),
        "status": "running",
        "trusted": False,
        "context_reset": bool(selection.get("reset_context")),
    }
    if latest:
        for key in ("model_fingerprint", "base_query"):
            if latest.get(key) is not None:
                context[key] = latest[key]
        if latest.get("pending_query") or latest.get("query"):
            context["base_query"] = latest.get("pending_query") or latest["query"]
    try:
        require_metric_access(
            payload.get("user_id"), persistent=bool(payload.get("persistent"))
        )
        model, _, row, _ = service.executable_model(
            model_id, revision, metadata_only=True
        )
        revision = row["revision"]
        context.update(revision=revision, model_fingerprint=model.fingerprint())
        prior = _interpretation_context(
            model_id, revision, model.fingerprint(), history
        )
        yield {"type": "metric_context", "query_context": dict(context)}
        yield {"type": "step", "node": "metrics_interpret", "label": "解析指标口径"}
        confirmation = selection.get("confirm_query_fingerprint")
        interpreted = (
            _confirmed_request(model, latest, confirmation, selected)
            if confirmation
            else interpret_question(
                model,
                InterpretRequest(
                    question=payload["question"], selected_metric_ids=selected
                ),
                revision=revision,
                context=prior,
            )
        )
        context["selected_metric_ids"] = list(interpreted.selected_metric_ids)
        if interpreted.status != "ready":
            context.update(
                status=interpreted.status,
                clarification={
                    "code": interpreted.code,
                    "message": interpreted.message,
                    "options": [
                        option.model_dump(mode="json") for option in interpreted.options
                    ],
                },
            )
            yield _answer(context, interpreted.message)
            return
        if interpreted.query is None or interpreted.plan is None:
            raise ContractError(
                "INVALID_INTERPRETATION",
                "The interpreter did not return a validated metric request",
            )
        if not confirmation:
            context.update(
                status="confirmation_required",
                pending_query=interpreted.query.model_dump(mode="json"),
                confirmation_fingerprint=content_fingerprint(interpreted.query),
                intent_confirmed=False,
                metric_names={
                    metric.metric_id: metric.name
                    for metric in model.metrics
                    if metric.metric_id in interpreted.query.metric_ids
                },
            )
            yield _answer(
                context,
                "请核对下面的指标、时间、币种和筛选范围，确认后执行；如有偏差，请直接补充或修改条件。",
            )
            return
        context.update(
            query=interpreted.query.model_dump(mode="json"),
            plan=interpreted.plan.model_dump(mode="json"),
            intent_confirmed=True,
        )
        context.pop("base_query", None)
        yield {"type": "metric_context", "query_context": dict(context)}
        yield {"type": "sql", "node": "metrics_execute", "sql": interpreted.plan.sql}
        yield {"type": "step", "node": "metrics_execute", "label": "执行认证指标"}
        # The endpoint rechecks active revision, certification, current structure,
        # unique keys and source snapshot; no LLM SQL or client plan can execute.
        require_metric_access(
            payload.get("user_id"), persistent=bool(payload.get("persistent"))
        )
        result = service.execute(model_id, interpreted.query, revision=revision)
        context.update(
            status="ready",
            trusted=True,
            plan=result["plan"],
            columns=result["columns"],
            column_metadata=result["column_metadata"],
        )
        names = [
            metric.name
            for metric in model.metrics
            if metric.metric_id in interpreted.query.metric_ids
        ]
        response = f"已按认证模型「{model.name}」第 {revision} 版执行指标「{'、'.join(names)}」，返回 {result['row_count']} 行。口径、时间范围、维度和参数见查询依据；数值按数据库原始精度展示。"
        yield _answer(context, response, sql=result["plan"]["sql"], rows=result["rows"])
    except ContractError as exc:
        context.update(
            status="error",
            trusted=False,
            clarification={"code": exc.code, "message": exc.message, "options": []},
        )
        yield _answer(context, exc.message)
    except ModelNotFound:
        context.update(
            status="error",
            trusted=False,
            clarification={
                "code": "MODEL_NOT_FOUND",
                "message": "The selected metric model is unavailable",
                "options": [],
            },
        )
        yield _answer(context, "The selected metric model is unavailable")
    except (psycopg.Error, mysql.connector.Error, StatementTimeout) as exc:
        code = (
            "QUERY_TIMEOUT"
            if isinstance(exc, StatementTimeout)
            else "SOURCE_QUERY_FAILED"
        )
        message = "The governed query could not complete; no exploratory query was run"
        context.update(
            status="error",
            trusted=False,
            clarification={"code": code, "message": message, "options": []},
        )
        yield _answer(context, message)
    except Exception as exc:
        logger.error("Governed Chat failed error_class=%s", type(exc).__name__)
        message = "Governed metric interpretation or execution failed; no exploratory query was run"
        context.update(
            status="error",
            trusted=False,
            clarification={
                "code": "METRICS_UNAVAILABLE",
                "message": message,
                "options": [],
            },
        )
        yield _answer(context, message)
