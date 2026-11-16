# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Two actual HTTP replicas, shared PostgreSQL, and spawned cancellable workers."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import multiprocessing as mp
import os
import socket
import threading
import time
from typing import Any, Iterator
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import delete, func, select, text, update
from auto_ontology.dal import chat_runs, schema as s
from auto_ontology.dal.chat_history import messages
from auto_ontology.dal.session import store
from auto_ontology.server.chat.conversation_dal import (
    prepare_conversation,
    persist_assistant_result,
)


def _test_worker(incoming: Any, outgoing: Any) -> None:
    """Real IPC and cancellation; the fixture does not claim LLM quality."""
    from auto_ontology.server.chat import worker
    from auto_ontology.dal.connections import connection_generation

    generation = connection_generation()

    outgoing.put((worker._TAG_READY, None))
    while True:
        _, payload = incoming.get()
        question = payload["question"]
        outgoing.put(
            (
                worker._TAG_EVENT,
                {
                    "type": "step",
                    "message": question,
                    "node": "fixture",
                    "generation": generation,
                },
            )
        )
        if question.startswith("wait"):
            time.sleep(60)
        outgoing.put(
            (worker._TAG_EVENT, {"type": "result", "answer": {"response": question}})
        )
        outgoing.put((worker._TAG_DONE, None))


def _serve_replica(pipe: Any) -> None:
    from fastapi import FastAPI
    import uvicorn
    from auto_ontology.server.chat import router, worker
    from auto_ontology.server.chat.run_lifecycle import start_reaper, stop_reaper

    original = worker.PrewarmedWorker
    worker.PrewarmedWorker = lambda mode="metrics": original(
        mode, loop_override=_test_worker
    )
    pool = worker.WarmPool("metrics")
    router.get_metrics_pool = lambda: pool

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Iterator[None]:
        start_reaper()
        try:
            yield
        finally:
            stop_reaper()
            pool.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router.router, prefix="/api")

    @app.get("/ready")
    def ready() -> dict:
        return {"ready": True}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    pipe.send(sock.getsockname()[1])
    pipe.close()
    uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
        sockets=[sock]
    )


@pytest.fixture
def users() -> Iterator[tuple[str, str]]:
    if not os.getenv("AUTO_ONTOLOGY_GOVERNED_TEST_DSN"):
        pytest.skip("Requires isolated migrated PostgreSQL and frontend schema")
    ids = tuple("shared-run-" + uuid4().hex for _ in range(2))
    for user_id in ids:
        store().query_write(
            text(
                'INSERT INTO frontend."user" (id,name,email,role,"updatedAt") '
                "VALUES (:id,:id,:email,'admin',NOW())"
            ),
            {"id": user_id, "email": user_id + "@example.test"},
        )
    try:
        yield ids
    finally:
        store().query_write(delete(s.chat_run).where(s.chat_run.c.user_id.in_(ids)))
        for user_id in ids:
            store().query_write(
                text('DELETE FROM frontend."user" WHERE id=:id'), {"id": user_id}
            )


@pytest.fixture
def replicas(users: tuple[str, str]) -> Iterator[list[tuple[Any, str]]]:
    ctx = mp.get_context("spawn")
    instances = []
    try:
        for _ in range(2):
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(target=_serve_replica, args=(child,))
            process.start()
            child.close()
            assert parent.poll(20), "Replica did not bind a port"
            url = f"http://127.0.0.1:{parent.recv()}"
            parent.close()
            instances.append((process, url))
            deadline = time.monotonic() + 15
            while True:
                try:
                    if httpx.get(url + "/ready", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.05)
        yield instances
    finally:
        for process, _ in instances:
            if process.is_alive():
                process.terminate()
            process.join(8)
            if process.is_alive():
                process.kill()
                process.join(3)
            process.close()


def _headers(user: str) -> dict:
    return {"x-auto-ontology-user-id": user}


def _payload(conversation: str, question: str) -> dict:
    return {
        "conversation_id": conversation,
        "question": question,
        "query_mode": "metrics",
        "metric_context": {"model_id": "fixture"},
    }


def _events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_two_replicas_serialize_watch_cancel_and_replay(
    replicas: list, users: tuple[str, str]
) -> None:
    conversation = str(uuid4())
    barrier = threading.Barrier(2)
    clients = [httpx.Client(timeout=15) for _ in replicas]

    def submit(index: int) -> httpx.Response:
        barrier.wait()
        req = clients[index].build_request(
            "POST",
            replicas[index][1] + "/api/chat/completions",
            headers=_headers(users[0]),
            json=_payload(conversation, "wait-private-A"),
        )
        return clients[index].send(req, stream=True)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, range(2)))
        assert sorted(response.status_code for response in responses) == [200, 409]
        owner = next(
            i for i, response in enumerate(responses) if response.status_code == 200
        )
        other_url = replicas[1 - owner][1]
        run_id = responses[owner].headers["x-chat-run-id"]
        for response in responses:
            response.close()
        with httpx.stream(
            "GET",
            other_url + "/api/chat/watch",
            params={"conversation_id": conversation},
            headers=_headers(users[0]),
            timeout=15,
        ) as watch:
            assert watch.status_code == 200
            first_id = None
            for line in watch.iter_lines():
                if line.startswith("id: "):
                    first_id = line[4:]
                if line.startswith("data: "):
                    assert "wait-private-A" in line
                    break
        assert first_id == run_id + ":1"
        response = httpx.post(
            other_url + "/api/chat/cancel",
            params={"conversation_id": conversation},
            headers=_headers(users[1]),
        )
        assert response.json() == {"cancelled": False}
        response = httpx.post(
            other_url + "/api/chat/cancel",
            params={"conversation_id": conversation},
            headers=_headers(users[0]),
        )
        assert response.json() == {"cancelled": True}
        watch = httpx.get(
            other_url + "/api/chat/watch",
            params={"conversation_id": conversation},
            headers={**_headers(users[0]), "last-event-id": first_id},
            timeout=15,
        )
        assert watch.status_code == 200
        assert "wait-private-A" not in watch.text
        assert _events(watch)[-1]["answer"]["query_context"]["status"] == "cancelled"
        assert watch.text.endswith("data: [DONE]\n\n")
        denied = httpx.get(
            other_url + "/api/chat/watch",
            params={"conversation_id": conversation},
            headers={**_headers(users[1]), "last-event-id": first_id},
        )
        assert denied.status_code == 404
        next_run = httpx.post(
            other_url + "/api/chat/completions",
            headers=_headers(users[0]),
            json=_payload(conversation, "B-only"),
            timeout=15,
        )
        assert next_run.status_code == 200
        assert next_run.headers["x-chat-run-id"] != run_id
        assert "wait-private-A" not in next_run.text
        assert _events(next_run)[-1]["answer"]["response"] == "B-only"
        rows = store().query_read(
            select(messages).where(messages.c.conversation_id == conversation)
        )
        assert sum(row["role"] == "user" for row in rows) == 2
        assert sum(row["role"] == "assistant" for row in rows) == 2
    finally:
        for client in clients:
            client.close()


def test_dead_owner_is_interrupted_and_cannot_write_a_late_answer(
    replicas: list, users: tuple[str, str]
) -> None:
    conversation = str(uuid4())
    with httpx.stream(
        "POST",
        replicas[0][1] + "/api/chat/completions",
        headers=_headers(users[0]),
        json=_payload(conversation, "wait-owner-death"),
        timeout=15,
    ) as response:
        assert response.status_code == 200
        run_id = response.headers["x-chat-run-id"]
    row = store().query_read(select(s.chat_run).where(s.chat_run.c.id == run_id))[0]
    replicas[0][0].kill()
    replicas[0][0].join(3)
    recovered = httpx.get(
        replicas[1][1] + "/api/chat/watch",
        params={"conversation_id": conversation},
        headers=_headers(users[0]),
        timeout=chat_runs.LEASE_SECONDS + 12,
    )
    assert _events(recovered)[-1]["answer"]["query_context"]["status"] == "interrupted"
    with pytest.raises(chat_runs.LeaseLost):
        with chat_runs.owned_transaction(chat_runs.Ticket(run_id, row["owner_token"])):
            persist_assistant_result(
                conversation_id=conversation,
                user_id=users[0],
                analytics_id=row["analytics_id"],
                response="STALE_SECRET",
                sql_code=None,
            )
    assert not store().query_read(
        select(messages).where(messages.c.content == "STALE_SECRET")
    )
    accepted = httpx.post(
        replicas[1][1] + "/api/chat/completions",
        headers=_headers(users[0]),
        json=_payload(conversation, "fresh-after-recovery"),
        timeout=15,
    )
    assert accepted.status_code == 200
    assert _events(accepted)[-1]["answer"]["response"] == "fresh-after-recovery"


def test_shared_quotas_and_terminal_event_budget(
    users: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def reserve(index: int) -> chat_runs.Ticket | int:
        try:
            return chat_runs.reserve(
                user_id=users[index % 2],
                conversation_id=None,
                query_mode="metrics",
                owner_id=str(index),
                global_limit=3,
                user_limit=2,
            )
        except chat_runs.AdmissionDenied as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(reserve, range(24)))
    tickets = [x for x in outcomes if isinstance(x, chat_runs.Ticket)]
    assert len(tickets) == 3
    assert [x for x in outcomes if isinstance(x, int)] == [429] * 21
    ticket = tickets[0]
    monkeypatch.setattr(chat_runs, "MAX_EVENTS", 4)
    for _ in range(3):
        chat_runs.append(ticket, {"type": "step", "message": "ok"})
    with pytest.raises(chat_runs.EventBudgetExceeded):
        chat_runs.append(ticket, {"type": "step", "message": "over"})
    chat_runs.finish(ticket, "failed", message="Result budget exceeded")
    row = store().query_read(
        select(s.chat_run).where(s.chat_run.c.id == ticket.run_id)
    )[0]
    assert row["status"] == "failed" and row["event_count"] == 4


def test_expiry_fences_history_and_retains_terminal_replay(
    users: tuple[str, str],
) -> None:
    conversation = str(uuid4())
    ticket = chat_runs.reserve(
        user_id=users[0],
        conversation_id=conversation,
        query_mode="metrics",
        owner_id="old",
        global_limit=4,
        user_limit=2,
    )
    with chat_runs.owned_transaction(ticket):
        prepared = prepare_conversation(
            conversation_id=conversation, user_id=users[0], question="q", source="test"
        )
        chat_runs.attach_analytics(ticket, prepared.analytics_id)
    store().query_write(
        update(s.chat_run)
        .where(s.chat_run.c.id == ticket.run_id)
        .values(lease_until=func.now() - timedelta(seconds=1))
    )
    with pytest.raises(chat_runs.LeaseLost):
        chat_runs.append(ticket, {"type": "result", "answer": {"response": "late"}})
    assert chat_runs.reap_expired() >= 1
    events, status = chat_runs.replay(run_id=ticket.run_id, user_id=users[0])
    assert status == "interrupted" and len(events) == 1
    with pytest.raises(chat_runs.AdmissionDenied):
        chat_runs.replay(run_id=ticket.run_id, user_id=users[1])
    new = chat_runs.reserve(
        user_id=users[0],
        conversation_id=conversation,
        query_mode="metrics",
        owner_id="new",
        global_limit=4,
        user_limit=2,
    )
    chat_runs.finish(new, "succeeded")
    assert chat_runs.replay(run_id=ticket.run_id, user_id=users[0])[0] == events


@pytest.mark.parametrize("mutation", ["rotation", "delete"])
def test_durable_generation_retires_busy_and_other_replica_standby(
    users: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from auto_ontology.server.chat import worker
    from auto_ontology.dal.connections import (
        insert_connection,
        clear_connection,
        connection_generation,
    )

    name = "revision-fixture-" + uuid4().hex
    insert_connection(
        database_name=name,
        connection=json.dumps(
            {"database": name, "type": "postgres", "password": "old"}
        ),
    )
    original = worker.PrewarmedWorker
    monkeypatch.setattr(
        worker,
        "PrewarmedWorker",
        lambda mode="metrics": original(mode, loop_override=_test_worker),
    )
    pools = [worker.WarmPool("metrics"), worker.WarmPool("metrics")]
    try:
        old = pools[0].acquire()
        old.submit("before", user_id=users[0])
        before = connection_generation()
        if mutation == "rotation":
            insert_connection(
                database_name=name,
                connection=json.dumps(
                    {"database": name, "type": "postgres", "password": "new"}
                ),
            )
        else:
            clear_connection(database_name=name)
        after = connection_generation()
        assert after > before
        assert [event for event in old.events() if event][0]["generation"] == before
        pools[0].return_alive(old)
        assert not old.is_alive()
        for pool in pools:
            fresh = pool.acquire()
            fresh.submit("after", user_id=users[0])
            assert [event for event in fresh.events() if event][0][
                "generation"
            ] == after
            pool.return_alive(fresh)
    finally:
        for pool in pools:
            pool.shutdown()
        store().query_write(
            delete(s.catalog_database).where(s.catalog_database.c.name == name)
        )


def test_event_failure_rolls_back_history_and_retention_expires_replay(
    users: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = str(uuid4())
    ticket = chat_runs.reserve(
        user_id=users[0],
        conversation_id=conversation,
        query_mode="metrics",
        owner_id="test",
        global_limit=4,
        user_limit=2,
    )
    with chat_runs.owned_transaction(ticket):
        prepared = prepare_conversation(
            conversation_id=conversation, user_id=users[0], question="q", source="test"
        )
        chat_runs.attach_analytics(ticket, prepared.analytics_id)
    with pytest.raises(chat_runs.EventBudgetExceeded):
        with chat_runs.owned_transaction(ticket):
            persist_assistant_result(
                conversation_id=conversation,
                user_id=users[0],
                analytics_id=prepared.analytics_id,
                response="must-rollback",
                sql_code=None,
            )
            chat_runs.append(
                ticket,
                {"type": "result", "value": "x" * (chat_runs.MAX_EVENT_BYTES + 1)},
            )
    assert not store().query_read(
        select(messages).where(messages.c.content == "must-rollback")
    )
    chat_runs.finish(ticket, "failed", message="budget exceeded")
    assert chat_runs.replay(run_id=ticket.run_id, user_id=users[0])[1] == "failed"
    monkeypatch.setattr(chat_runs, "RETENTION_SECONDS", 1)
    store().query_write(
        update(s.chat_run)
        .where(s.chat_run.c.id == ticket.run_id)
        .values(finished_at=func.now() - timedelta(seconds=2))
    )
    chat_runs.reap_expired()
    with pytest.raises(chat_runs.AdmissionDenied):
        chat_runs.replay(run_id=ticket.run_id, user_id=users[0])


def test_late_failure_keeps_answer_and_persists_the_same_notice_as_replay(
    users: tuple[str, str],
) -> None:
    conversation = str(uuid4())
    ticket = chat_runs.reserve(
        user_id=users[0],
        conversation_id=conversation,
        query_mode="metrics",
        owner_id="test",
        global_limit=4,
        user_limit=2,
    )
    with chat_runs.owned_transaction(ticket):
        prepared = prepare_conversation(
            conversation_id=conversation, user_id=users[0], question="q", source="test"
        )
        chat_runs.attach_analytics(ticket, prepared.analytics_id)
        persist_assistant_result(
            conversation_id=conversation,
            user_id=users[0],
            analytics_id=prepared.analytics_id,
            response="actual answer",
            sql_code="SELECT 1",
        )
        chat_runs.append(
            ticket,
            {
                "type": "result",
                "answer": {"response": "actual answer"},
                "query_context": {"model_id": "fixture", "plan": "x" * 30000},
            },
        )
    chat_runs.finish(ticket, "failed", message="chart step failed")
    events, status = chat_runs.replay(run_id=ticket.run_id, user_id=users[0])
    assert status == "failed"
    assert events[-1]["event"]["partial_result_retained"]
    assert events[-1]["event"]["type"] == "error"
    assert "plan" not in events[-1]["event"]["query_context"]
    rows = store().query_read(
        select(messages).where(
            messages.c.conversation_id == conversation, messages.c.role == "assistant"
        )
    )
    assert sorted(row["content"] for row in rows) == [
        "actual answer",
        "chart step failed",
    ]
