# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Durable admission, fenced leases and bounded event replay across replicas."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from auto_ontology.dal import schema as s
from auto_ontology.dal.chat_history import analytics, conversations, messages
from auto_ontology.dal.session import store, write_transaction

ACTIVE = ("pending", "running")
LEASE_SECONDS = max(3, int(os.getenv("CHAT_LEASE_SECONDS", "15")))
RUN_SECONDS = max(LEASE_SECONDS, int(os.getenv("CHAT_RUN_TIMEOUT_S", "300")))
MAX_EVENT_BYTES = max(2048, int(os.getenv("CHAT_MAX_EVENT_BYTES", "2097152")))
MAX_RUN_BYTES = max(
    MAX_EVENT_BYTES * 2, 8192, int(os.getenv("CHAT_MAX_RUN_BYTES", "8388608"))
)
MAX_EVENTS = max(4, int(os.getenv("CHAT_MAX_EVENTS", "1024")))
RETENTION_SECONDS = max(60, int(os.getenv("CHAT_EVENT_RETENTION_SECONDS", "86400")))
_ADMISSION_LOCK = 731520491


class AdmissionDenied(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class LeaseLost(RuntimeError):
    """An expired or superseded owner must stop without writing another result."""


class RunCancelled(RuntimeError):
    """Cancellation was requested on this or another replica."""


class EventBudgetExceeded(RuntimeError):
    """The replay log is full; the run must finish with a bounded error."""


@dataclass(frozen=True)
class Ticket:
    run_id: str
    owner_token: str


def _now() -> Any:
    return store().query_read(select(func.clock_timestamp().label("now")))[0]["now"]


def _configure_transaction() -> None:
    store().query_read("SET LOCAL lock_timeout = '3s'")
    store().query_read("SET LOCAL statement_timeout = '5s'")


def _row(run_id: str, *, lock: bool = False) -> dict | None:
    statement = select(s.chat_run).where(s.chat_run.c.id == run_id)
    if lock:
        statement = statement.with_for_update()
    rows = store().query_read(statement)
    return rows[0] if rows else None


@contextmanager
def owned_transaction(ticket: Ticket, *, allow_cancel: bool = False) -> Iterator[dict]:
    """Fence the event and its history write in the same database transaction."""
    with write_transaction():
        _configure_transaction()
        row = _row(ticket.run_id, lock=True)
        now = _now()
        if (
            row is None
            or row["owner_token"] != ticket.owner_token
            or row["status"] not in ACTIVE
            or row["lease_until"] <= now
            or row["deadline_at"] <= now
        ):
            raise LeaseLost("Chat run ownership expired")
        if row["cancel_requested"] and not allow_cancel:
            raise RunCancelled("Chat run cancelled")
        yield row


def _append(row: dict, event: dict, *, terminal: bool = False) -> None:
    clean = json.loads(
        json.dumps(event, ensure_ascii=False, default=str, allow_nan=False)
    )
    size = len(json.dumps(clean, ensure_ascii=False).encode())
    # Reserve a small terminal envelope even when a normal event hits the cap.
    if not terminal and (
        size > MAX_EVENT_BYTES
        or row["event_count"] >= MAX_EVENTS - 1
        or row["event_bytes"] + size > MAX_RUN_BYTES - 4096
    ):
        raise EventBudgetExceeded("Chat result budget exceeded")
    sequence = row["event_count"] + 1
    store().query_write(
        insert(s.chat_run_event).values(
            run_id=row["id"], sequence=sequence, event=clean
        )
    )
    store().query_write(
        update(s.chat_run)
        .where(s.chat_run.c.id == row["id"])
        .values(event_count=sequence, event_bytes=row["event_bytes"] + size)
    )
    row.update(event_count=sequence, event_bytes=row["event_bytes"] + size)


def append(ticket: Ticket, event: dict) -> None:
    with owned_transaction(ticket) as row:
        _append(row, event)
        context = event.get("query_context") or (event.get("answer") or {}).get(
            "query_context"
        )
        if context:
            store().query_write(
                update(s.chat_run)
                .where(s.chat_run.c.id == ticket.run_id)
                .values(query_context=context)
            )


def _terminal(row: dict, status: str, message: str | None = None) -> None:
    if message:
        message = message[:512]
        saved_context = row.get("query_context") or {}
        # Retain scope, never duplicate a large result/plan in the reserved
        # terminal envelope. Cancelled/failed turns cannot reuse a typed query.
        pin = {
            key: saved_context[key]
            for key in ("model_id", "revision", "model_fingerprint")
            if key in saved_context
        }
        context = {
            **pin,
            "version": 1,
            "query_mode": row["query_mode"],
            "status": status,
            "turn_id": row["analytics_id"],
            "trusted": False,
            "clarification": {
                "code": "RUN_" + status.upper(),
                "message": message,
                "options": [],
            },
        }
        completed = False
        if row["analytics_id"] and row["user_id"]:
            prior = store().query_read(
                select(analytics.c.response_timestamp).where(
                    analytics.c.id == row["analytics_id"],
                    analytics.c.user_id == row["user_id"],
                )
            )
            completed = bool(prior and prior[0]["response_timestamp"] is not None)
        event = {
            "type": "error",
            "message": message,
            "code": "RUN_" + status.upper(),
            "query_context": context,
        }
        if completed:
            event["partial_result_retained"] = True
        elif row["query_mode"] == "metrics":
            event = {
                "type": "result",
                "answer": {"response": message, "query_context": context},
            }
        _append(row, event, terminal=True)
        if row["analytics_id"] and row["user_id"]:
            store().query_write(
                update(analytics)
                .where(
                    analytics.c.id == row["analytics_id"],
                    analytics.c.user_id == row["user_id"],
                    analytics.c.response_timestamp.is_(None),
                )
                .values(response=message, response_timestamp=func.now())
            )
            if row["conversation_id"]:
                owner = store().query_read(
                    select(conversations.c.id).where(
                        conversations.c.id == row["conversation_id"],
                        conversations.c.user_id == row["user_id"],
                    )
                )
                if owner:
                    suffix = ":notice" if completed else ":answer"
                    store().query_write(
                        insert(messages)
                        .values(
                            id=str(uuid5(NAMESPACE_URL, row["analytics_id"] + suffix)),
                            conversation_id=row["conversation_id"],
                            role="assistant",
                            content=message,
                            query_context=context,
                            created_at=func.now(),
                        )
                        .on_conflict_do_nothing()
                    )
                    store().query_write(
                        update(conversations)
                        .where(conversations.c.id == row["conversation_id"])
                        .values(updated_at=func.now())
                    )
    store().query_write(
        update(s.chat_run)
        .where(s.chat_run.c.id == row["id"])
        .values(status=status, finished_at=func.clock_timestamp())
    )


def _reap_expired() -> int:
    now = _now()
    rows = store().query_read(
        select(s.chat_run)
        .where(
            s.chat_run.c.status.in_(ACTIVE),
            or_(s.chat_run.c.lease_until <= now, s.chat_run.c.deadline_at <= now),
        )
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    for row in rows:
        cancelled = row["cancel_requested"]
        _terminal(
            row,
            "cancelled" if cancelled else "interrupted",
            "查询已取消。"
            if cancelled
            else "执行进程失联或任务超过期限，查询已中断，请重试。",
        )
    return len(rows)


def reap_expired() -> int:
    with write_transaction():
        _configure_transaction()
        count = _reap_expired()
        # Bounded cleanup: a busy replica cannot monopolize retention work.
        expired = select(s.chat_run.c.id).where(
            s.chat_run.c.status.not_in(ACTIVE),
            s.chat_run.c.finished_at < _now() - timedelta(seconds=RETENTION_SECONDS),
        )
        store().query_write(
            delete(s.chat_run).where(s.chat_run.c.id.in_(expired.limit(100)))
        )
        return count


def reserve(
    *,
    user_id: str | None,
    conversation_id: str | None,
    query_mode: str,
    owner_id: str,
    global_limit: int,
    user_limit: int,
) -> Ticket:
    """Serialize admission across replicas, before allocating a subprocess."""
    with write_transaction():
        _configure_transaction()
        store().query_read(select(func.pg_advisory_xact_lock(_ADMISSION_LOCK)))
        _reap_expired()
        if conversation_id:
            owners = store().query_read(
                select(conversations.c.user_id).where(
                    conversations.c.id == conversation_id
                )
            )
            if owners and owners[0]["user_id"] != user_id:
                raise AdmissionDenied(404, "Conversation not found")
        key = (
            f"conversation:{conversation_id}"
            if conversation_id
            else f"stateless:{uuid4()}"
        )
        active = s.chat_run.c.status.in_(ACTIVE)
        if store().query_read(
            select(s.chat_run.c.id).where(active, s.chat_run.c.run_key == key)
        ):
            raise AdmissionDenied(409, "Conversation in progress")
        count = store().query_read(
            select(func.count().label("n")).select_from(s.chat_run).where(active)
        )[0]["n"]
        if count >= global_limit:
            raise AdmissionDenied(429, "Chat capacity is full")
        count = store().query_read(
            select(func.count().label("n"))
            .select_from(s.chat_run)
            .where(active, s.chat_run.c.user_id == user_id)
        )[0]["n"]
        if count >= user_limit:
            raise AdmissionDenied(429, "User chat capacity is full")
        ticket = Ticket(str(uuid4()), str(uuid4()))
        now = _now()
        store().query_write(
            insert(s.chat_run).values(
                id=ticket.run_id,
                run_key=key,
                user_id=user_id,
                conversation_id=conversation_id,
                owner_id=owner_id,
                owner_token=ticket.owner_token,
                query_mode=query_mode,
                status="pending",
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
                deadline_at=now + timedelta(seconds=RUN_SECONDS),
            )
        )
        return ticket


def attach_analytics(
    ticket: Ticket, analytics_id: str | None, context: dict | None = None
) -> None:
    with owned_transaction(ticket):
        store().query_write(
            update(s.chat_run)
            .where(s.chat_run.c.id == ticket.run_id)
            .values(analytics_id=analytics_id, status="running", query_context=context)
        )


def heartbeat(ticket: Ticket) -> bool:
    """Renew a valid lease; False requests immediate local worker cancellation."""
    with owned_transaction(ticket, allow_cancel=True) as row:
        store().query_write(
            update(s.chat_run)
            .where(s.chat_run.c.id == ticket.run_id)
            .values(
                lease_until=min(
                    row["deadline_at"], _now() + timedelta(seconds=LEASE_SECONDS)
                )
            )
        )
        return not row["cancel_requested"]


def finish(ticket: Ticket, status: str, *, message: str | None = None) -> None:
    if status not in ("succeeded", "failed", "cancelled"):
        raise ValueError("Invalid owner terminal status")
    with owned_transaction(ticket, allow_cancel=True) as row:
        if row["cancel_requested"]:
            status, message = "cancelled", "查询已取消。"
        _terminal(row, status, message)


def latest(
    *, user_id: str, conversation_id: str, run_id: str | None = None
) -> dict | None:
    reap_expired()
    statement = select(s.chat_run).where(
        s.chat_run.c.user_id == user_id, s.chat_run.c.conversation_id == conversation_id
    )
    if run_id:
        statement = statement.where(s.chat_run.c.id == run_id)
    rows = store().query_read(
        statement.order_by(s.chat_run.c.created_at.desc()).limit(1)
    )
    return rows[0] if rows else None


def replay(
    *, run_id: str, user_id: str | None, after: int = 0
) -> tuple[list[dict], str]:
    with write_transaction():
        _configure_transaction()
        _reap_expired()
        row = _row(run_id)
        if row is None or row["user_id"] != user_id:
            raise AdmissionDenied(404, "Run not found")
        events = store().query_read(
            select(s.chat_run_event)
            .where(
                s.chat_run_event.c.run_id == run_id, s.chat_run_event.c.sequence > after
            )
            .order_by(s.chat_run_event.c.sequence)
            .limit(32)
        )
        return events, row["status"]


def cancel(*, user_id: str, conversation_id: str) -> bool:
    with write_transaction():
        _configure_transaction()
        _reap_expired()
        return bool(
            store().query_write(
                update(s.chat_run)
                .where(
                    s.chat_run.c.user_id == user_id,
                    s.chat_run.c.conversation_id == conversation_id,
                    s.chat_run.c.status.in_(ACTIVE),
                )
                .values(cancel_requested=True)
                .returning(s.chat_run.c.id)
            )
        )
