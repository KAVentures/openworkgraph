from __future__ import annotations

"""Reusable non-blocking sink for optional agent-runtime adapters.

Agent observation must never become a runtime dependency. Adapter callbacks can
therefore enqueue only already-safe structural events into this bounded sink; a
daemon worker performs local HTTP writes in the background. Queue pressure or an
unavailable observer drops telemetry rather than delaying or failing the agent.
"""

from dataclasses import dataclass
import os
import queue
import sys
import threading
import time
from typing import Callable

from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence

from ._agent_client import post_agent_events


@dataclass(frozen=True)
class SinkStats:
    accepted: int
    dropped: int
    send_failures: int
    batches_sent: int


class BufferedAgentEventSink:
    """Bounded, fail-open structural event delivery for agent adapters."""

    def __init__(
        self,
        *,
        sender: Callable[[list[dict]], dict] | None = None,
        max_queue: int = 512,
        batch_size: int = 32,
        flush_interval: float = 0.2,
    ) -> None:
        self._sender = sender or post_agent_events
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=max(1, min(int(max_queue), 10_000)))
        self._batch_size = max(1, min(int(batch_size), 256))
        self._flush_interval = max(0.02, min(float(flush_interval), 2.0))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._accepted = 0
        self._dropped = 0
        self._send_failures = 0
        self._batches_sent = 0

    def _debug_notice(self, message: str) -> None:
        if os.getenv("OWG_AGENT_ADAPTER_DEBUG", "").strip() == "1":
            # Never include the exception or native event in diagnostics: either
            # can contain URLs, paths, headers, or runtime content.
            print(f"OpenWorkGraph agent adapter: {message}", file=sys.stderr)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="openworkgraph-agent-sink",
                daemon=True,
            )
            self._thread.start()

    def emit(self, event: dict) -> bool:
        """Validate and enqueue one structural event without blocking."""
        if self._stop.is_set():
            with self._lock:
                self._dropped += 1
            return False
        try:
            # Validate locally before a native runtime payload can enter the
            # queue. The server validates again at persistence time.
            agent_event_to_evidence(event)
        except (AgentEvidenceError, TypeError, ValueError):
            with self._lock:
                self._dropped += 1
            self._debug_notice("dropped invalid structural event")
            return False

        self._ensure_worker()
        try:
            self._queue.put_nowait(dict(event))
        except queue.Full:
            with self._lock:
                self._dropped += 1
            self._debug_notice("telemetry queue full; event dropped")
            return False
        with self._lock:
            self._accepted += 1
        return True

    def _take_batch(self) -> list[dict]:
        batch: list[dict] = []
        try:
            first = self._queue.get(timeout=self._flush_interval)
        except queue.Empty:
            return batch
        batch.append(first)
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _run(self) -> None:
        while not self._stop.is_set() or self._queue.unfinished_tasks:
            batch = self._take_batch()
            if not batch:
                if self._stop.is_set():
                    break
                continue
            try:
                self._sender(batch)
            except Exception:
                with self._lock:
                    self._send_failures += 1
                self._debug_notice("local observer unavailable; telemetry batch dropped")
            else:
                with self._lock:
                    self._batches_sent += 1
            finally:
                for _ in batch:
                    self._queue.task_done()

    def force_flush(self, *, timeout: float = 2.0) -> bool:
        """Wait briefly for currently queued events; never waits indefinitely."""
        deadline = time.monotonic() + max(0.0, min(float(timeout), 10.0))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def shutdown(self, *, timeout: float = 2.0) -> None:
        # Reject new events first, then let the existing worker drain what was
        # already accepted. Shutdown remains bounded even if the sender hangs.
        self._stop.set()
        self.force_flush(timeout=timeout)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, min(float(timeout), 10.0)))

    def stats(self) -> SinkStats:
        with self._lock:
            return SinkStats(
                accepted=self._accepted,
                dropped=self._dropped,
                send_failures=self._send_failures,
                batches_sent=self._batches_sent,
            )
