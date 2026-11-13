# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Postgres persistence and bounded history loading for chat conversations."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from auto_ontology.infra.postgres import FRONTEND_SCHEMA, get_postgres_connection_string

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
    """Verify/create a conversation, load prior turns, then persist the user turn."""

    conversation_str = str(conversation_id)
    analytics_id = str(uuid.uuid4())
    if query_context is not None:
        query_context = {**query_context, "turn_id": analytics_id}
    with psycopg.connect(get_postgres_connection_string(), connect_timeout=3) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.conversations (id, user_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (id) DO NOTHING
                RETURNING user_id
                """,
                (conversation_str, user_id, question[:50] or "New conversation"),
            )
            owner = cur.fetchone()
            if owner is None:
                cur.execute(
                    f"SELECT user_id FROM {FRONTEND_SCHEMA}.conversations WHERE id = %s FOR UPDATE",
                    (conversation_str,),
                )
                owner = cur.fetchone()
            if owner is None or owner["user_id"] != user_id:
                raise ConversationAccessError(conversation_str)

            cur.execute(
                f"""
                SELECT role, content, sql_code, sql_response, query_context
                FROM {FRONTEND_SCHEMA}.messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (conversation_str,),
            )
            history = _rows_to_history(
                [dict(row) for row in cur.fetchall()],
                include_incomplete=(query_context or {}).get("query_mode") == "metrics",
            )

            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.messages
                    (id, conversation_id, role, content, query_context, created_at)
                VALUES (%s, %s, 'user', %s, %s, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    conversation_str,
                    question,
                    Jsonb(query_context) if query_context is not None else None,
                ),
            )
            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.conversation_analytics
                    (id, user_id, source, question, question_timestamp)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (analytics_id, user_id, source, question),
            )
            cur.execute(
                f"UPDATE {FRONTEND_SCHEMA}.conversations SET updated_at = NOW() WHERE id = %s",
                (conversation_str,),
            )

    return PreparedConversation(history=history, analytics_id=analytics_id)


def persist_assistant_result(
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    analytics_id: str,
    response: str,
    sql_code: str | None,
    query_context: dict[str, Any] | None = None,
) -> None:
    """Persist one final assistant turn and complete its analytics row."""

    conversation_str = str(conversation_id)
    with psycopg.connect(get_postgres_connection_string(), connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {FRONTEND_SCHEMA}.conversations WHERE id = %s AND user_id = %s",
                (conversation_str, user_id),
            )
            if cur.fetchone() is None:
                raise ConversationAccessError(conversation_str)
            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.messages
                    (id, conversation_id, role, content, sql_code, query_context, created_at)
                VALUES (%s, %s, 'assistant', %s, %s, %s, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    conversation_str,
                    response,
                    sql_code,
                    Jsonb(query_context) if query_context is not None else None,
                ),
            )
            cur.execute(
                f"""
                UPDATE {FRONTEND_SCHEMA}.conversation_analytics
                SET response = %s, sql = %s, response_timestamp = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (response, sql_code, analytics_id, user_id),
            )
            cur.execute(
                f"UPDATE {FRONTEND_SCHEMA}.conversations SET updated_at = NOW() WHERE id = %s",
                (conversation_str,),
            )


def persist_result_message(
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    content: str,
    sql_response: str | None,
    query_context: dict[str, Any] | None = None,
) -> None:
    """Persist the chart/table bubble ("Message 2") for a completed turn.

    Called unconditionally from ``_pump`` so this bubble is guaranteed to
    exist — same guarantee ``persist_assistant_result`` already gives the
    SQL answer — instead of depending on a browser tab staying around to
    ask for it.
    """

    conversation_str = str(conversation_id)
    with psycopg.connect(get_postgres_connection_string(), connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {FRONTEND_SCHEMA}.conversations "
                "WHERE id = %s AND user_id = %s",
                (conversation_str, user_id),
            )
            if cur.fetchone() is None:
                raise ConversationAccessError(conversation_str)
            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.messages
                    (id, conversation_id, role, content, sql_response, query_context, created_at)
                VALUES (%s, %s, 'assistant', %s, %s, %s, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    conversation_str,
                    content,
                    sql_response,
                    Jsonb(query_context) if query_context is not None else None,
                ),
            )
            cur.execute(
                f"UPDATE {FRONTEND_SCHEMA}.conversations "
                "SET updated_at = NOW() WHERE id = %s",
                (conversation_str,),
            )


def create_stateless_analytics(*, user_id: str, question: str, source: str) -> str:
    """Create analytics for an authenticated one-shot request."""

    analytics_id = str(uuid.uuid4())
    with psycopg.connect(get_postgres_connection_string(), connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {FRONTEND_SCHEMA}.conversation_analytics
                    (id, user_id, source, question, question_timestamp)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (analytics_id, user_id, source, question),
            )
    return analytics_id


def persist_analytics_result(
    *,
    analytics_id: str,
    user_id: str,
    response: str,
    sql_code: str | None,
) -> None:
    """Complete an analytics row for a stateless authenticated request."""

    with psycopg.connect(get_postgres_connection_string(), connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {FRONTEND_SCHEMA}.conversation_analytics
                SET response = %s, sql = %s, response_timestamp = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (response, sql_code, analytics_id, user_id),
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
