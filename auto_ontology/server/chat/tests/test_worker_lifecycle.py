# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual spawned-process regressions for request correlation and lifecycle bounds."""

import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any
import pytest
from auto_ontology.server.chat import worker as module


def echo_loop(incoming: Any, outgoing: Any) -> None:
    outgoing.put((module._TAG_READY, None))
    while True:
        _, payload = incoming.get()
        if isinstance(payload, dict):
            question, identity = payload["question"], payload.get("user_id")
        else:
            question, identity = payload[0], payload[3]
        if question in {"error", "delayed-error"}:
            # Simulate the original broken child that reports ERROR then DONE and
            # continues running. The production parent must kill it before reuse.
            outgoing.put((module._TAG_ERROR, "private exception content"))
            if question == "delayed-error":
                time.sleep(0.1)
            outgoing.put((module._TAG_DONE, None))
            continue
        if question == "exit":
            os._exit(9)
        if question == "wait":
            time.sleep(30)
        if question == "oversized":
            try:
                outgoing.put(
                    (
                        module._TAG_EVENT,
                        {"type": "result", "value": "x" * (module._MAX_IPC_BYTES + 1)},
                    )
                )
            except RuntimeError:
                outgoing.put((module._TAG_ERROR, "event budget exceeded"))
                return
        else:
            outgoing.put(
                (
                    module._TAG_EVENT,
                    {"type": "result", "question": question, "identity": identity},
                )
            )
        outgoing.put((module._TAG_DONE, None))


def never_ready(incoming: Any, outgoing: Any) -> None:
    time.sleep(30)


def ready_worker(mode: str = "explore") -> module.PrewarmedWorker:
    worker = module.PrewarmedWorker(mode, loop_override=echo_loop)
    assert worker.drain_until_ready(5)
    return worker


@pytest.mark.parametrize("mode", ["explore", "metrics"])
@pytest.mark.parametrize("error_question", ["error", "delayed-error"])
def test_distinct_request_identity_and_error_poisoning(
    mode: str, error_question: str
) -> None:
    first = ready_worker(mode)
    try:
        first.submit("A", subject_token="token-A", user_id="user-A")
        events = [event for event in first.events() if event]
        assert events == [
            {
                "type": "result",
                "question": "A",
                "identity": "user-A" if mode == "metrics" else "token-A",
            }
        ]
        assert first.reusable()
        first.submit(error_question, subject_token="token-A", user_id="user-A")
        with pytest.raises(RuntimeError, match="execution failed"):
            list(first.events())
        assert not first.is_alive()
        with pytest.raises(RuntimeError):
            first.submit("B")
        second = ready_worker(mode)
        try:
            for name in ["B", "C"]:
                second.submit(
                    name, subject_token="token-" + name, user_id="user-" + name
                )
                events = [event for event in second.events() if event]
                assert events == [
                    {
                        "type": "result",
                        "question": name,
                        "identity": ("user-" if mode == "metrics" else "token-") + name,
                    }
                ]
        finally:
            second.kill()
    finally:
        first.kill()


def test_mismatched_old_event_never_reaches_next_request() -> None:
    worker = ready_worker()
    try:
        worker.submit("wait")
        worker._out_q.put(
            (
                module._TAG_EVENT,
                "old-request-id",
                {"type": "result", "private": "OLD_SENTINEL"},
            )
        )
        with pytest.raises(RuntimeError, match="another request"):
            list(worker.events())
        assert not worker.is_alive()
    finally:
        worker.kill()


@pytest.mark.parametrize("question", ["exit", "oversized"])
def test_dead_or_oversized_worker_fails_instead_of_returning_empty_success(
    question: str,
) -> None:
    worker = ready_worker()
    try:
        worker.submit(question)
        with pytest.raises(RuntimeError):
            list(worker.events())
        assert not worker.reusable()
    finally:
        worker.kill()


def test_startup_deadline_kills_and_returns_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_PROCESS_PERMITS", threading.BoundedSemaphore(1))
    worker = module.PrewarmedWorker(loop_override=never_ready)
    started = time.monotonic()
    assert not worker.drain_until_ready(0.05)
    assert time.monotonic() - started < 3
    assert not worker.is_alive()
    replacement = ready_worker()
    replacement.kill()


def test_capacity_rejection_happens_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_PROCESS_PERMITS", threading.BoundedSemaphore(1))
    worker = ready_worker()
    try:
        before = {process.pid for process in mp.active_children()}
        for _ in range(50):
            with pytest.raises(module.WorkerBusy):
                module.PrewarmedWorker(loop_override=echo_loop)
        assert {process.pid for process in mp.active_children()} == before
    finally:
        worker.kill()


def test_request_deadline_kills_a_hung_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_REQUEST_TIMEOUT_S", 0.05)
    worker = ready_worker()
    try:
        worker.submit("wait")
        with pytest.raises(RuntimeError, match="deadline"):
            list(worker.events())
        assert not worker.is_alive()
    finally:
        worker.kill()


def startup_error(incoming: Any, outgoing: Any) -> None:
    outgoing.put((module._TAG_ERROR, "cannot initialize"))


@pytest.mark.parametrize("failure", ["error", "killed"])
def test_ready_error_or_death_is_rejected_and_capacity_recovers(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(module, "_PROCESS_PERMITS", threading.BoundedSemaphore(1))
    worker = module.PrewarmedWorker(
        loop_override=startup_error if failure == "error" else never_ready
    )
    if failure == "killed":
        worker._proc.kill()
        worker._proc.join(2)
    assert not worker.drain_until_ready(5)
    assert not worker.is_alive()
    ready_worker().kill()


def test_worker_survives_the_thread_that_spawned_it() -> None:
    result = []
    thread = threading.Thread(target=lambda: result.append(ready_worker()))
    thread.start()
    thread.join(6)
    assert not thread.is_alive()
    worker = result[0]
    try:
        worker.submit("after-thread-exit")
        assert [event["question"] for event in worker.events() if event] == [
            "after-thread-exit"
        ]
    finally:
        worker.kill()


def supervisor(output: Any) -> None:
    worker = ready_worker()
    output.put(worker._proc.pid)
    time.sleep(60)


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux pidfd lifecycle check")
def test_parent_sigkill_does_not_leave_running_orphan() -> None:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    parent = ctx.Process(target=supervisor, args=(queue,))
    parent.start()
    try:
        child_pid = queue.get(timeout=8)
        os.kill(parent.pid, signal.SIGKILL)
        parent.join(3)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
                if state == "Z":
                    break
            except FileNotFoundError:
                break
            time.sleep(0.05)
        else:
            os.kill(child_pid, signal.SIGKILL)
            pytest.fail("Worker survived parent death")
    finally:
        if parent.is_alive():
            parent.kill()
        parent.join(3)
        parent.close()
        queue.close()
        queue.join_thread()


def test_killed_workers_release_pipe_and_process_descriptors() -> None:
    # Initialize multiprocessing's one persistent resource tracker first.
    ready_worker().kill()
    baseline = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(4):
        worker = ready_worker()
        worker.submit("q")
        list(worker.events())
        worker.kill()
        worker.kill()
    assert len(list(Path("/proc/self/fd").iterdir())) <= baseline + 2


def test_refresh_prevents_leased_worker_from_reentering_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = module.PrewarmedWorker
    monkeypatch.setattr(
        module,
        "PrewarmedWorker",
        lambda mode="explore": original(mode, loop_override=echo_loop),
    )
    pool = module.WarmPool(generation_reader=lambda: 0)
    try:
        worker = pool.acquire()
        pool.refresh()
        pool.return_alive(worker)
        assert not worker.is_alive()
        fresh = pool.acquire()
        assert fresh is not worker
        pool.return_alive(fresh)
        pool.return_alive(fresh)
        assert fresh.is_alive(), "duplicate release must not kill a returned standby"
    finally:
        pool.shutdown()


def test_pool_warmup_failure_does_not_start_a_second_full_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = module.PrewarmedWorker
    monkeypatch.setattr(module, "_STARTUP_TIMEOUT_S", 0.05)
    workers = []

    def create(mode: str = "explore") -> module.PrewarmedWorker:
        worker = original(mode, loop_override=never_ready)
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "PrewarmedWorker", create)
    pool = module.WarmPool(generation_reader=lambda: 0)
    try:
        with pool._condition:
            assert pool._condition.wait_for(lambda: not pool._warming, timeout=4)
        with pytest.raises(module.WorkerStartupError):
            pool.acquire()
        assert len(workers) == 1
        assert not workers[0].is_alive()
    finally:
        pool.shutdown()


def test_shutdown_rejects_a_worker_finishing_warmup_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = module.PrewarmedWorker
    created, proceed = threading.Event(), threading.Event()
    workers = []

    def create(mode: str = "explore") -> module.PrewarmedWorker:
        worker = original(mode, loop_override=echo_loop)
        workers.append(worker)
        created.set()
        assert proceed.wait(3)
        return worker

    monkeypatch.setattr(module, "PrewarmedWorker", create)
    pool = module.WarmPool(generation_reader=lambda: 0)
    try:
        assert created.wait(3)
        pool.shutdown()
        proceed.set()
        with pool._condition:
            assert pool._condition.wait_for(lambda: not pool._warming, timeout=8)
        assert pool._standby is None
        assert not workers[0].is_alive()
        with pytest.raises(module.WorkerBusy):
            pool.acquire()
    finally:
        proceed.set()
        pool.shutdown()
