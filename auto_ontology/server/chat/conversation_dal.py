# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Postgres persistence and bounded history loading for chat conversations."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from auto_ontology.dal.chat_history import conversations, messages, analytics
from auto_ontology.dal.session import store, write_transaction

logger = logging.getLogger(__name__)

_MAX_HISTORY_TURNS = 5
_MAX_HISTORY_CHARS = 12_000
_MAX_QUESTION_CHARS = 2_000
_MAX_RESPONSE_CHARS = 4_000
_MAX_SQL_CHARS = 6_000


class ConversationAccessError(Exception):
    """Raised when a conversation exists but belongs to another user."""


@dataclass(frozen=True)
class ConversationTurn:
    """One completed user/assistant exchange suitable for LLM context."""

    question: str
    response: str
    sql_code: str | None = None
    query_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class PreparedConversation:
    """Identifiers and prior history created when accepting a chat turn."""

    history: list[ConversationTurn]
    analytics_id: str


def _is_context_assistant(row: dict) -> bool:
    if row["role"] != "assistant" or row["sql_response"] is not None:
        return False
    content = (row["content"] or "").strip()
    if content.startswith("```chart") or content.startswith("```chart-carousel"):
        return False
    if content.startswith("Agent failed:") or content.startswith(
        "Agent stream failed:"
    ):
        return False
    return bool(content or row["sql_code"])


def _rows_to_history(
    rows: list[dict], *, include_incomplete: bool = False
) -> list[ConversationTurn]:
    """Pair trusted turn IDs, retaining complete metric turns and boundaries.

    User ordering is stable even when equal timestamps place an assistant row
    first. Legacy rows retain their adjacent-pair behavior and text caps.
    """
    users: list[dict] = []
    by_turn: dict[str, dict] = {}
    at_index: dict[int, dict] = {}
    for index, row in enumerate(rows):
        if row["role"] == "user":
            entry = {"row": row, "answer": None}
            users.append(entry)
            at_index[index] = entry
            turn_id = (row.get("query_context") or {}).get("turn_id")
            if turn_id:
                by_turn[turn_id] = entry
    pending: dict | None = None
    for index, row in enumerate(rows):
        if row["role"] == "user":
            pending = at_index[index]
            continue
        if not _is_context_assistant(row):
            continue
        turn_id = (row.get("query_context") or {}).get("turn_id")
        target = by_turn.get(turn_id) if turn_id else pending
        if target is None or target["answer"] is not None:
            continue
        user_turn_id = (target["row"].get("query_context") or {}).get("turn_id")
        if user_turn_id and user_turn_id != turn_id:
            continue
        target["answer"] = row
        if target is pending:
            pending = None
    turns: list[ConversationTurn] = []
    for entry in users:
        question = (entry["row"]["content"] or "").strip()
        answer = entry["answer"]
        if not question:
            if include_incomplete:
                turns.append(
                    ConversationTurn(
                        question="",
                        response="",
                        query_context={"query_mode": "boundary"},
                    )
                )
            continue
        if answer is None:
            if include_incomplete:
                turns.append(
                    ConversationTurn(
                        question="",
                        response="",
                        query_context={"query_mode": "boundary"},
                    )
                )
            continue
        response = (answer["content"] or "").strip()
        sql_code = (answer["sql_code"] or "") or None
        context = answer.get("query_context")
        if not include_incomplete:
            question = question[:_MAX_QUESTION_CHARS]
            response = response[:_MAX_RESPONSE_CHARS]
            sql_code = sql_code[:_MAX_SQL_CHARS] if sql_code else None
        else:
            # Metric interpretation receives typed requests, never prior SQL.
            sql_code = None
        turns.append(
            ConversationTurn(
                question=question,
                response=response,
                sql_code=sql_code,
                query_context=context,
            )
        )
    selected: list[ConversationTurn] = []
    used_chars = 0
    for turn in reversed(turns):
        turn_chars = len(turn.question) + len(turn.response) + len(turn.sql_code or "")
        if include_incomplete and not selected and turn_chars > _MAX_HISTORY_CHARS:
            # Keep the scope pin but demand a restatement. Silently truncating
            # a pending clarification could discard its final exclusion clause.
            selected.append(
                ConversationTurn(
                    question="",
                    response="",
                    query_context={
                        **(turn.query_context or {}),
                        "context_too_large": True,
                    },
                )
            )
            break
        if selected and used_chars + turn_chars > _MAX_HISTORY_CHARS:
            break
        selected.append(turn)
        used_chars += turn_chars
        if len(selected) >= _MAX_HISTORY_TURNS:
            break
    return list(reversed(selected))


def prepare_conversation(
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    question: str,
    source: str,
    query_context: dict[str, Any] | None = None,
) -> PreparedConversation:
    """Verify ownership and persist the user turn in the caller's transaction."""
    conversation_str = str(conversation_id)
    analytics_id = str(uuid.uuid4())
    context = {**(query_context or {}), "turn_id": analytics_id}
    with write_transaction():
        store().query_write(
            insert(conversations)
            .values(
                id=conversation_str,
                user_id=user_id,
                title=question[:50] or "New conversation",
                created_at=func.now(),
                updated_at=func.now(),
            )
            .on_conflict_do_nothing()
        )
        _require_owner(conversation_str, user_id, lock=True)
        # Load the newest bounded window. Retained turn context must carry any
        # older normalized metric constraints; it cannot rely on unlimited SQL.
        rows = store().query_read(
            select(messages)
            .where(messages.c.conversation_id == conversation_str)
            .order_by(messages.c.created_at.desc(), messages.c.id.desc())
            .limit(128)
        )
        history = _rows_to_history(
            list(reversed(rows)),
            include_incomplete=context.get("query_mode") == "metrics",
        )
        store().query_write(
            insert(messages).values(
                id=str(uuid.uuid4()),
                conversation_id=conversation_str,
                role="user",
                content=question,
                query_context=context,
                created_at=func.now(),
            )
        )
        store().query_write(
            insert(analytics).values(
                id=analytics_id,
                user_id=user_id,
                source=source,
                question=question,
                question_timestamp=func.now(),
            )
        )
        store().query_write(
            update(conversations)
            .where(conversations.c.id == conversation_str)
            .values(updated_at=func.now())
        )
    return PreparedConversation(history=history, analytics_id=analytics_id)


def _require_owner(conversation_id: str, user_id: str, *, lock: bool = False) -> None:
    statement = select(conversations.c.user_id).where(
        conversations.c.id == conversation_id
    )
    if lock:
        statement = statement.with_for_update()
    rows = store().query_read(statement)
    if not rows or rows[0]["user_id"] != user_id:
        raise ConversationAccessError(conversation_id)


def persist_assistant_result(
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    analytics_id: str,
    response: str,
    sql_code: str | None,
    query_context: dict[str, Any] | None = None,
) -> None:
    """Atomically, idempotently persist one answer and its analytics."""
    with write_transaction():
        _require_owner(str(conversation_id), user_id)
        context = {**(query_context or {}), "turn_id": analytics_id}
        store().query_write(
            insert(messages)
            .values(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, analytics_id + ":answer")),
                conversation_id=str(conversation_id),
                role="assistant",
                content=response,
                sql_code=sql_code,
                query_context=context,
                created_at=func.now(),
            )
            .on_conflict_do_nothing()
        )
        persist_analytics_result(
            analytics_id=analytics_id,
            user_id=user_id,
            response=response,
            sql_code=sql_code,
        )
        store().query_write(
            update(conversations)
            .where(conversations.c.id == str(conversation_id))
            .values(updated_at=func.now())
        )


def persist_result_message(
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    content: str,
    sql_response: str | None,
    query_context: dict[str, Any] | None = None,
) -> None:
    """Persist the chart/table bubble, sharing the owner's fenced transaction."""
    with write_transaction():
        _require_owner(str(conversation_id), user_id)
        turn_id = (query_context or {}).get("turn_id")
        message_id = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, turn_id + ":table"))
            if turn_id
            else str(uuid.uuid4())
        )
        store().query_write(
            insert(messages)
            .values(
                id=message_id,
                conversation_id=str(conversation_id),
                role="assistant",
                content=content,
                sql_response=sql_response,
                query_context=query_context,
                created_at=func.now(),
            )
            .on_conflict_do_nothing()
        )
        store().query_write(
            update(conversations)
            .where(conversations.c.id == str(conversation_id))
            .values(updated_at=func.now())
        )


def create_stateless_analytics(*, user_id: str, question: str, source: str) -> str:
    """Create analytics for an authenticated one-shot request."""
    analytics_id = str(uuid.uuid4())
    store().query_write(
        insert(analytics).values(
            id=analytics_id,
            user_id=user_id,
            source=source,
            question=question,
            question_timestamp=func.now(),
        )
    )
    return analytics_id


def persist_analytics_result(
    *,
    analytics_id: str,
    user_id: str,
    response: str,
    sql_code: str | None,
) -> None:
    """Complete an analytics row once; repeated terminal delivery is harmless."""
    store().query_write(
        update(analytics)
        .where(
            analytics.c.id == analytics_id,
            analytics.c.user_id == user_id,
            analytics.c.response_timestamp.is_(None),
        )
        .values(response=response, sql=sql_code, response_timestamp=func.now())
    )


__all__ = [
    "ConversationAccessError",
    "ConversationTurn",
    "PreparedConversation",
    "create_stateless_analytics",
    "persist_analytics_result",
    "persist_assistant_result",
    "persist_result_message",
    "prepare_conversation",
]
