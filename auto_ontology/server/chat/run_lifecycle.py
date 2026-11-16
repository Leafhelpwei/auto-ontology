# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lease renewal survives browser disconnects and stops workers on lost ownership."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any
from uuid import uuid4

from auto_ontology.dal import chat_runs

logger = logging.getLogger(__name__)
OWNER_ID = f"{os.getpid()}:{uuid4()}"
_guards: set[LeaseGuard] = set()
_lock = threading.Lock()
_reaper_stop = threading.Event()
_reaper_thread: threading.Thread | None = None


class LeaseGuard:
    def __init__(self, ticket: chat_runs.Ticket) -> None:
        self.ticket = ticket
        self.cancelled = threading.Event()
        self.stopped = threading.Event()
        self._worker: Any = None
        self._lock = threading.Lock()
        with _lock:
            _guards.add(self)
        self._thread = threading.Thread(target=self._renew, daemon=True)
        self._thread.start()

    def set_worker(self, worker: Any) -> None:
        with self._lock:
            self._worker = worker
            if self.cancelled.is_set():
                worker.kill()
                raise chat_runs.RunCancelled("Run cancelled during startup")

    def _cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            if self._worker is not None:
                self._worker.kill()

    def _renew(self) -> None:
        interval = min(1, chat_runs.LEASE_SECONDS / 3)
        while not self.stopped.wait(interval):
            try:
                if not chat_runs.heartbeat(self.ticket) and not self.cancelled.is_set():
                    self._cancel()
            except Exception as exc:
                # Losing the store means we cannot prove exclusive ownership.
                logger.warning("Chat lease ended error_class=%s", type(exc).__name__)
                self._cancel()
                return

    def close(self) -> None:
        self.stopped.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=6)
        with _lock:
            _guards.discard(self)


def start_reaper() -> None:
    global _reaper_thread
    _reaper_stop.clear()

    def reap() -> None:
        while not _reaper_stop.wait(1):
            try:
                chat_runs.reap_expired()
            except Exception as exc:
                logger.warning(
                    "Chat recovery unavailable error_class=%s", type(exc).__name__
                )

    _reaper_thread = threading.Thread(target=reap, daemon=True)
    _reaper_thread.start()


def stop_reaper() -> None:
    _reaper_stop.set()
    if _reaper_thread is not None:
        _reaper_thread.join(timeout=6)
    with _lock:
        guards = list(_guards)
    for guard in guards:
        guard._cancel()
        try:
            chat_runs.finish(
                guard.ticket, "cancelled", message="服务正在重启，查询已取消，请重试。"
            )
        except Exception as exc:
            logger.warning(
                "Chat shutdown finalization deferred error_class=%s", type(exc).__name__
            )
        guard.close()
