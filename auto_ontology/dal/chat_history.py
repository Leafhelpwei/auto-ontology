# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Core handles for Prisma-owned tables; excluded from Alembic metadata."""

from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

_frontend = MetaData(schema="frontend")
conversations = Table(
    "conversations",
    _frontend,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("user_id", Text),
    Column("title", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)
messages = Table(
    "messages",
    _frontend,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("conversation_id", Uuid(as_uuid=False)),
    Column("role", Text),
    Column("content", Text),
    Column("sql_code", Text),
    Column("sql_response", Text),
    Column("query_context", JSONB),
    Column("created_at", DateTime(timezone=True)),
)
analytics = Table(
    "conversation_analytics",
    _frontend,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("user_id", Text),
    Column("source", Text),
    Column("question", Text),
    Column("question_timestamp", DateTime(timezone=True)),
    Column("response", Text),
    Column("sql", Text),
    Column("response_timestamp", DateTime(timezone=True)),
)
