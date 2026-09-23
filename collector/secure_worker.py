from __future__ import annotations

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
        # Fail closed for observation if the control file cannot be read.
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

main = base.main


if __name__ == "__main__":
    main()
