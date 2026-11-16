# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provider-neutral text chat messages without changing data trust levels."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


def text_content(value: object) -> str:
    """Extract visible text; never stringify reasoning or unsupported media."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {
                "text",
                "output_text",
            }:
                text = block.get("text")
                if not isinstance(text, str):
                    raise ValueError("Invalid text block")
                parts.append(text)
            elif isinstance(block, dict) and block.get("type") in {
                "reasoning",
                "thinking",
            }:
                continue
            else:
                raise ValueError("Only text chat content is supported")
        return "\n".join(parts)
    raise ValueError("Invalid chat content")


def normalize_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Produce at most one leading system message and preserve conversation order."""
    if not messages:
        raise ValueError("A chat request must contain a message")
    system: list[str] = []
    conversation: list[BaseMessage] = []
    for message in messages:
        if not isinstance(message, (SystemMessage, HumanMessage, AIMessage)):
            raise ValueError("Unsupported chat role")
        if getattr(message, "tool_calls", None) or getattr(
            message, "invalid_tool_calls", None
        ):
            raise ValueError("Tool conversation history is not supported")
        content = text_content(message.content)
        if not content.strip():
            raise ValueError("Empty chat content")
        if isinstance(message, SystemMessage):
            system.append(content)
        elif conversation and type(conversation[-1]) is type(message):
            conversation[-1] = type(message)(
                content=conversation[-1].content + "\n\n" + content
            )
        else:
            conversation.append(type(message)(content=content))
    if not conversation:
        conversation.append(
            HumanMessage(content="Please complete the task described above.")
        )
    prefix = [SystemMessage(content="\n\n".join(system))] if system else []
    return prefix + conversation
