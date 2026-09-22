from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import load_device_token, load_gateway_settings
from .policy import merge_policies, prepare_event_for_gateway
from .state import SyncState

STOP = False


def _stop(*_args) -> None:
    global STOP
    STOP = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_event(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value.pop("id", None)
    try:
        value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
    except Exception:
        value["metadata"] = {}
    return value


def _read_local_rows(db_path: Path, after_id: int, limit: int) -> list[tuple[int, dict[str, Any]]]:
    """Read from the canonical local evidence database, regardless of sensor source."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (int(after_id), max(1, min(int(limit), 1000))),
        ).fetchall()
    finally:
        conn.close()
    return [(int(row["id"]), _row_event(row)) for row in rows]


def _fetch_policy(client: httpx.Client, url: str) -> dict[str, Any]:
    response = client.get(f"{url}/v1/device-policy")
    response.raise_for_status()
    data = response.json()
    return data.get("policy") if isinstance(data.get("policy"), dict) else {}


def _push_batch(client: httpx.Client, url: str, events: list[dict[str, Any]]) -> set[str]:
    response = client.post(f"{url}/v1/evidence/batch", json={"events": events})
    response.raise_for_status()
    data = response.json()
    return {str(x) for x in data.get("acknowledged_event_ids") or []}


def _terminal_http_failure(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in {400, 413, 422}


def _push_batch_resilient(
    client: httpx.Client,
    url: str,
    events: list[dict[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    """Deliver good events even when one event is terminally invalid.

    Authentication/server/network failures remain retryable and abort the cycle.
    Validation/size failures are isolated by recursively splitting the batch. A
    single terminally bad event is returned for local quarantine rather than
    blocking every later event forever.
    """
    if not events:
        return set(), {}
    try:
        return _push_batch(client, url, events), {}
    except httpx.HTTPStatusError as exc:
        if not _terminal_http_failure(exc):
            raise
        if len(events) == 1:
            event_id = str(events[0].get("event_id") or "")
            try:
                detail = str(exc.response.json().get("detail") or exc.response.text)
            except Exception:
                detail = exc.response.text
            return set(), {event_id: f"HTTP {exc.response.status_code}: {detail}"[:500]}
        middle = len(events) // 2
        left_ok, left_bad = _push_batch_resilient(client, url, events[:middle])
        right_ok, right_bad = _push_batch_resilient(client, url, events[middle:])
        return left_ok | right_ok, left_bad | right_bad


def run(config_path: Path, *, once: bool = False) -> int:
    """Synchronize privacy-approved local evidence to an optional company Gateway.

    Local capture never depends on this worker. The cursor advances only after a
    complete Gateway acknowledgement, explicit policy/pause exclusion, or local
    quarantine of a terminally invalid event. Network/auth/server failures remain
    retryable and never advance the cursor.
    """
    global STOP
    STOP = False
    data_dir = Path(os.getenv("WORKFLOW_OBSERVER_DATA", config_path.parent / "data" / "live"))
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", config_path.parent / "data" / "auth"))
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)
    if not settings.enabled:
        return 0
    if not settings.url:
        raise RuntimeError("gateway.enabled is true but gateway.url is empty")
    token = load_device_token(settings)
    if not token:
        raise RuntimeError("Gateway is enabled but no device token exists. Enroll from the dashboard or run `python -m connector.enroll` first.")

    state = SyncState(data_dir / "gateway_sync_state.db")
    db_path = data_dir / "workflow_observer.db"
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    headers = {"Authorization": f"Bearer {token}"}
    policy: dict[str, Any] = merge_policies(settings.local_policy, {})
    policy_fetched_at = 0.0
    delay = settings.poll_seconds
    state.set("gateway_url", settings.url)

    with httpx.Client(headers=headers, timeout=10, verify=settings.verify_tls) as client:
        while not STOP:
            if state.get_bool("sharing_paused", False):
                state.set("status", "paused")
                state.set("last_error", "")
                if once:
                    return 0
                time.sleep(settings.poll_seconds)
                continue

            try:
                now = time.monotonic()
                if now - policy_fetched_at >= settings.policy_refresh_seconds:
                    # Fail closed: if the company's current policy cannot be fetched,
                    # do not upload using a potentially broader stale/default policy.
                    remote = _fetch_policy(client, settings.url)
                    policy = merge_policies(settings.local_policy, remote)
                    policy_fetched_at = now
                    state.set("policy_refreshed_at", _now())

                cursor = state.get_int("last_local_event_id", 0)
                rows = _read_local_rows(db_path, cursor, settings.batch_size)
                if not rows:
                    state.set("status", "connected")
                    state.set("last_error", "")
                    if once:
                        return 0
                    time.sleep(settings.poll_seconds)
                    continue

                state.set("status", "syncing")
                prepared: list[dict[str, Any]] = []
                local_id_by_event_id: dict[str, int] = {}
                last_local_id = cursor
                for local_id, event in rows:
                    last_local_id = local_id
                    if state.skipped(local_id):
                        continue
                    item = prepare_event_for_gateway(event, policy)
                    if item is None:
                        continue
                    event_id = str(item.get("event_id") or "").strip()
                    if not event_id:
                        raise RuntimeError(
                            f"Local evidence row {local_id} is missing event_id; sync cursor was not advanced"
                        )
                    prepared.append(item)
                    local_id_by_event_id[event_id] = local_id

                acknowledged: set[str] = set()
                rejected: dict[str, str] = {}
                if prepared:
                    acknowledged, rejected = _push_batch_resilient(client, settings.url, prepared)
                    expected = set(local_id_by_event_id)
                    processed = acknowledged | set(rejected)
                    if not expected.issubset(processed):
                        raise RuntimeError("Gateway did not acknowledge the complete evidence batch")
                    for event_id, reason in rejected.items():
                        local_id = local_id_by_event_id.get(event_id)
                        if local_id is not None:
                            state.quarantine(local_id, event_id, reason)

                # Policy-excluded, pause-excluded and terminally quarantined rows are
                # intentionally processed. Shareable rows advance only after ACK.
                state.set_int("last_local_event_id", last_local_id)
                state.set("last_success_at", _now())
                state.set_int("last_batch_shared", len(acknowledged))
                state.set_int("quarantined_events", state.quarantine_count())
                state.set("last_error", "")
                state.set("status", "connected")
                delay = settings.poll_seconds
                if once:
                    return len(acknowledged)
            except Exception as exc:
                message = str(exc)[:500]
                state.set("status", "error")
                state.set("last_error", message)
                if once:
                    raise
                print(f"OpenWorkGraph Gateway sync paused by error: {message}")
                time.sleep(delay)
                delay = min(30.0, max(settings.poll_seconds, delay * 1.8))
    state.set("status", "stopped")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize local OpenWorkGraph evidence to a self-hosted Gateway")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(Path(args.config).resolve(), once=args.once)


if __name__ == "__main__":
    main()
