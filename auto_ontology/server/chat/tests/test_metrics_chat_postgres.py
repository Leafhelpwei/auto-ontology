# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real PostgreSQL metric execution, owned conversation state and cancellation."""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from auto_ontology.connectors.postgres import PostgresDatabase
from auto_ontology.semantic.contracts.compiler import compile_query
from auto_ontology.semantic.contracts.models import MetricQueryRequest
from auto_ontology.server.chat import metrics_flow
from auto_ontology.server.chat.conversation_dal import (
    ConversationAccessError,
    prepare_conversation,
    persist_assistant_result,
    persist_result_message,
)
from auto_ontology.server.chat.worker import PrewarmedWorker
from auto_ontology.server.metrics.interpretation import InterpretationResult
from auto_ontology.server.metrics.tests.test_governance import (
    catalog as _real_catalog,
    publish,
)

catalog = _real_catalog


@pytest.fixture
def chat_user():
    dsn = os.environ.get("AUTO_ONTOLOGY_GOVERNED_TEST_DSN")
    if not dsn:
        pytest.skip("Requires migrated PostgreSQL and Prisma frontend schema")
    user_id = f"metric-chat-{uuid4().hex}"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            'INSERT INTO frontend."user" (id,name,email,role,"updatedAt") VALUES (%s,%s,%s,%s,NOW())',
            (user_id, user_id, f"{user_id}@example.test", "admin"),
        )
    yield user_id, dsn
    with psycopg.connect(dsn) as conn:
        conn.execute('DELETE FROM frontend."user" WHERE id=%s', (user_id,))


def test_metric_clarification_execution_and_owned_context_persist(
    catalog, chat_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, _, _, _ = catalog
    user_id, dsn = chat_user
    publish(model)
    conversation_id = uuid4()
    scope = {
        "version": 1,
        "query_mode": "metrics",
        "model_id": model.model_id,
        "revision": 1,
        "selected_metric_ids": ["order_count", "amount"],
    }
    first = prepare_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        question="订单数量和金额",
        source="test",
        query_context=scope,
    )
    monkeypatch.setattr(
        metrics_flow,
        "interpret_question",
        lambda *a, **k: InterpretationResult(
            status="clarification",
            message="请选择时间范围",
            code="clarification_required",
            selected_metric_ids=("order_count", "amount"),
        ),
    )
    answer = list(
        metrics_flow.stream_metric_response(
            {
                "question": "订单数量和金额",
                "metric_context": scope,
                "user_id": user_id,
                "persistent": True,
                "turn_id": first.analytics_id,
                "conversation_history": [],
            }
        )
    )[-1]["answer"]
    assert answer["query_context"]["status"] == "clarification"
    assert "sql_code" not in answer
    persist_assistant_result(
        conversation_id=conversation_id,
        user_id=user_id,
        analytics_id=first.analytics_id,
        response=answer["response"],
        sql_code=None,
        query_context=answer["query_context"],
    )
    second = prepare_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        question="全部时间",
        source="test",
        query_context=scope,
    )
    assert second.history[0].query_context["turn_id"] == first.analytics_id
    query = MetricQueryRequest(metric_ids=("order_count", "amount"), all_time=True)
    plan = compile_query(model, query)
    captured = {}

    def interpret(*args, **kwargs):
        captured.update(kwargs)
        return InterpretationResult(
            status="ready", query=query, plan=plan, selected_metric_ids=query.metric_ids
        )

    monkeypatch.setattr(metrics_flow, "interpret_question", interpret)
    answer = list(
        metrics_flow.stream_metric_response(
            {
                "question": "全部时间",
                "metric_context": scope,
                "user_id": user_id,
                "persistent": True,
                "turn_id": second.analytics_id,
                "conversation_history": [vars(turn) for turn in second.history],
            }
        )
    )[-1]["answer"]
    assert '"amount": "350"' in answer["sql_response_from_db"]
    assert answer["query_context"]["trusted"] is True
    assert captured["context"].turns[0].content == "订单数量和金额"
    persist_assistant_result(
        conversation_id=conversation_id,
        user_id=user_id,
        analytics_id=second.analytics_id,
        response=answer["response"],
        sql_code=answer["sql_code"],
        query_context=answer["query_context"],
    )
    persist_result_message(
        conversation_id=conversation_id,
        user_id=user_id,
        content="",
        sql_response=answer["sql_response_from_db"],
        query_context=answer["query_context"],
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        messages = conn.execute(
            "SELECT role,query_context,sql_response FROM frontend.messages WHERE conversation_id=%s ORDER BY created_at,id",
            (conversation_id,),
        ).fetchall()
    assert len(messages) == 5
    assert (
        messages[-1]["query_context"]["plan"]["model_fingerprint"]
        == model.fingerprint()
    )
    assert messages[-1]["query_context"]["turn_id"] == second.analytics_id
    with pytest.raises(ConversationAccessError):
        prepare_conversation(
            conversation_id=conversation_id,
            user_id="another-user",
            question="steal context",
            source="test",
            query_context=scope,
        )


def _long_read(dsn: str, output) -> None:
    connector = PostgresDatabase(dsn)
    with connector.read_transaction(timeout_s=30) as read:
        output.put(int(read("SELECT pg_backend_pid() AS pid", None).iloc[0, 0]))
        read("SELECT pg_sleep(20)", None)


def test_killing_metric_process_stops_actual_postgres_query(chat_user) -> None:
    _, dsn = chat_user
    with psycopg.connect(dsn, autocommit=True) as observer:
        if observer.info.server_version < 140000:
            pytest.skip("Immediate client-disconnect detection requires PostgreSQL 14+")
        ctx = mp.get_context("spawn")
        output = ctx.Queue()
        process = ctx.Process(target=_long_read, args=(dsn, output))
        process.start()
        try:
            pid = output.get(timeout=15)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                active = observer.execute(
                    "SELECT state='active' AND query LIKE 'SELECT pg_sleep%%' FROM pg_stat_activity WHERE pid=%s",
                    (pid,),
                ).fetchone()
                if active and active[0]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("The real PostgreSQL long query never started")
            worker = PrewarmedWorker.__new__(PrewarmedWorker)
            worker._proc = process
            began = time.monotonic()
            worker.kill()
            while time.monotonic() - began < 3:
                row = observer.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE pid=%s AND state='active'",
                    (pid,),
                ).fetchone()
                if row is None:
                    break
                time.sleep(0.05)
            assert row is None, (
                "PostgreSQL kept executing after its Chat worker was killed"
            )
            assert time.monotonic() - began < 3
        finally:
            if process.is_alive():
                process.kill()
            process.join(timeout=3)
            output.close()
