# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Admission precedes worker allocation and cleanup is atomic under races."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock
from uuid import uuid4

from fastapi import HTTPException, Request
import pytest

from auto_ontology.server.chat import router
from auto_ontology.server.chat.helpers import ChatRequest
from auto_ontology.server.chat.worker import WorkerStartupError


@pytest.fixture(autouse=True)
def isolated_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router, "_active_slots", {})
    monkeypatch.setattr(router, "_pending_slots", {})
    monkeypatch.setattr(router, "_MAX_ACTIVE_RUNS", 4)
    monkeypatch.setattr(router, "_MAX_USER_RUNS", 2)
    monkeypatch.setattr(router, "require_metric_access", lambda *a, **k: None)


def slot(key: str = "u:conversation") -> router._Slot:
    return router._Slot(
        key=key,
        worker=Mock(),
        user_id="u",
        conversation_id=uuid4(),
        analytics_id=None,
        question="q",
        client_alive=threading.Event(),
        cancelled=threading.Event(),
        released=threading.Event(),
        query_mode="metrics",
    )


@pytest.mark.parametrize("finished", [False, True])
def test_conflict_never_allocates_or_replaces_worker(
    monkeypatch: pytest.MonkeyPatch,
    finished: bool,
) -> None:
    old = slot()
    old.key = f"u:{old.conversation_id}"
    if finished:
        old.finished.set()
    router._active_slots[old.key] = old
    get_pool = Mock()
    monkeypatch.setattr(router, "get_metrics_pool", get_pool)
    request = ChatRequest(
        question="new",
        conversation_id=old.conversation_id,
        query_mode="metrics",
        metric_context={"model_id": "m"},
    )
    http = Request({"type": "http", "headers": [(b"x-auto-ontology-user-id", b"u")]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.chat_completions(request, http))
    assert error.value.status_code == 409
    get_pool.assert_not_called()
    old.worker.kill.assert_not_called()


def test_admission_counts_pending_requests_per_user_and_globally() -> None:
    router._reserve_slot("a", "u")
    router._reserve_slot("b", "u")
    with pytest.raises(HTTPException) as error:
        router._reserve_slot("c", "u")
    assert error.value.status_code == 429
    router._reserve_slot("c", "v")
    router._reserve_slot("d", "v")
    with pytest.raises(HTTPException) as error:
        router._reserve_slot("e", "w")
    assert error.value.status_code == 429
    assert len(router._pending_slots) == 4


def test_startup_failure_removes_pending_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = Mock()
    pool.acquire.side_effect = WorkerStartupError("unavailable")
    monkeypatch.setattr(router, "get_metrics_pool", lambda: pool)
    request = ChatRequest(
        question="q", query_mode="metrics", metric_context={"model_id": "m"}
    )
    http = Request({"type": "http", "headers": [(b"x-auto-ontology-user-id", b"u")]})
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.chat_completions(request, http))
    assert error.value.status_code == 503
    assert router._pending_slots == router._active_slots == {}


def test_concurrent_release_returns_worker_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = slot()
    router._active_slots[current.key] = current
    pool = Mock()
    monkeypatch.setattr(router, "get_metrics_pool", lambda: pool)
    barrier = threading.Barrier(8)

    def release() -> None:
        barrier.wait()
        router._release(current)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(release) for _ in range(8)]
        for future in futures:
            future.result(timeout=3)
    pool.return_alive.assert_called_once_with(current.worker)
    assert not router._active_slots


def test_cancel_during_startup_recycles_late_worker() -> None:
    entered, proceed, returned = threading.Event(), threading.Event(), threading.Event()
    worker = Mock()
    pool = Mock()

    def acquire() -> Mock:
        entered.set()
        assert proceed.wait(3)
        return worker

    pool.acquire.side_effect = acquire
    pool.return_alive.side_effect = lambda _: returned.set()

    async def run() -> None:
        task = asyncio.create_task(router._acquire_worker(pool))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proceed.set()
        assert await asyncio.to_thread(returned.wait, 3)

    asyncio.run(run())
    pool.return_alive.assert_called_once_with(worker)
