# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve contextual chat follow-ups before the text-to-SQL graph runs."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from auto_ontology.retrieval.text_to_sql.models import FollowUpResolutionModel
from auto_ontology.retrieval.text_to_sql.prompts import (
    create_follow_up_resolution_prompt,
)
from auto_ontology.utils.llm_invoke import invoke_with_structured_output

logger = logging.getLogger(__name__)


def _format_history(history: Sequence[Mapping[str, str | None]]) -> str:
    blocks: list[str] = []
    for index, turn in enumerate(history, start=1):
        sql = (turn.get("sql_code") or "").strip()
        block = (
            f"Turn {index}\n"
            f"User: {(turn.get('question') or '').strip()}\n"
            f"Assistant: {(turn.get('response') or '').strip()}"
        )
        if sql:
            block += f"\nFinal SQL: {sql}"
        blocks.append(block)
    return "\n\n".join(blocks)


def resolve_follow_up(
    *,
    question: str,
    history: Sequence[Mapping[str, str | None]],
    llm: BaseChatModel | None,
) -> tuple[bool, str]:
    """Return ``(is_follow_up, processing_question)`` with safe fallback."""

    if not history:
        return False, question
    if llm is None:
        raise ValueError(
            "Conversation context resolver is unavailable; restate the complete question"
        )

    prompt = create_follow_up_resolution_prompt(
        question=question,
        conversation_history=_format_history(history),
    )
    try:
        result = invoke_with_structured_output(
            llm, [HumanMessage(content=prompt)], FollowUpResolutionModel
        )
    except Exception as exc:
        logger.warning(
            "Follow-up resolution unavailable error_class=%s", type(exc).__name__
        )
        raise ValueError(
            "Conversation context could not be resolved; restate the complete question"
        ) from None

    if result is None:
        raise ValueError(
            "Conversation context could not be resolved; restate the complete question"
        )

    standalone = result.standalone_question.strip()
    if not result.is_follow_up:
        # Enforce the contract locally instead of allowing an independent
        # question to be silently changed by the model.
        return False, question
    if not standalone:
        raise ValueError("Context resolution returned an empty question")
    return True, standalone


__all__ = ["resolve_follow_up"]
