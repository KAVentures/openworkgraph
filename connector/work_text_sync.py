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
    # The sync-state DB may be recreated (reinstall, storage cleanup, worker
    # reset) independently of the user's persistent privacy policy. A disabled
    # grant must still reach the Gateway for deletion in that case. Remember
    # the successfully revoked *grant epoch*, so no calls are made for users
    # who have never enabled cloud sharing and successful revokes are not
    # repeatedly retried.
    previously_shared = state.get_bool("cloud_text_remote_granted", False)
    grant_epoch = str(policy.get("cloud_granted_at") or "")
    revocation_due = bool(
        previously_shared or (
            grant_epoch and state.get("cloud_text_last_revoked_grant") != grant_epoch
        )
    )
    if not allowed:
        if revocation_due:
            try:
                response = client.post(f"{url}/v1/device/work-text-revoke")
                response.raise_for_status()
                state.set_bool("cloud_text_remote_granted", False)
                if grant_epoch:
                    state.set("cloud_text_last_revoked_grant", grant_epoch)
                for name in ("cloud_text_last_manifest", "cloud_text_missing_refs",
                             "cloud_text_last_check", "cloud_text_consent"):
                    state.delete(name)
                state.set("cloud_text_last_error", "")
                # A successful cloud revoke creates a durable Gateway consent
                # boundary. If it came from Pause/Stop or the master AI switch,
                # leave cloud sharing OFF locally so Resume cannot replay an
                # older consent epoch and silently attempt re-sharing.
                fresh_policy = wt.get_policy()
                if (fresh_policy.get("cloud_read_enabled")
                        and fresh_policy.get("cloud_granted_at") == grant_epoch):
                    wt.set_policy({"cloud_read_enabled": False})
                    state.set("cloud_text_last_error",
                              "Cloud sharing was revoked. A fresh local cloud "
                              "opt-in is required before sharing resumes.")
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
        # A separately authenticated account-level deletion revokes the cloud
        # grant on the server. Treat that as authoritative: stop local uploads
        # and require a fresh user opt-in rather than retrying a stale batch
        # forever. Check the grant epoch to avoid overriding a newer choice
        # made while the HTTPS request was in flight.
        response = getattr(exc, "response", None)
        if (getattr(response, "status_code", None) == 403
                and "OAuth reauthorization required" in str(getattr(response, "text", ""))):
            try:
                local = wt.get_policy()
                if local.get("cloud_granted_at") == snapshot["granted_at"]:
                    wt.set_policy({"cloud_read_enabled": False})
                    state.set_bool("cloud_text_remote_granted", False)
                    for name in ("cloud_text_last_manifest", "cloud_text_missing_refs",
                                 "cloud_text_last_check", "cloud_text_consent"):
                        state.delete(name)
                    state.set("cloud_text_last_error",
                              "Cloud text was deleted from the signed-in account. "
                              "Cloud sharing is now off on this computer; "
                              "fresh account and local approval are required.")
                    return 0
            except Exception:
                # Failure to update the local policy never authorizes upload.
                pass
        # Without independent account approval, repeated attempts can burn
        # requests all day while the user has only enabled the local switch.
        # Keep the preference intact but use a restrained retry interval.
        if (getattr(response, "status_code", None) == 403
                and ("OAuth cloud work-text approval required" in str(getattr(response, "text", ""))
                     or "OAuth device-specific cloud-text approval required"
                     in str(getattr(response, "text", "")))):
            state.set("cloud_text_last_error",
                      "Cloud sync requires separate signed-in account approval "
                      "of this particular linked device.")
            _NEXT_SCAN = current + 300.0
            return 0
        if (getattr(response, "status_code", None) == 503
                and "Cloud text sharing is disabled" in str(getattr(response, "text", ""))):
            state.set("cloud_text_last_error",
                      "Cloud work-text sharing is not available on this Gateway yet.")
            _NEXT_SCAN = current + 1800.0
            return 0
        state.set("cloud_text_last_error", str(exc)[:180])
        _NEXT_SCAN = current + 30.0
        return 0
