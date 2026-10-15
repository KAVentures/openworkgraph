from __future__ import annotations

"""Explicit historical structural-evidence backfill, disabled by default.

This deliberately NEVER rewinds the normal forward-only sync cursor. It
honors enrollment boundary, consent, current organization policy, pause and
discovery exclusions, and the same local-only redaction code as live sync.

No AI transcript messages, text excerpts, screenshot bytes, or source secrets.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Callable

import httpx

from shared.time_utils import normalize_timestamp
from shared.discovery_scope import read_state as read_discovery_state

from .config import load_device_token, load_gateway_settings
from .cloud_privacy import local_owner_names, project_event_for_upload
from .policy import merge_policies, prepare_event_for_gateway
from .privacy_key import load_or_create_upload_privacy_key
from .state import SyncState
from .sync import _fetch_policy, _push_batch_resilient, _row_event

_MAX_WINDOW_DAYS = 30
_MAX_SCANNED = 5_000
_PREVIEW_EXPIRY_SECONDS = 600
_CONFIRMATION = "I explicitly consent to share the previewed older evidence"


def _enabled() -> bool:
    # A deliberate rollout kill switch, independent of ordinary Gateway sync.
    return os.getenv("OWG_ENABLE_HISTORY_BACKFILL", "").lower() in {"1", "true", "yes"}


def _window(since: str, until: str) -> tuple[str, str]:
    try:
        # Make users choose a timezone-aware range; do not reinterpret a
        # naive date/time silently when deciding what old data to transmit.
        raw_start = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        raw_end = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if raw_start.tzinfo is None or raw_end.tzinfo is None:
            raise ValueError("timezone is required")
        start = datetime.fromisoformat(normalize_timestamp(since))
        end = datetime.fromisoformat(normalize_timestamp(until))
    except (TypeError, ValueError) as exc:
        raise ValueError("explicit timezone-aware start and end are required") from exc
    if start >= end or end > datetime.now(timezone.utc) or end - start > timedelta(days=_MAX_WINDOW_DAYS):
        raise ValueError("past date range must be positive and at most 30 days")
    return start.isoformat(), end.isoformat()


def _context(config_path: Path) -> dict[str, Any]:
    if not _enabled():
        raise PermissionError("historical sharing is disabled pending privacy review")
    root = config_path.resolve().parent
    data_dir = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data" / "live"))
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", root / "data" / "auth"))
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)
    token = load_device_token(settings)
    state = SyncState(data_dir / "gateway_sync_state.db")
    if not settings.enabled or not token or not settings.url:
        raise PermissionError("organization enrollment and a current device grant are required")
    if state.get_bool("sharing_paused") or state.get("status") == "revoked":
        raise PermissionError("cannot share historical data while paused or revoked")
    if state.get("history_sync_mode") != "from_enrollment_forward":
        raise PermissionError("unsupported enrollment history mode")
    boundary = state.get_int("history_enrollment_boundary_event_id", 0)
    if boundary < 1:
        raise PermissionError("legacy enrollment has no safe historical boundary; re-enroll first")
    return dict(data_dir=data_dir, auth_dir=auth_dir, settings=settings,
                token=token, state=state, boundary=boundary)


def _eligible(config_path: Path, context: dict[str, Any], since: str, until: str,
              remote_policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = context["state"]
    database = context["data_dir"] / "workflow_observer.db"
    if not database.exists():
        raise ValueError("no retained local events to preview")
    local_policy = dict(context["settings"].local_policy)
    # Never backfill transcripts: this endpoint is restricted to structural
    # evidence, irrespective of separate AI-content sharing settings.
    local_policy["allow_agent_session_messages"] = False
    policy = merge_policies(local_policy, remote_policy)
    discovery = read_discovery_state(data_dir=context["data_dir"])
    discovery_hold = bool(discovery.get("enabled"))
    discovery_boundary = int(discovery.get("gateway_event_boundary_id") or 0)
    key = load_or_create_upload_privacy_key(context["auth_dir"])
    owner = local_owner_names(config_path)

    with sqlite3.connect(database, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        raw = conn.execute(
            "SELECT * FROM events WHERE id <= ? "
            "AND julianday(observed_at) >= julianday(?) "
            "AND julianday(observed_at) < julianday(?) "
            "ORDER BY id ASC LIMIT ?",
            (context["boundary"], since, until, _MAX_SCANNED + 1),
        ).fetchall()
        if len(raw) > _MAX_SCANNED:
            raise ValueError("too much historical evidence; select a smaller range")

    projected = []
    excluded = 0
    for row in raw:
        local_id = int(row["id"])
        try:
            observed = normalize_timestamp(str(row["observed_at"] or ""))
        except (TypeError, ValueError):
            excluded += 1
            continue
        if not (since <= observed < until):
            continue
        if state.skipped(local_id) or (discovery_hold and local_id > discovery_boundary):
            excluded += 1
            continue
        item = prepare_event_for_gateway(_row_event(row), policy)
        if item is None:
            excluded += 1
            continue
        # Never substitute raw local content if redaction raises.
        protected = project_event_for_upload(item, secret=key, owner_names=owner)
        event_id = str(protected.get("event_id") or "")
        if not event_id:
            raise ValueError("a historical event has no stable sync identifier")
        projected.append(protected)
    return projected, {"scanned": len(raw), "eligible": len(projected),
                       "excluded": excluded, "boundary": context["boundary"]}


def _plan(config_path: Path, since: str, until: str, *,
          policy_reader: Callable[[httpx.Client, str], dict[str, Any]]) -> tuple[dict, list[dict], dict]:
    start, end = _window(since, until)
    ctx = _context(config_path)
    settings = ctx["settings"]
    with httpx.Client(headers={"Authorization": "Bearer " + ctx["token"]},
                      timeout=10, verify=settings.verify_tls, follow_redirects=False,
                      trust_env=False) as client:
        policy = policy_reader(client, settings.url)
    prepared, counters = _eligible(config_path, ctx, start, end, policy)
    # Bind confirmation to the *exact projected preview*, device grant, URL,
    # eligibility boundary and current policy. No unredacted content is stored.
    encoded = json.dumps(prepared, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    key = load_or_create_upload_privacy_key(ctx["auth_dir"])
    identity = "|".join([str(settings.url), str(ctx["boundary"]), start, end,
                         fingerprint, hashlib.sha256(ctx["token"].encode()).hexdigest()])
    digest = hmac.new(key.encode() if isinstance(key, str) else key,
                      identity.encode(), hashlib.sha256).hexdigest()
    return {
        "since": start, "until": end, "eligible": counters["eligible"],
        "excluded": counters["excluded"], "scanned": counters["scanned"],
        "historical_event_count": counters["eligible"],
        "only_privacy_hardened_structural_events": True,
        "older_local_transcripts_excluded": True,
        "screenshots_and_clipboard_contents_excluded": True,
        "normal_sync_cursor_unchanged": True,
        "no_upload_performed": True,
        "preview_digest": digest,
    }, prepared, ctx


def preview(config_path: Path, since: str, until: str,
            *, policy_reader: Callable = _fetch_policy) -> dict[str, Any]:
    result, _, ctx = _plan(config_path, since, until, policy_reader=policy_reader)
    state = ctx["state"]
    # The user still must explicitly confirm. Preview never uploads.
    token = secrets.token_urlsafe(32)
    state.set("history_backfill_preview_token", hashlib.sha256(token.encode()).hexdigest())
    state.set("history_backfill_preview_digest", result["preview_digest"])
    state.set_int("history_backfill_preview_expiry", int(time.time()) + _PREVIEW_EXPIRY_SECONDS)
    state.set("history_backfill_preview_window", result["since"] + "|" + result["until"])
    return {k:v for k,v in result.items() if k!="preview_digest"} | {
        "confirmation_token": token,
        "expires_in_seconds": _PREVIEW_EXPIRY_SECONDS,
        "requires_explicit_confirmation": True,
    }


def confirm(config_path: Path, since: str, until: str, *,
            confirmation_token: str, explicit_confirmation: str,
            policy_reader: Callable = _fetch_policy,
            batch_sender: Callable = _push_batch_resilient) -> dict[str, Any]:
    if explicit_confirmation != _CONFIRMATION:
        raise PermissionError("specific informed historical sharing confirmation is required")
    ctx = _context(config_path)
    state = ctx["state"]
    if (not confirmation_token or not hmac.compare_digest(
        state.get("history_backfill_preview_token"), hashlib.sha256(str(confirmation_token).encode()).hexdigest())
        or state.get_int("history_backfill_preview_expiry") < int(time.time())):
        raise PermissionError("preview token has expired or already been used")
    start, end = _window(since, until)
    if state.get("history_backfill_preview_window") != start + "|" + end:
        raise PermissionError("confirmed date range differs from the preview")
    result, events, new_ctx = _plan(config_path, since, until, policy_reader=policy_reader)
    if not hmac.compare_digest(state.get("history_backfill_preview_digest"),
                               result["preview_digest"]):
        raise PermissionError("historical evidence or organization policy changed; preview again")
    # Consume before first network write. Gateway's event IDs are idempotent;
    # after partial failures the user must preview and confirm again.
    for name in ("history_backfill_preview_token","history_backfill_preview_digest",
                 "history_backfill_preview_expiry","history_backfill_preview_window"):
        state.delete(name)
    if new_ctx["state"].get_bool("sharing_paused") or new_ctx["state"].get("status") == "revoked":
        raise PermissionError("history sharing revoked before upload")
    acknowledged: set[str] = set()
    rejected: dict[str, str] = {}
    settings = new_ctx["settings"]
    with httpx.Client(headers={"Authorization": "Bearer " + new_ctx["token"]},
                      timeout=10, verify=settings.verify_tls, follow_redirects=False,
                      trust_env=False) as client:
        step = max(1, min(settings.batch_size, 100))
        for offset in range(0, len(events), step):
            # A pause or revoke that arrives during a multi-batch operation
            # prevents any *further* transmission of historical evidence.
            if state.get_bool("sharing_paused") or state.get("status") == "revoked":
                raise PermissionError("historical upload interrupted by pause or revocation")
            batch = events[offset:offset+step]
            ok, bad = batch_sender(client, settings.url, batch)
            acknowledged.update(ok)
            rejected.update(bad)
            expected = {str(row["event_id"]) for row in batch}
            if not expected.issubset(ok | set(bad)):
                raise RuntimeError("Gateway failed to acknowledge the historical batch")
    # Rejections are not silently quarantined or retried: this explicit operation
    # has no backfill cursor, preventing any later accidental reupload.
    state.set("history_backfill_last_complete_at", datetime.now(timezone.utc).isoformat())
    state.set_int("history_backfill_last_acknowledged", len(acknowledged))
    state.set_int("history_backfill_last_rejected", len(rejected))
    return {"completed": True, "acknowledged": len(acknowledged),
            "rejected": len(rejected), "requires_new_preview_for_retry": bool(rejected),
            "normal_sync_cursor_unchanged": True, "scope": "explicit_date_bounded_structural_history"}


__all__ = ["preview", "confirm"]
