"""Explicitly opted-in personal cloud work-text pull relay.

Runs only inside an already enrolled Gateway sync worker. The ordinary event
outbox never sees a work-text body; only remote search requests are answered,
using the same local privacy, recorder, master AI and cloud-consent gates.
"""
from __future__ import annotations

import time
from typing import Any

from .state import SyncState

_NEXT_CHECK = 0.0
POLL_SECONDS = 15.0


def process_pending(client: Any, *, url: str, state: SyncState, now: float) -> int:
    global _NEXT_CHECK
    from server import work_text_capture as local
    from server.ai_access import ai_access_enabled

    policy = local.get_policy()
    permitted = bool(
        policy.get("capture_enabled")
        and policy.get("ai_read_enabled")
        and policy.get("cloud_read_enabled")
        and ai_access_enabled()
    )
    was_permitted = state.get_bool("cloud_work_text_was_permitted", False)
    if not permitted:
        # Persistent state survives worker restarts and re-attempts revocation
        # on network failures. Never send a body when consent is gone.
        if was_permitted:
            try:
                client.post(f"{url}/v1/device/work-text-revoke").raise_for_status()
                state.set_bool("cloud_work_text_was_permitted", False)
                state.set("cloud_work_text_last_error", "")
            except Exception as exc:
                state.set("cloud_work_text_last_error", str(exc)[:180])
        return 0

    if now < _NEXT_CHECK:
        return 0
    _NEXT_CHECK = now + POLL_SECONDS
    try:
        response = client.get(f"{url}/v1/device/work-text-requests")
        response.raise_for_status()
        requests = response.json().get("requests") or []
        handled = 0
        for task in requests[:3]:
            ref = str(task.get("request_id") or "")
            query = str(task.get("query") or "")
            # Recheck consent immediately before local content is read AND sent.
            try:
                result = local.search_for_cloud(query, limit=4)
            except (PermissionError, ValueError):
                continue
            fresh = local.get_policy()
            if not (
                fresh.get("capture_enabled")
                and fresh.get("ai_read_enabled")
                and fresh.get("cloud_read_enabled")
                and ai_access_enabled()
            ):
                break
            sent = client.post(
                f"{url}/v1/device/work-text-response",
                json={"request_id": ref, "items": result["items"]},
            )
            sent.raise_for_status()
            handled += 1
        state.set_bool("cloud_work_text_was_permitted", True)
        state.set("cloud_work_text_last_error", "")
        return handled
    except Exception as exc:
        # A missing/older relay deployment or a temporary outage must NEVER
        # interrupt ordinary work evidence synchronization.
        state.set("cloud_work_text_last_error", str(exc)[:180])
        return 0
