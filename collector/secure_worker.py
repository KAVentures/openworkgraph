from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import httpx

from server.local_auth import ensure_api_token
from shared.capture_control import prepare_recordable_event, read_state

_real_post = httpx.post


def _authenticated_post(url, *args, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {ensure_api_token()}")
    return _real_post(url, *args, headers=headers, **kwargs)


httpx.post = _authenticated_post

from . import main as base  # noqa: E402

_real_persist = base.persist_event
_real_screenshot = base.screenshot
_real_activity_record = base.ActivityTracker.record


def _recording() -> bool:
    try:
        return read_state().get("state") == "recording"
    except Exception:
        return False


def _controlled_persist(event, outbox):
    prepared = prepare_recordable_event(event)
    if prepared is None:
        return
    return _real_persist(prepared, outbox)


def _controlled_screenshot(event_id: str):
    if not _recording():
        return None
    return _real_screenshot(event_id)


def _controlled_activity_record(self, kind, occurred_mono):
    if not _recording():
        return None
    return _real_activity_record(self, kind, occurred_mono)


base.persist_event = _controlled_persist
base.screenshot = _controlled_screenshot
base.ActivityTracker.record = _controlled_activity_record


def _watch_capture_state(initial_generation: int, done: threading.Event) -> None:
    # The existing collector loop already knows how to shut down cleanly: it
    # closes the current focus span, drains interaction work and retries its
    # durable local outbox. Reuse that path on every platform instead of relying
    # on process signal semantics (notably Windows TerminateProcess behavior).
    time.sleep(0.1)
    while not done.is_set():
        try:
            value = read_state()
            if value.get("state") != "recording" or int(value.get("generation") or 1) != initial_generation:
                base.STOP = True
                return
        except Exception:
            # Fail closed: a control-state read failure stops observation rather
            # than continuing to record without a trustworthy user control.
            base.STOP = True
            return
        done.wait(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated OpenWorkGraph capture worker")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--config", default=str(root / "config.json"))
    args = parser.parse_args()
    state = read_state()
    if state.get("state") != "recording":
        return
    generation = int(state.get("generation") or 1)
    done = threading.Event()
    monitor = threading.Thread(target=_watch_capture_state, args=(generation, done), daemon=True)
    monitor.start()
    try:
        base.run(Path(args.config).resolve())
    finally:
        done.set()
        monitor.join(timeout=1)


if __name__ == "__main__":
    main()
