# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat workers with PostgreSQL admission, leases and durable event replay.

All replicas share per-conversation exclusion and global/user quotas before
worker allocation. Server-issued owner tokens fence event/history writes;
cancel requests are observed by the owner heartbeat. Lost owners become
interrupted, never silently restart a database query. Disconnecting an SSE
observer does not stop its run. Explicit cancellation, deadlines and failed
ownership checks terminate the worker.

Live submissions and reconnects read the bounded database log. Last-Event-ID
pins replay to a retained run and sequence. The local slot map only locates
processes for prompt cancellation; it is not the authoritative run state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from contextlib import nullcontext
from auto_ontology.dal import chat_runs
from auto_ontology.server.chat.run_lifecycle import LeaseGuard, OWNER_ID
from typing import Any, Generator, Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import StreamingResponse

from auto_ontology.dal import datasources as datasources_dal
from auto_ontology.dal.terms import semantic_layer_calculated
from auto_ontology.retrieval.text_to_sql.visualization import analyze_and_visualize
from auto_ontology.server.chat.helpers import (
    NODE_LABELS,
    ChatRequest,
    build_result_message,
)
from auto_ontology.server.chat.conversation_dal import (
    ConversationAccessError,
    create_stateless_analytics,
    persist_analytics_result,
    persist_assistant_result,
    persist_result_message,
    prepare_conversation,
)
from auto_ontology.server.chat.settings_dal import is_visualization_enabled
from auto_ontology.server.chat.worker import (
    PrewarmedWorker,
    get_pool,
    get_metrics_pool,
)
from auto_ontology.server.chat.metrics_flow import require_metric_access
from auto_ontology.semantic.contracts.models import ContractError
from auto_ontology.server.identity import resolve_internal_user
from auto_ontology.utils.llm_invoke import get_llm_client, get_non_reasoning_llm_client
from auto_ontology.server.responses import ChatCancelResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class EventStreamResponse(StreamingResponse):
    """Declares the SSE media type so the spec doesn't also claim JSON."""

    media_type = "text/event-stream"


# The two streaming routes emit OpenAI-style SSE frames rather than a JSON
# body, so they document a media type instead of a response_model.
SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
        "description": "Server-sent events; the stream ends with `data: [DONE]`",
    }
}

# Built once at import time, same fallback order as the main agent pipeline
# (auto_ontology.retrieval.text_to_sql.main): prefer the cheaper non-reasoning model,
# fall back to the main reasoning model. Neither client does I/O at
# construction time, so this is safe to build eagerly; a missing API key
# just leaves the client as ``None`` and ``_build_charts_event`` soft-fails
# per run.
try:
    _non_reasoning_llm = get_non_reasoning_llm_client(max_tokens=2048)
except (ValueError, EnvironmentError) as e:
    logger.warning("Chart generation: failed to init non-reasoning LLM: %s", e)
    _non_reasoning_llm = None

try:
    _reasoning_llm = get_llm_client()
except (ValueError, EnvironmentError) as e:
    logger.warning("Chart generation: failed to init reasoning LLM: %s", e)
    _reasoning_llm = None

# Returned as the 409 detail when a chat is attempted before the semantic
# layer has been built.
_SEMANTIC_MISSING_MSG = (
    "The semantic layer hasn't been created yet, so I can't answer questions. "
    "Enable semantic compilation to build it."
)

# How often the watchdog polls the ASGI disconnect signal. Small enough that
# a navigate-away → come-back-and-ask flow always finds the slot free.
_DISCONNECT_POLL_S = 0.1

# How often `_stream_slot` re-checks `slot.buffer` for new events when it
# has caught up to the live tail. Also the SSE heartbeat cadence.
_BUFFER_POLL_S = 0.25

# Headers every SSE response here carries. Shared so the two endpoints cannot
# drift apart — ``/chat/watch`` is a GET, so it is the one a shared cache would
# actually be willing to store.
#
# ``no-store``, not ``no-cache``: the latter permits storing the response and
# only requires revalidation before reuse, and these bodies carry the user's
# SQL and the rows it returned. ``X-Accel-Buffering`` is what stops nginx from
# buffering the stream; the cache directive has no bearing on that.
_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


def _resolve_chat_target_db(target_db: str | None) -> str | None:
    """Resolve a catalog database UUID or name to its canonical name."""
    if target_db is None or not target_db.strip():
        return None

    requested = target_db.strip()
    databases = datasources_dal.fetch_databases(zone_ids=None)
    for database in databases:
        database_id = str(database.get("id") or "").strip()
        database_name = str(database.get("name") or "").strip()
        if database_name and (
            requested == database_id or requested.casefold() == database_name.casefold()
        ):
            return database_name

    raise HTTPException(
        status_code=422,
        detail=(
            f"target_db {target_db!r} does not match a catalog database UUID or name."
        ),
    )


@dataclass
class _Slot:
    """In-flight stream descriptor stored in ``_active_slots``."""

    key: str
    worker: PrewarmedWorker
    user_id: str | None
    conversation_id: UUID | None
    analytics_id: str | None
    # The question this run answers — needed by ``_pump`` to generate the
    # chart step itself (mirrors what the client used to pass to
    # ``POST /chat/visualize``).
    question: str
    client_alive: threading.Event
    # Set if the watchdog detected a client disconnect. Tells the stream's
    # finally whether to ``replace`` the worker (cancel) or ``return_alive``
    # it to the warm pool (natural completion).
    cancelled: threading.Event
    # Latches the moment any path (watchdog or pump) has called back into
    # the pool, so the second path doesn't double-release.
    released: threading.Event
    # Append-only log of every SSE event this run has produced so far,
    # filled by ``_pump`` independently of any HTTP connection. Any number
    # of ``_stream_slot`` consumers (the original submitter, plus any
    # ``/chat/watch`` reconnects) replay it from index 0 and then keep
    # tailing it live — this is what lets a reloaded browser tab resume
    # watching a run that started before the reload.
    buffer: list[dict] = field(default_factory=list)
    buffer_lock: threading.Lock = field(default_factory=threading.Lock)
    # Set once the worker's event generator ends (naturally or via error),
    # i.e. once ``buffer`` is complete and no more events will ever be
    # appended.
    finished: threading.Event = field(default_factory=threading.Event)
    # Last SQL the run streamed out (from a ``sql`` event). A failure after
    # the query was validated — execution errors that exhaust the retry
    # budget, or a hard error — produces an answer with no ``sql_code``, so
    # without remembering it here the query the user just watched fail would
    # vanish from the persisted turn the moment the page reloaded. A run that
    # never got a query past validation has no ``sql`` event and so persists
    # none. See ``_pump``.
    last_sql: str | None = None
    query_mode: str = "explore"
    query_context: dict[str, Any] | None = None
    ticket: chat_runs.Ticket | None = None
    lease_guard: LeaseGuard | None = None


# Keyed by authenticated user + conversation_id (or a per-request unique key
# for stateless calls), so distinct users and conversations never contend.
_active_slots: dict[str, _Slot] = {}
_slot_lock = threading.Lock()
_pending_slots: dict[str, str | None] = {}
_MAX_USER_RUNS = max(1, int(os.getenv("CHAT_MAX_RUNS_PER_USER", "2")))
_MAX_ACTIVE_RUNS = max(
    1, int(os.getenv("CHAT_MAX_ACTIVE_RUNS", os.getenv("CHAT_MAX_WORKERS", "4")))
)


def _reserve_slot(key: str, user_id: str | None) -> None:
    """Reject conflicting/over-limit work before allocating threads or processes."""
    with _slot_lock:
        previous = _active_slots.get(key)
        if key in _pending_slots or previous is not None:
            raise HTTPException(409, detail="Conversation in progress")
        running = list(_active_slots.values())
        if len(running) + len(_pending_slots) >= _MAX_ACTIVE_RUNS:
            raise HTTPException(
                429, detail="Chat capacity is full", headers={"Retry-After": "2"}
            )
        used = sum(slot.user_id == user_id for slot in running) + sum(
            owner == user_id for owner in _pending_slots.values()
        )
        if used >= _MAX_USER_RUNS:
            raise HTTPException(
                429, detail="User chat capacity is full", headers={"Retry-After": "2"}
            )
        _pending_slots[key] = user_id


async def _acquire_worker(pool: Any) -> PrewarmedWorker:
    task = asyncio.create_task(asyncio.to_thread(pool.acquire))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:

        def recycle(completed: asyncio.Task) -> None:
            if not completed.cancelled() and completed.exception() is None:
                pool.return_alive(completed.result())

        task.add_done_callback(recycle)
        raise


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _try_claim_slot(new_slot: _Slot) -> None:
    """Claim only an empty slot; a finished run must still complete release."""

    with _slot_lock:
        prev = _active_slots.get(new_slot.key)
        if prev is not None:
            raise HTTPException(
                status_code=409,
                detail="Conversation in progress",
            )
        _active_slots[new_slot.key] = new_slot
        _pending_slots.pop(new_slot.key, None)


def _release(slot: _Slot) -> None:
    """Free the slot and hand the worker back to the pool.

    Idempotent. ``slot.cancelled`` decides whether the worker is killed
    (cancel) or returned alive (natural end). The first caller wins; the
    second is a no-op via ``slot.released``.
    """

    with _slot_lock:
        if slot.released.is_set():
            return
        slot.released.set()
        slot.client_alive.clear()
        if _active_slots.get(slot.key) is slot:
            del _active_slots[slot.key]

    pool = get_metrics_pool() if slot.query_mode == "metrics" else get_pool()
    try:
        if slot.cancelled.is_set():
            pool.replace(slot.worker)
        else:
            pool.return_alive(slot.worker)
    except Exception as exc:
        slot.worker.kill()
        logger.warning("Worker release failed error_class=%s", type(exc).__name__)


async def _watch_disconnect(http_request: Request, slot: _Slot) -> None:
    """Mark the slot orphaned the moment the ASGI client disconnects.

    Without this, the sync streaming generator only learns the client is
    gone when its next write to the socket fails — and the kernel happily
    buffers small heartbeat writes, so that signal can lag by tens of
    seconds, holding the slot and forcing follow-up requests into 409.

    Crucially, this only clears ``client_alive`` — it never cancels the
    run or releases the slot itself. A disconnected client (navigated
    away, switched to another conversation, closed the tab) must not cut
    the agent off mid-answer: ``_pump`` keeps draining the worker and
    persisting the result regardless of whether anyone is watching, so the
    run always finishes and the full answer always lands in the
    conversation. The slot remains reserved until persistence and release
    finish; reconnecting never implicitly cancels or replaces a run.
    """

    while slot.client_alive.is_set():
        try:
            disconnected = await http_request.is_disconnected()
        except Exception:  # noqa: BLE001 — defensive; never want to leak
            logger.exception("Disconnect watchdog failed")
            return
        if disconnected:
            slot.client_alive.clear()
            return
        await asyncio.sleep(_DISCONNECT_POLL_S)


def _build_charts_event(slot: _Slot, answer: dict[str, Any]) -> dict[str, Any] | None:
    """Compute and persist Message 2 (chart, or the raw result table) for ``answer``.

    Runs unconditionally from ``_pump`` — the same guarantee the SQL answer
    already gets — instead of depending on a browser tab staying around to
    ask for it. Returns the SSE event to buffer (so any live/reattached
    consumer renders it immediately), or ``None`` when there's nothing to
    show (no executed result).
    """

    # Stateless calls (no ``conversation_id``) have nowhere to persist a
    # chart bubble to, so they simply don't get one.
    if slot.conversation_id is None or slot.user_id is None:
        return None

    sql_response_from_db = answer.get("sql_response_from_db")
    if not sql_response_from_db:
        return None

    charts: list[dict[str, Any]] | None = None
    llm = _non_reasoning_llm or _reasoning_llm
    if llm is not None and slot.query_mode != "metrics":
        try:
            if is_visualization_enabled():
                charts = analyze_and_visualize(
                    llm=llm,
                    question=slot.question,
                    sql=str(answer.get("sql_code") or ""),
                    sql_response_from_db=sql_response_from_db,
                )
        except Exception:  # noqa: BLE001 — charts are best-effort
            logger.exception("Chart generation failed")

    if slot.cancelled.is_set():
        return None

    built = build_result_message(sql_response_from_db, charts)
    if built is None:
        return None
    content, sql_response = built

    if slot.cancelled.is_set():
        return None

    if slot.ticket is None:
        # Local, non-server callers retain the helper's historical behavior.
        persist_result_message(
            conversation_id=slot.conversation_id,
            user_id=slot.user_id,
            content=content,
            sql_response=sql_response,
            query_context=answer.get("query_context") or slot.query_context,
        )

    event = {"type": "charts", "content": content, "sql_response": sql_response}
    event["query_context"] = answer.get("query_context") or slot.query_context
    return event


def _pump(slot: _Slot) -> None:
    """Persist events and history atomically, fenced by the current run lease."""
    persisted = False
    terminal_received = False
    failure = False

    def emit(event: dict) -> None:
        nonlocal persisted, terminal_received, failure
        old_persisted = persisted
        try:
            with (
                chat_runs.owned_transaction(slot.ticket)
                if slot.ticket
                else nullcontext()
            ):
                # Check budgets before history writes; rollback both on failure.
                if slot.ticket:
                    chat_runs.append(slot.ticket, event)
                kind = event.get("type")
                if kind in {"result", "error"}:
                    terminal_received = True
                    failure = failure or kind == "error"
                    if not persisted and slot.user_id and slot.analytics_id:
                        answer = event.get("answer") or {}
                        response = str(
                            answer.get("response") or event.get("message") or ""
                        )
                        sql_code = answer.get("sql_code") or slot.last_sql
                        context = answer.get("query_context") or slot.query_context
                        if slot.conversation_id:
                            persist_assistant_result(
                                conversation_id=slot.conversation_id,
                                user_id=slot.user_id,
                                analytics_id=slot.analytics_id,
                                response=response,
                                sql_code=sql_code,
                                query_context=context,
                            )
                        else:
                            persist_analytics_result(
                                analytics_id=slot.analytics_id,
                                user_id=slot.user_id,
                                response=response,
                                sql_code=sql_code,
                            )
                        persisted = True
                elif (
                    kind == "charts"
                    and slot.ticket
                    and slot.conversation_id
                    and slot.user_id
                ):
                    persist_result_message(
                        conversation_id=slot.conversation_id,
                        user_id=slot.user_id,
                        content=event["content"],
                        sql_response=event["sql_response"],
                        query_context=event.get("query_context") or slot.query_context,
                    )
            if slot.ticket is None:
                with slot.buffer_lock:
                    slot.buffer.append(event)
        except BaseException:
            persisted = old_persisted
            raise

    def failed_event(cancelled: bool) -> dict:
        message = "查询已取消。" if cancelled else "查询未能完成，请重试。"
        if slot.query_mode != "metrics":
            return {"type": "error", "message": message}
        context = {
            **(slot.query_context or {}),
            "status": "cancelled" if cancelled else "error",
            "trusted": False,
            "clarification": {
                "code": "QUERY_CANCELLED" if cancelled else "WORKER_FAILED",
                "message": message,
                "options": [],
            },
        }
        return {
            "type": "result",
            "answer": {"response": message, "query_context": context},
        }

    try:
        for event in slot.worker.events():
            if slot.cancelled.is_set():
                raise chat_runs.RunCancelled("Query cancelled")
            if event is None:
                continue
            if event.get("type") == "metric_context":
                slot.query_context = event["query_context"]
            elif event.get("type") == "result" and event.get("answer", {}).get(
                "query_context"
            ):
                slot.query_context = event["answer"]["query_context"]
            if slot.analytics_id:
                slot.query_context = {
                    **(slot.query_context or {}),
                    "turn_id": slot.analytics_id,
                }
            if event.get("type") == "result":
                event = {
                    **event,
                    "answer": {
                        **(event.get("answer") or {}),
                        "query_context": slot.query_context,
                    },
                }
            elif event.get("type") == "error":
                event = {**event, "query_context": slot.query_context}
            if event.get("type") == "step":
                node = event.get("node", "")
                event = {
                    **event,
                    "label": event.get("label") or NODE_LABELS.get(node, node),
                }
            elif event.get("type") == "sql":
                slot.last_sql = str(event.get("sql") or "") or None
            emit(event)
            if event.get("type") == "result" and not slot.cancelled.is_set():
                chart = _build_charts_event(slot, event.get("answer") or {})
                if chart is not None:
                    emit(chart)
        if not terminal_received:
            raise RuntimeError("Worker stopped without a terminal result")
        if slot.ticket:
            chat_runs.finish(slot.ticket, "failed" if failure else "succeeded")
    except Exception as exc:
        cancelled = slot.cancelled.is_set() or isinstance(exc, chat_runs.RunCancelled)
        slot.cancelled.set()
        logger.warning("Chat run ended error_class=%s", type(exc).__name__)
        if slot.ticket:
            try:
                message = (
                    "查询已取消。"
                    if cancelled
                    else (
                        "结果超过交互查询预算，请缩小范围后重试。"
                        if isinstance(exc, chat_runs.EventBudgetExceeded)
                        else "查询未能完成，请重试。"
                    )
                )
                chat_runs.finish(
                    slot.ticket, "cancelled" if cancelled else "failed", message=message
                )
            except Exception as final_error:
                # A stale owner cannot overwrite recovery or a newer run.
                logger.warning(
                    "Chat finalization deferred error_class=%s",
                    type(final_error).__name__,
                )
        else:
            emit(failed_event(cancelled))
    finally:
        if slot.lease_guard:
            slot.lease_guard.close()
        slot.finished.set()
        _release(slot)


def _subject_token(http_request: Request) -> str | None:
    """Return the caller's SSO JWT when a connection authenticates as the user.

    The frontend forwards the browser user's SSO token (or a service caller's
    own bearer token) as ``Authorization: Bearer``. Fail closed: when any
    connection has "Authenticate as signed-in user" enabled, a request without a
    token is rejected rather than allowed to run under that connection's PAT.
    """
    from auto_ontology.connectors.databricks_oauth import (
        any_connection_uses_sso_federation,
        DatabricksOAuthError,
    )

    try:
        requires_token = any_connection_uses_sso_federation()
    except DatabricksOAuthError as exc:
        raise HTTPException(
            503, detail="Connection authorization is unavailable"
        ) from exc
    if not requires_token:
        return None

    header = http_request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "A connection is configured to authenticate as the signed-in "
                "user, but the request carried no SSO bearer token."
            ),
        )
    return token


def _stream_slot(slot: _Slot) -> Generator[str, None, None]:
    """Replay ``slot.buffer`` from the start, then tail it live.

    Shared by the submitting request and any later ``/chat/watch``
    reconnects — every consumer just reads the same growing buffer, so a
    client that reconnects mid-run sees every step from the beginning
    before catching up to the live tail.
    """

    if slot.ticket:
        yield from _stream_run(slot.ticket.run_id, slot.user_id)
        return
    index = 0
    while True:
        with slot.buffer_lock:
            new_events = slot.buffer[index:]
            index = len(slot.buffer)
        for event in new_events:
            yield _sse(event)
        if new_events:
            continue
        if slot.finished.is_set():
            break
        # SSE comment — keeps the connection alive against any intermediary
        # (proxy/load balancer) that drops idle streams. Clients ignore
        # non-"data:" lines.
        yield ": ping\n\n"
        time.sleep(_BUFFER_POLL_S)

    yield "data: [DONE]\n\n"


def _stream_run(
    run_id: str, user_id: str | None, after: int = 0
) -> Generator[str, None, None]:
    while True:
        try:
            events, status = chat_runs.replay(
                run_id=run_id, user_id=user_id, after=after
            )
        except Exception:
            yield _sse({"type": "error", "message": "运行记录暂时不可用，请重新连接。"})
            return
        for item in events:
            after = item["sequence"]
            yield f"id: {run_id}:{after}\n" + _sse(item["event"])
        if events:
            continue
        if status not in chat_runs.ACTIVE:
            yield "data: [DONE]\n\n"
            return
        yield ": ping\n\n"
        time.sleep(_BUFFER_POLL_S)


def _prepare_run(
    ticket: chat_runs.Ticket,
    request: ChatRequest,
    user_id: str | None,
    source: str,
    context: dict,
) -> tuple[str | None, list[dict]]:
    with chat_runs.owned_transaction(ticket):
        analytics_id, history = None, []
        if request.conversation_id is not None and user_id is not None:
            prepared = prepare_conversation(
                conversation_id=request.conversation_id,
                user_id=user_id,
                question=request.question,
                source=source,
                query_context=context,
            )
            analytics_id = prepared.analytics_id
            history = [vars(turn) for turn in prepared.history]
        elif user_id:
            analytics_id = create_stateless_analytics(
                user_id=user_id, question=request.question, source=source
            )
        chat_runs.attach_analytics(ticket, analytics_id, context)
        return analytics_id, history


@router.post(
    "/chat/completions",
    response_class=EventStreamResponse,
    responses=SSE_RESPONSES,
)
async def chat_completions(
    request: ChatRequest, http_request: Request
) -> StreamingResponse:
    """Run the text-to-SQL agent and stream OpenAI-style SSE frames back.

    Emits a ``sql`` event once the agent has a validated query, just before it
    executes, so callers can show the SQL while it runs. Emits the final SQL
    and formatted answer as a ``result`` event, then — if that
    answer has an executed result — generates and persists the chart/table
    bubble itself and streams it back as a ``charts`` event before the stream
    closes with ``[DONE]``. Callers that only care about the text/SQL answer
    can still stop reading after ``result``; the chart step and its
    persistence happen server-side regardless of whether anyone keeps
    listening (see ``_build_charts_event``).

    ``query_mode=explore`` (the compatibility default) requires a compiled
    semantic layer. ``query_mode=metrics`` instead requires ``metric_context``
    selecting an active certified model, authenticates catalog/chat access,
    interprets a typed request and executes the governed read-only path. It
    never falls back to exploratory SQL. Clarifications/errors are persisted;
    exact table values and query evidence travel in ``query_context`` JSON.
    ``conversation_id`` persists the
    turn and claims that conversation's single run slot — a second concurrent
    request for the same conversation gets 409 too. Omitting ``conversation_id``
    runs the question statelessly: no history, no persisted turn, and no chart
    step (``_build_charts_event`` needs a conversation to save into).
    """
    logger.info(
        "Chat request mode=%s question_chars=%d",
        request.query_mode,
        len(request.question),
    )

    if request.query_mode == "explore" and not await asyncio.to_thread(
        semantic_layer_calculated
    ):
        raise HTTPException(status_code=409, detail=_SEMANTIC_MISSING_MSG)
    target_db = (
        await asyncio.to_thread(_resolve_chat_target_db, request.target_db)
        if request.query_mode == "explore"
        else None
    )
    user_id = resolve_internal_user(
        http_request,
        required=request.conversation_id is not None or request.query_mode == "metrics",
    )
    if request.query_mode == "metrics":
        try:
            await asyncio.to_thread(
                require_metric_access,
                user_id,
                persistent=request.conversation_id is not None,
            )
        except ContractError as exc:
            raise HTTPException(
                403, detail={"code": exc.code, "message": exc.message}
            ) from exc
    key = (
        f"{user_id}:{request.conversation_id}"
        if request.conversation_id is not None
        else str(uuid.uuid4())
    )
    subject_token = (
        await asyncio.to_thread(_subject_token, http_request)
        if request.query_mode == "explore"
        else None
    )
    _reserve_slot(key, user_id)
    ticket = None
    guard = None
    slot = None
    worker = None
    pool = None
    launched = False
    try:
        ticket = await asyncio.to_thread(
            chat_runs.reserve,
            user_id=user_id,
            conversation_id=str(request.conversation_id)
            if request.conversation_id
            else None,
            query_mode=request.query_mode,
            owner_id=OWNER_ID,
            global_limit=_MAX_ACTIVE_RUNS,
            user_limit=_MAX_USER_RUNS,
        )
        guard = LeaseGuard(ticket)
        context: dict[str, Any] = {"version": 1, "query_mode": request.query_mode}
        if request.metric_context is not None:
            context.update(
                request.metric_context.model_dump(mode="json"),
                status="running",
                trusted=False,
            )
            context["selected_metric_ids"] = list(
                request.metric_context.selected_metric_ids or ()
            )
        analytics_id, history = await asyncio.to_thread(
            _prepare_run,
            ticket,
            request,
            user_id,
            http_request.headers.get("x-auto-ontology-source") or "api",
            context,
        )
        context["turn_id"] = analytics_id
        pool = await asyncio.to_thread(
            get_metrics_pool if request.query_mode == "metrics" else get_pool
        )
        worker = await _acquire_worker(pool)
        await asyncio.to_thread(guard.set_worker, worker)
        slot = _Slot(
            key=key,
            worker=worker,
            user_id=user_id,
            conversation_id=request.conversation_id,
            analytics_id=analytics_id,
            question=request.question,
            query_mode=request.query_mode,
            query_context=context,
            ticket=ticket,
            lease_guard=guard,
            client_alive=threading.Event(),
            cancelled=guard.cancelled,
            released=threading.Event(),
        )
        slot.client_alive.set()
        _try_claim_slot(slot)
        if request.query_mode == "metrics":
            worker.submit(
                request.question,
                metric_context=request.metric_context.model_dump(mode="json"),
                user_id=user_id,
                persistent=request.conversation_id is not None,
                turn_id=analytics_id,
                conversation_history=history,
            )
        else:
            worker.submit(
                request.question,
                prediction=request.prediction,
                target_db=target_db,
                subject_token=subject_token,
                conversation_history=history,
                evidence=request.evidence,
            )
        threading.Thread(target=_pump, args=(slot,), daemon=True).start()
        launched = True
        asyncio.create_task(_watch_disconnect(http_request, slot))
        return StreamingResponse(
            _stream_slot(slot),
            media_type="text/event-stream",
            headers={**_SSE_HEADERS, "X-Chat-Run-Id": ticket.run_id},
        )
    except chat_runs.AdmissionDenied as exc:
        raise HTTPException(
            exc.status, detail=str(exc), headers={"Retry-After": "2"}
        ) from exc
    except ConversationAccessError as exc:
        raise HTTPException(404, detail="Conversation not found") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Chat admission failed error_class=%s", type(exc).__name__)
        raise HTTPException(
            503,
            detail="Chat worker or run storage unavailable; retry shortly",
            headers={"Retry-After": "2"},
        ) from exc
    finally:
        if not launched:
            with _slot_lock:
                _pending_slots.pop(key, None)
            if ticket:
                try:
                    await asyncio.to_thread(
                        chat_runs.finish,
                        ticket,
                        "failed",
                        message="查询未能启动，请稍后重试。",
                    )
                except Exception as exc:
                    logger.warning(
                        "Pending run recovery deferred error_class=%s",
                        type(exc).__name__,
                    )
            if guard:
                await asyncio.to_thread(guard.close)
            if slot:
                slot.cancelled.set()
                await asyncio.to_thread(_release, slot)
            elif worker is not None and pool is not None:
                await asyncio.to_thread(pool.replace, worker)


@router.get(
    "/chat/watch",
    response_class=EventStreamResponse,
    responses=SSE_RESPONSES,
)
async def chat_watch(
    conversation_id: UUID,
    request: Request,
    last_event_id: Annotated[
        str | None,
        Header(
            description="Resume after <run UUID>:<sequence>, including completed retained runs."
        ),
    ] = None,
) -> StreamingResponse:
    """Reattach to an in-flight run for ``conversation_id``, if any.

    Purely a passive observer: it never submits a question, never touches
    ``client_alive``/``cancelled`` (so it can't trigger or block the 409
    "Conversation in progress" logic in `_try_claim_slot`), and has no
    effect on the run if it disconnects. This is what lets a reloaded or
    reopened browser tab recover the live progress UI (and, once it lands,
    the final answer) for a run it didn't itself start — replaying retained steps from the beginning, then tailing new ones as they arrive.
    Last-Event-ID accepts <run UUID>:<sequence> to resume after an acknowledged
    event, including a completed run retained for the configured replay window.

    If nothing is running for this conversation, the stream ends
    immediately with `[DONE]` and no events, which the frontend treats
    identically to "nothing to resume" — no separate status endpoint
    needed.
    """

    user_id = resolve_internal_user(request, required=True)
    after = 0
    run_id = None
    last_id = last_event_id or ""
    if last_id:
        try:
            run_id, sequence = last_id.rsplit(":", 1)
            run_id = str(UUID(run_id))
            after = int(sequence)
            if after < 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail="Invalid Last-Event-ID") from exc
    row = await asyncio.to_thread(
        chat_runs.latest,
        user_id=user_id,
        conversation_id=str(conversation_id),
        run_id=run_id,
    )
    if row is None:
        if run_id:
            raise HTTPException(404, detail="Run not found")
        return StreamingResponse(
            iter(["data: [DONE]\n\n"]),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    if after > row["event_count"]:
        raise HTTPException(422, detail="Event cursor is ahead of this run")
    if not run_id and row["status"] not in chat_runs.ACTIVE:
        # The browser already loaded persisted history before probing. An
        # explicit Last-Event-ID can still resume a completed retained run.
        return StreamingResponse(
            iter(["data: [DONE]\n\n"]),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    if row["query_mode"] == "metrics":
        try:
            await asyncio.to_thread(require_metric_access, user_id, persistent=True)
        except ContractError as exc:
            raise HTTPException(
                403, detail={"code": exc.code, "message": exc.message}
            ) from exc
    return StreamingResponse(
        _stream_run(row["id"], user_id, after),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat/cancel", response_model=ChatCancelResponse)
async def chat_cancel(conversation_id: UUID, request: Request) -> dict[str, bool]:
    """Abort the in-flight run for ``conversation_id``, if any.

    Backs the chat UI's Stop button. Without this, stopping only detached
    the browser while the agent kept running and holding the slot, so the
    very next question in the same conversation was rejected with 409.

    Cancellation is persisted for every replica. The owner heartbeat kills
    the worker; a local owner is also interrupted immediately. The run remains
    reserved until terminal persistence or lease expiry. Metric runs record an untrusted cancellation result tied to
    their server-generated turn ID; exploratory runs retain their existing
    partial-stream behavior. The slot remains unavailable while terminal
    persistence is unfinished, so a subsequent question cannot consume a late
    assistant result. PostgreSQL 14+ detects the dead client during execution;
    older versions retain the governed statement-timeout bound. MySQL workers
    request same-account KILL CONNECTION before exiting; the server timeout
    remains a fallback when cancellation cannot reach the database.
    """

    user_id = resolve_internal_user(request, required=True)
    cancelled = await asyncio.to_thread(
        chat_runs.cancel, user_id=user_id, conversation_id=str(conversation_id)
    )
    key = f"{user_id}:{conversation_id}"
    with _slot_lock:
        slot = _active_slots.get(key)
    if cancelled and slot is not None:
        slot.cancelled.set()
        await asyncio.to_thread(slot.worker.kill)
        await asyncio.to_thread(slot.finished.wait, 5)
    return {"cancelled": cancelled}
