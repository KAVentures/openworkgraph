"""Explicit opt-in cloud text sync using the existing enrolled Gateway device token.

No second key, relay queue, or standing background polls when opted out.
Normal structural evidence synchronization is intentionally independent.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .state import SyncState

_NEXT_SCAN = 0.0


def process_cloud_text(client: Any, *, url: str, state: SyncState,
                       sharing_paused: bool = False, now: float | None = None) -> int:
    global _NEXT_SCAN
    from server import work_text_capture as wt
    from server.ai_access import ai_access_enabled
    from shared.capture_control import read_state
    current = time.monotonic() if now is None else float(now)
    policy = wt.get_policy()
    allowed = bool(
        not sharing_paused and policy.get("capture_enabled")
        and policy.get("ai_read_enabled") and policy.get("cloud_read_enabled")
        and policy.get("cloud_ack_version") == wt.CLOUD_ACK_VERSION
        and ai_access_enabled() and read_state().get("state") == "recording"
    )
    previously_shared = state.get_bool("cloud_text_remote_granted", False)
    if not allowed:
        if previously_shared:
            try:
                response = client.post(f"{url}/v1/device/work-text-revoke")
                response.raise_for_status()
                state.set_bool("cloud_text_remote_granted", False)
                for name in ("cloud_text_last_manifest", "cloud_text_missing_refs",
                             "cloud_text_last_check", "cloud_text_consent"):
                    state.delete(name)
                state.set("cloud_text_last_error", "")
            except Exception as exc:
                # Retry on the next normal worker iteration; do not resurrect
                # data nor interrupt structural evidence synchronization.
                state.set("cloud_text_last_error", str(exc)[:180])
        return 0

    if current < _NEXT_SCAN:
        return 0
    _NEXT_SCAN = current + 10.0
    try:
        snapshot = wt.cloud_sync_snapshot()
        items = snapshot["items"]
        refs = [x["ref"] for x in items]
        manifest = hashlib.sha256(("\n".join(refs) + snapshot["granted_at"]).encode()).hexdigest()
        last_check = float(state.get("cloud_text_last_check", "0"))
        pending = json.loads(state.get("cloud_text_missing_refs", "[]"))
        if not isinstance(pending, list):
            pending = []
        changed = (
            state.get("cloud_text_last_manifest") != manifest
            or state.get("cloud_text_consent") != snapshot["granted_at"]
        )
        if not changed and not pending and current - last_check < 1800:
            return 0
        # Missing refs are supplied by the server and must originate from the
        # local manifest. Never upload model-supplied references.
        requested = set(str(x) for x in pending)
        lookup = {x["ref"]: x for x in items}
        batch = [lookup[ref] for ref in refs if ref in requested][:50]
        # After a policy toggle but before sending a body, fail closed again.
        check = wt.get_policy()
        if not (check.get("cloud_read_enabled")
                and check.get("cloud_granted_at") == snapshot["granted_at"]
                and ai_access_enabled()
                and read_state().get("state") == "recording"):
            return 0
        response = client.post(f"{url}/v1/device/work-text-sync", json={
            "items": batch,
            "active_refs": refs,
            "consent_version": snapshot["consent_version"],
            "granted_at": snapshot["granted_at"],
        })
        response.raise_for_status()
        result = response.json()
        missing = result.get("missing_refs") or []
        if not isinstance(missing, list):
            raise ValueError("invalid cloud text acknowledgment")
        state.set_bool("cloud_text_remote_granted", True)
        state.set("cloud_text_consent", snapshot["granted_at"])
        state.set("cloud_text_last_manifest", manifest)
        state.set("cloud_text_missing_refs", json.dumps(missing))
        state.set("cloud_text_last_check", str(current))
        state.set("cloud_text_last_error", "")
        # Backfill any missing rows rapidly, but bounded to 50 per request.
        if missing:
            _NEXT_SCAN = current + 2.0
        return len(batch)
    except Exception as exc:
        state.set("cloud_text_last_error", str(exc)[:180])
        _NEXT_SCAN = current + 30.0
        return 0
