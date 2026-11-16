# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded subprocess workers with request-scoped messages and poisoned-worker eviction."""

from __future__ import annotations

import logging
import json
import select
import time
import uuid
import weakref
import multiprocessing as mp
import os
import signal
import threading
from queue import Empty
from typing import Any, Generator, Callable

logger = logging.getLogger(__name__)

# Wire-format tags. ``ready`` is sent once after imports + retriever/connector
# init complete, so the pool can tell a warming worker from a fully usable
# one (the parent doesn't strictly *need* to wait — submit() will just block
# on the worker's in_queue.get() until the loop runs — but knowing readiness
# helps with logging/health checks).
_TAG_READY = "ready"
_TAG_EVENT = "event"
_TAG_DONE = "done"
_TAG_ERROR = "error"

# Inbound message tags (parent → worker).
_MSG_ASK = "ask"

# How often events() polls the outbound queue. Doubles as the heartbeat
# cadence the router uses to write SSE comments.
_QUEUE_POLL_S = 0.25

# Allows a one-second MySQL control-connection timeout before hard termination.
_TERMINATE_GRACE_S = 2.0


def _terminate_worker(signum: int, _frame: Any) -> None:
    """Stop owned MySQL server work before the worker loses its sockets."""
    try:
        from auto_ontology.connectors.mysql import cancel_active_mysql_queries

        cancel_active_mysql_queries()
    finally:
        # Worker loops intentionally catch BaseException. SystemExit here
        # would therefore resume the loop instead of actually terminating.
        os._exit(128 + signum)


class WorkerBusy(RuntimeError):
    """No capacity is available before spawning another process."""


class WorkerStartupError(RuntimeError):
    """A worker failed its bounded startup handshake."""


_MAX_WORKERS = max(1, int(os.getenv("CHAT_MAX_WORKERS", "4")))
_PROCESS_PERMITS = threading.BoundedSemaphore(_MAX_WORKERS)
_STARTUP_TIMEOUT_S = float(os.getenv("CHAT_WORKER_STARTUP_TIMEOUT_S", "60"))
_REQUEST_TIMEOUT_S = float(os.getenv("CHAT_REQUEST_TIMEOUT_S", "180"))
_MAX_IPC_BYTES = int(os.getenv("CHAT_MAX_EVENT_BYTES", "2097152"))
_POOLS: weakref.WeakSet = weakref.WeakSet()
_POOLS_LOCK = threading.Lock()


class _RequestInput:
    def __init__(self, queue: Any, context: dict[str, str | None]) -> None:
        self.queue, self.context = queue, context

    def get(self) -> tuple[str, Any]:
        frame = self.queue.get()
        if not isinstance(frame, tuple) or len(frame) != 3:
            raise EOFError("Invalid request envelope")
        tag, request_id, payload = frame
        if tag != _MSG_ASK or not isinstance(request_id, str) or not request_id:
            raise EOFError("Invalid request identifier")
        self.context["request_id"] = request_id
        return tag, payload


class _RequestOutput:
    def __init__(self, queue: Any, context: dict[str, str | None]) -> None:
        self.queue, self.context = queue, context

    def put(self, item: tuple[str, Any]) -> None:
        tag, payload = item
        if len(json.dumps(payload, default=str).encode()) > _MAX_IPC_BYTES:
            raise RuntimeError("Worker event byte budget exceeded")
        self.queue.put((tag, self.context["request_id"], payload))


def _watch_parent(parent_pid: int) -> None:
    """pidfd avoids the thread-parent trap of PR_SET_PDEATHSIG and PID reuse."""
    descriptor = None
    try:
        try:
            descriptor = os.pidfd_open(parent_pid)
        except (AttributeError, OSError):
            pass
        while True:
            if descriptor is not None:
                if select.select([descriptor], [], [], 0.5)[0]:
                    break
            elif os.getppid() != parent_pid:
                break
            else:
                time.sleep(0.5)
        # A dead parent cannot enforce the usual SIGKILL grace period.
        timer = threading.Timer(_TERMINATE_GRACE_S, os._exit, args=(143,))
        timer.daemon = True
        timer.start()
        _terminate_worker(signal.SIGTERM, None)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _worker_entrypoint(
    in_q: Any,
    out_q: Any,
    mode: str,
    parent_pid: int | None = None,
    loop_override: Callable | None = None,
) -> None:
    signal.signal(signal.SIGTERM, _terminate_worker)
    if parent_pid is not None:
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    context: dict[str, str | None] = {"request_id": None}
    loop = loop_override or (
        _metrics_worker_loop if mode == "metrics" else _worker_loop
    )
    loop(_RequestInput(in_q, context), _RequestOutput(out_q, context))


def _worker_loop(
    in_q: "mp.Queue[tuple[str, Any]]",
    out_q: "mp.Queue[tuple[str, Any]]",
) -> None:
    """Subprocess entry: import once, then process ``ask`` messages forever."""

    # `multiprocessing` with the `spawn` start method does NOT inherit the
    # parent's logging configuration — the child runs straight into this
    # function, never executing the parent's `logging.basicConfig` call in
    # `auto_ontology.server.__main__`. Without re-applying it here, INFO-level logs
    # from the agent ("Final answer (Xs):", per-step traces, etc.) are
    # silently dropped because Python's default root level is WARNING.
    # Subprocess stdout/stderr are inherited from the parent, so once
    # configured, log lines flow into the same terminal as everything else.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        from auto_ontology.retrieval.text_to_sql.main import (
            stream_agent_response,
        )
        from auto_ontology.retrieval.text_to_sql.follow_up import resolve_follow_up

        from auto_ontology.connectors import get_connectors
        from auto_ontology.utils.llm_invoke import (
            get_non_reasoning_llm_client,
        )
        from auto_ontology.utils import (
            get_data_objects_retriever,
            get_semantic_objects_retriever,
        )
        from auto_ontology.server.chat.settings_dal import (
            fetch_acronyms,
            fetch_custom_prompts,
        )

        # Eagerly build the heavyweights so they're hot before the first ask.
        data_retriever = get_data_objects_retriever()
        semantic_retriever = get_semantic_objects_retriever()
        connectors = get_connectors()
        try:
            follow_up_llm = get_non_reasoning_llm_client(max_tokens=1024)
        except Exception:
            # Missing configuration must not redirect conversation history.
            follow_up_llm = None
    except BaseException as exc:  # noqa: BLE001 — surface init failure to parent
        logger.error("Worker initialization failed error_class=%s", type(exc).__name__)
        try:
            out_q.put((_TAG_ERROR, "Worker initialization failed"))
            out_q.put((_TAG_DONE, None))
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        out_q.put((_TAG_READY, None))
    except Exception:  # noqa: BLE001
        return

    while True:
        try:
            message = in_q.get()
        except (EOFError, OSError):
            return
        if not isinstance(message, tuple) or len(message) != 2:
            continue
        tag, payload = message
        if tag != _MSG_ASK:
            continue

        (
            question,
            prediction,
            target_db,
            subject_token,
            conversation_history,
            evidence,
        ) = payload
        try:
            # Connections are resolved once at worker init. If that
            # lookup came back empty — the store not yet reachable when this
            # subprocess booted, or the first connection created afterwards —
            # the snapshot would stay empty for the life of the process and
            # every question would fail with "missing required 'connectors'"
            # until the pod restarted. Re-resolve lazily so the worker heals
            # itself; get_connectors() caches a non-empty result and only
            # retries while there is nothing to cache, so this costs nothing
            # on the normal path.
            #
            # This has to run BEFORE ``ask_connectors`` is bound: binding first
            # would capture the stale empty list, and rebinding ``connectors``
            # here would not update it, leaving the healing ineffective.
            if not connectors:
                connectors = get_connectors()

            # Per-user Databricks auth trades the prewarmed connectors for ones
            # bound to the caller's exchanged token. Databricks connectors open
            # connections per operation, so rebuilding them here is cheap.
            from auto_ontology.connectors.databricks_oauth import (
                any_connection_uses_sso_federation,
            )

            if subject_token is not None or any_connection_uses_sso_federation():
                from auto_ontology.connectors.registry import (
                    get_connectors_for_subject_token,
                )

                ask_connectors = get_connectors_for_subject_token(subject_token)
            else:
                ask_connectors = connectors

            is_follow_up, processing_question = resolve_follow_up(
                question=question,
                history=conversation_history,
                llm=follow_up_llm,
            )
            logger.info(
                "Conversation context resolution: follow_up=%s question_chars=%d",
                is_follow_up,
                len(processing_question),
            )
            custom_prompts = fetch_custom_prompts()

            agent_payload = {
                "question": question,
                "evidence": evidence or "",
                "processing_question": processing_question,
                "prediction": prediction,
                "data_retriever": data_retriever,
                "semantic_retriever": semantic_retriever,
                "connectors": ask_connectors,
                "acronyms": fetch_acronyms(),
                "custom_prompts": custom_prompts,
            }
            if target_db:
                agent_payload["target_db"] = target_db
            for event in stream_agent_response(agent_payload):
                out_q.put((_TAG_EVENT, event))
        except BaseException as exc:  # noqa: BLE001 — surface to parent
            logger.error("Agent run failed error_class=%s", type(exc).__name__)
            try:
                out_q.put((_TAG_ERROR, "Agent execution failed"))
            except Exception:  # noqa: BLE001
                pass
            return
        else:
            try:
                out_q.put((_TAG_DONE, None))
            except Exception:  # noqa: BLE001
                pass


def _metrics_worker_loop(
    in_q: "mp.Queue[tuple[str, Any]]", out_q: "mp.Queue[tuple[str, Any]]"
) -> None:
    """A cancellable worker that never initializes exploratory SQL prerequisites."""
    logging.basicConfig(level=logging.INFO)
    try:
        from auto_ontology.server.chat.metrics_flow import stream_metric_response

        out_q.put((_TAG_READY, None))
    except BaseException:
        logger.exception("Metric worker init failed")
        out_q.put((_TAG_ERROR, "Metric worker could not initialize"))
        out_q.put((_TAG_DONE, None))
        return
    while True:
        try:
            tag, payload = in_q.get()
        except (EOFError, OSError):
            return
        if tag != _MSG_ASK:
            continue
        try:
            for event in stream_metric_response(payload):
                out_q.put((_TAG_EVENT, event))
        except BaseException as exc:
            logger.error("Metric worker failed error_class=%s", type(exc).__name__)
            out_q.put((_TAG_ERROR, "Governed metric query failed"))
            return
        else:
            out_q.put((_TAG_DONE, None))


class PrewarmedWorker:
    """One request at a time; failed, expired or mismatched streams are never reused."""

    def __init__(
        self, mode: str = "explore", *, loop_override: Callable | None = None
    ) -> None:
        self._mode = mode
        self._closed = False
        self._ready = False
        self._request_id: str | None = None
        self._kill_lock = threading.Lock()
        self._permit = False
        self._owns_process = True
        if not _PROCESS_PERMITS.acquire(blocking=False):
            raise WorkerBusy("Chat worker capacity is full")
        self._permit = True
        self._ctx = mp.get_context("spawn")
        try:
            self._in_q = self._ctx.Queue(maxsize=1)
            self._out_q = self._ctx.Queue(maxsize=128)
            # A killed child may close its read end during a pending send.
            # This is deliberate cancellation, not an asynchronous feed error.
            self._in_q._ignore_epipe = True
            self._out_q._ignore_epipe = True
            self._proc = self._ctx.Process(
                target=_worker_entrypoint,
                args=(self._in_q, self._out_q, mode, os.getpid(), loop_override),
                daemon=True,
            )
            self._proc.start()
        except BaseException:
            self.kill()
            raise

    def is_alive(self) -> bool:
        return not getattr(self, "_closed", False) and self._proc.is_alive()

    def reusable(self) -> bool:
        return self.is_alive() and self._ready and self._request_id is None

    def submit(
        self,
        question: str,
        prediction: bool | None = None,
        target_db: str | None = None,
        subject_token: str | None = None,
        conversation_history: list[dict[str, str | None]] | None = None,
        evidence: str | None = None,
        metric_context: dict[str, Any] | None = None,
        user_id: str | None = None,
        persistent: bool = False,
        turn_id: str | None = None,
    ) -> None:
        if not self.reusable():
            raise RuntimeError("Worker is not ready for a new request")
        if self._mode == "metrics":
            payload = {
                "question": question,
                "metric_context": metric_context,
                "user_id": user_id,
                "persistent": persistent,
                "turn_id": turn_id,
                "conversation_history": conversation_history or [],
            }
        else:
            payload = (
                question,
                prediction,
                target_db,
                subject_token,
                conversation_history or [],
                evidence,
            )
        if len(json.dumps(payload, default=str).encode()) > _MAX_IPC_BYTES:
            raise ValueError("Worker request byte budget exceeded")
        self._request_id = uuid.uuid4().hex
        self._run_deadline = time.monotonic() + _REQUEST_TIMEOUT_S
        try:
            self._in_q.put((_MSG_ASK, self._request_id, payload), timeout=1)
        except BaseException:
            self.kill()
            raise

    def events(self) -> Generator[dict[str, Any] | None, None, None]:
        if not self._request_id:
            raise RuntimeError("Worker has no submitted request")
        expected = self._request_id
        try:
            while True:
                if time.monotonic() >= self._run_deadline:
                    raise RuntimeError("Chat request deadline exceeded")
                try:
                    frame = self._out_q.get(timeout=_QUEUE_POLL_S)
                except Empty:
                    if not self.is_alive():
                        raise RuntimeError(
                            "Worker exited before completing its request"
                        )
                    yield None
                    continue
                if not isinstance(frame, tuple) or len(frame) != 3:
                    raise RuntimeError("Invalid worker event envelope")
                tag, request_id, value = frame
                if request_id != expected:
                    raise RuntimeError("Worker event belongs to another request")
                if tag == _TAG_ERROR:
                    raise RuntimeError("Worker execution failed")
                if tag == _TAG_DONE:
                    self._request_id = None
                    return
                if tag != _TAG_EVENT or not isinstance(value, dict):
                    raise RuntimeError("Invalid worker event")
                yield value
        except BaseException:
            self.kill()
            raise

    def drain_until_ready(self, timeout: float | None = None) -> bool:
        if self._ready:
            return self.is_alive()
        deadline = time.monotonic() + (
            timeout if timeout is not None else _STARTUP_TIMEOUT_S
        )
        while time.monotonic() < deadline and self.is_alive():
            try:
                frame = self._out_q.get(
                    timeout=min(_QUEUE_POLL_S, max(0.001, deadline - time.monotonic()))
                )
            except Empty:
                continue
            if frame == (_TAG_READY, None, None):
                self._ready = True
                return True
            break
        self.kill()
        return False

    def kill(self) -> None:
        # Also supports externally-owned deterministic test processes.
        if not hasattr(self, "_kill_lock"):
            self._kill_lock = threading.Lock()
        with self._kill_lock:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            proc = getattr(self, "_proc", None)
            if proc is not None and proc.pid is not None:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=_TERMINATE_GRACE_S)
                    if proc.is_alive():
                        proc.kill()
                        proc.join(timeout=_TERMINATE_GRACE_S)
                else:
                    proc.join(timeout=0)
                if getattr(self, "_owns_process", False) and not proc.is_alive():
                    proc.close()
            for name in ("_in_q", "_out_q"):
                queue = getattr(self, name, None)
                if queue is not None:
                    queue.cancel_join_thread()
                    queue.close()
                    feeder = getattr(queue, "_thread", None)
                    if feeder is not None:
                        feeder.join(timeout=0.5)
                        if feeder.is_alive():
                            # No child is reading now. Unblock a pending large
                            # write, then let the feeder exit before closing its
                            # writer; closing a live writer races its sentinel.
                            queue._reader.close()
                            feeder.join(timeout=0.5)
                    if feeder is None or not feeder.is_alive():
                        queue._reader.close()
                        queue._writer.close()
            if getattr(self, "_permit", False):
                self._permit = False
                _PROCESS_PERMITS.release()


class WarmPool:
    """One standby per mode, one warming task, and a shared process capacity bound."""

    def __init__(
        self,
        mode: str = "explore",
        *,
        generation_reader: Callable[[], int] | None = None,
    ) -> None:
        from auto_ontology.dal.connections import connection_generation

        self._generation_reader = generation_reader or connection_generation
        self._connection_generation = self._generation_reader()
        self._mode = mode
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._standby: PrewarmedWorker | None = None
        self._leased: set[PrewarmedWorker] = set()
        self._warming = False
        self._closed = False
        self._generation = 0
        self._warmup_error: Exception | None = None
        with _POOLS_LOCK:
            _POOLS.add(self)
        self._spawn_async()

    def _sync_generation(self, *, force: bool = False) -> None:
        current = self._generation_reader()
        with self._lock:
            if not force and current == self._connection_generation:
                return
            self._connection_generation = current
            self._generation += 1
            worker, self._standby = self._standby, None
        if worker is not None:
            worker.kill()

    def _spawn_async(self) -> None:
        with self._lock:
            if self._closed or self._warming or self._standby is not None:
                return
            self._warming = True
            self._warmup_error = None
            generation = self._generation
        threading.Thread(
            target=self._spawn_sync, args=(generation,), daemon=True
        ).start()

    def _spawn_sync(self, generation: int) -> None:
        worker = None
        try:
            worker = PrewarmedWorker(self._mode)
            worker._generation = generation
            if not worker.drain_until_ready():
                raise WorkerStartupError("Worker startup failed")
            with self._lock:
                if (
                    not self._closed
                    and generation == self._generation
                    and self._standby is None
                ):
                    self._standby, worker = worker, None
        except Exception as exc:
            with self._lock:
                if generation == self._generation:
                    self._warmup_error = exc
            logger.warning(
                "Worker warmup unavailable error_class=%s", type(exc).__name__
            )
        finally:
            if worker is not None:
                worker.kill()
            with self._condition:
                self._warming = False
                self._condition.notify_all()

    def _evict_idle_spare(self) -> bool:
        with _POOLS_LOCK:
            pools = list(_POOLS)
        for pool in pools:
            if pool is self:
                continue
            with pool._lock:
                worker, pool._standby = pool._standby, None
            if worker is not None:
                worker.kill()
                return True
        return False

    def acquire(self) -> PrewarmedWorker:
        self._sync_generation()
        with self._condition:
            if self._closed:
                raise WorkerBusy("Chat worker pool is closed")
            if self._warming and self._standby is None:
                if not self._condition.wait_for(
                    lambda: not self._warming or self._closed,
                    timeout=_STARTUP_TIMEOUT_S,
                ):
                    raise WorkerStartupError("Worker startup deadline exceeded")
            if self._closed:
                raise WorkerBusy("Chat worker pool is closed")
            if self._warmup_error is not None and not isinstance(
                self._warmup_error, WorkerBusy
            ):
                self._warmup_error = None
                raise WorkerStartupError(
                    "Worker warmup failed; retry after configuration is repaired"
                )
            worker, self._standby = self._standby, None
            generation = self._generation
        if worker is not None and not worker.reusable():
            worker.kill()
            worker = None
        if worker is None:
            try:
                worker = PrewarmedWorker(self._mode)
            except WorkerBusy:
                if not self._evict_idle_spare():
                    raise
                worker = PrewarmedWorker(self._mode)
            worker._generation = generation
            if not worker.drain_until_ready():
                raise WorkerStartupError("Worker startup failed")
        try:
            self._sync_generation()
        except Exception:
            worker.kill()
            raise
        with self._lock:
            if self._closed or generation != self._generation:
                worker.kill()
                raise WorkerBusy("Chat worker configuration changed; retry")
            self._leased.add(worker)
        return worker

    def return_alive(self, worker: PrewarmedWorker) -> None:
        try:
            self._sync_generation()
        except Exception:
            # An unverified credential generation must never enter standby.
            with self._lock:
                self._leased.discard(worker)
            worker.kill()
            raise
        with self._lock:
            if self._standby is worker:
                return
            owned = worker in self._leased
            self._leased.discard(worker)
            keep = (
                owned
                and not self._closed
                and getattr(worker, "_generation", None) == self._generation
                and worker.reusable()
                and self._standby is None
            )
            if keep:
                self._standby = worker
        if not keep:
            worker.kill()
        self._spawn_async()

    def replace(self, worker: PrewarmedWorker) -> None:
        with self._lock:
            self._leased.discard(worker)
        worker.kill()
        self._spawn_async()

    def refresh(self) -> None:
        self._sync_generation(force=True)
        self._spawn_async()

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            workers = list(self._leased)
            self._leased.clear()
            if self._standby is not None:
                workers.append(self._standby)
                self._standby = None
            self._condition.notify_all()
        for worker in workers:
            worker.kill()


_pool: WarmPool | None = None
_metrics_pool: WarmPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> WarmPool:
    """Lazily-initialised module-level pool. Safe under thread races."""

    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = WarmPool()
        return _pool


def get_metrics_pool() -> WarmPool:
    """Lazily warm only the governed-query worker's minimal dependencies."""
    global _metrics_pool
    with _pool_lock:
        if _metrics_pool is None:
            _metrics_pool = WarmPool("metrics")
        return _metrics_pool


def shutdown_pool() -> None:
    global _pool, _metrics_pool
    with _pool_lock:
        pool, metrics_pool = _pool, _metrics_pool
        _pool = _metrics_pool = None
    for current in (pool, metrics_pool):
        if current is not None:
            current.shutdown()


def refresh_chat_workers() -> None:
    """Invalidate standby and leased generations without creating unused pools."""
    with _pool_lock:
        pools = (_pool, _metrics_pool)
    for pool in pools:
        if pool is not None:
            pool.refresh()
