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

from shared.discovery_scope import read_state as read_discovery_state

from .config import load_device_token, load_gateway_settings
from .declared_policy import refresh_managed_declared_policy
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


def _agent_session_local_opt_in(data_dir: Path) -> bool:
    """Read the user's explicit local transcript-sharing choice.

    This is intentionally separate from structural agent sharing. Missing or
    unreadable policy fails closed. The session store owns the setting; the
    connector only reads it so it does not need to import the local server.
    """
    path = data_dir / "agent_session_policy.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(value.get("capture_visible_messages")) and bool(value.get("allow_gateway_session_messages"))


def _read_local_agent_message_rows(db_path: Path, after_id: int, limit: int) -> list[tuple[int, dict[str, Any]]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                """SELECT m.id, m.message_ref, m.session_ref, m.observed_at, m.role, m.content,
                          s.source, s.workspace_ref
                   FROM agent_session_messages m
                   JOIN agent_sessions s ON s.session_ref = m.session_ref
                   WHERE m.id > ? ORDER BY m.id ASC LIMIT ?""",
                (int(after_id), max(1, min(int(limit), 500))),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [
        (
            int(row["id"]),
            {
                "message_ref": str(row["message_ref"] or ""),
                "session_ref": str(row["session_ref"] or ""),
                "observed_at": str(row["observed_at"] or ""),
                "source": str(row["source"] or ""),
                "workspace_ref": str(row["workspace_ref"] or ""),
                "role": str(row["role"] or ""),
                "content": str(row["content"] or ""),
            },
        )
        for row in rows
    ]


def _max_local_agent_message_id(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        try:
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM agent_session_messages").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _push_agent_message_batch(client: httpx.Client, url: str, messages: list[dict[str, Any]]) -> set[str]:
    response = client.post(f"{url}/v1/agent-session-messages/batch", json={"messages": messages})
    response.raise_for_status()
    data = response.json()
    return {str(x) for x in data.get("acknowledged_message_refs") or []}


def _push_agent_message_batch_resilient(
    client: httpx.Client, url: str, messages: list[dict[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    if not messages:
        return set(), {}
    try:
        return _push_agent_message_batch(client, url, messages), {}
    except httpx.HTTPStatusError as exc:
        if not _terminal_http_failure(exc):
            raise
        if len(messages) == 1:
            ref = str(messages[0].get("message_ref") or "")
            try:
                detail = str(exc.response.json().get("detail") or exc.response.text)
            except Exception:
                detail = exc.response.text
            return set(), {ref: f"HTTP {exc.response.status_code}: {detail}"[:500]}
        middle = len(messages) // 2
        left_ok, left_bad = _push_agent_message_batch_resilient(client, url, messages[:middle])
        right_ok, right_bad = _push_agent_message_batch_resilient(client, url, messages[middle:])
        return left_ok | right_ok, left_bad | right_bad


def _sync_agent_session_messages(
    client: httpx.Client, *, url: str, db_path: Path, state: SyncState,
    policy: dict[str, Any], batch_size: int,
) -> int:
    cursor = state.get_int("last_local_agent_message_id", 0)

    # No retrospective sharing: while this distinct channel is disabled, mark
    # currently-retained messages as processed locally. Enabling it later starts
    # with messages created after the opt-in boundary.
    if policy.get("allow_agent_session_messages") is not True:
        current = _max_local_agent_message_id(db_path)
        if current > cursor:
            state.set_int("last_local_agent_message_id", current)
        state.set_int("last_agent_message_batch_shared", 0)
        return 0

    rows = _read_local_agent_message_rows(db_path, cursor, batch_size)
    if not rows:
        state.set_int("last_agent_message_batch_shared", 0)
        return 0

    prepared: list[dict[str, Any]] = []
    id_by_ref: dict[str, int] = {}
    last_local_id = cursor
    discovery = read_discovery_state(data_dir=db_path.parent)
    discovery_holds = bool(discovery.get("enabled"))
    discovery_boundary = int(discovery.get("gateway_agent_message_boundary_id") or 0)
    for local_id, message in rows:
        last_local_id = local_id
        # Discovery Mode never silently uploads evidence/messages created after
        # its start boundary. Advancing the cursor makes the exclusion durable.
        if discovery_holds and local_id > discovery_boundary:
            continue
        if state.agent_message_skipped(local_id):
            continue
        ref = str(message.get("message_ref") or "")
        if not ref:
            # A malformed local row is never uploaded and must not block all
            # later privacy-hardened messages forever.
            state.set(f"agent_message_quarantine:{local_id}", "missing message_ref")
            continue
        prepared.append(message)
        id_by_ref[ref] = local_id

    acknowledged: set[str] = set()
    rejected: dict[str, str] = {}
    if prepared:
        acknowledged, rejected = _push_agent_message_batch_resilient(client, url, prepared)
        expected = set(id_by_ref)
        if not expected.issubset(acknowledged | set(rejected)):
            raise RuntimeError("Gateway did not acknowledge the complete agent-session message batch")
        for ref, reason in rejected.items():
            local_id = id_by_ref.get(ref)
            if local_id is not None:
                state.set(f"agent_message_quarantine:{local_id}", reason)

    state.set_int("last_local_agent_message_id", last_local_id)
    state.set_int("last_agent_message_batch_shared", len(acknowledged))
    return len(acknowledged)


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


def _refresh_managed_policy_if_due(
    client: httpx.Client,
    *,
    settings,
    state: SyncState,
    data_dir: Path,
    fetched_at: float,
    now: float,
) -> float:
    """Refresh managed policy without coupling failure to evidence upload."""
    if not settings.managed_declared_policy_enabled:
        return fetched_at
    if now - fetched_at < settings.managed_declared_policy_refresh_seconds:
        return fetched_at
    try:
        refresh_managed_declared_policy(
            client,
            settings.url,
            settings=settings,
            state=state,
            data_dir=data_dir,
        )
        state.set("managed_declared_policy_refreshed_at", _now())
        state.set("managed_declared_policy_last_error", "")
    except Exception as exc:
        # Managed SOP verification is a separate concern from evidence sharing.
        # Keep the last verified policy, record the problem, and continue normal
        # local capture/Gateway evidence synchronization.
        state.set("managed_declared_policy_status", "error")
        state.set("managed_declared_policy_last_error", str(exc)[:500])
        print(f"OpenWorkGraph managed declared policy refresh failed: {str(exc)[:500]}")
    return now


def run(config_path: Path, *, once: bool = False) -> int:
    """Synchronize privacy-approved local evidence to an optional company Gateway.

    Local capture never depends on this worker. The cursor advances only after a
    complete Gateway acknowledgement, explicit policy/pause exclusion, or local
    quarantine of a terminally invalid event. Network/auth/server failures remain
    retryable and never advance the cursor. Managed declared-policy refresh is
    separately opt-in and cannot make evidence synchronization fail.
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
    remote_policy: dict[str, Any] = {}
    initial_local_policy = dict(settings.local_policy)
    initial_local_policy["allow_agent_session_messages"] = _agent_session_local_opt_in(data_dir)
    policy: dict[str, Any] = merge_policies(initial_local_policy, remote_policy)
    policy_fetched_at = 0.0
    managed_policy_fetched_at = 0.0
    delay = settings.poll_seconds
    state.set("gateway_url", settings.url)

    with httpx.Client(headers=headers, timeout=10, verify=settings.verify_tls) as client:
        while not STOP:
            now = time.monotonic()
            managed_policy_fetched_at = _refresh_managed_policy_if_due(
                client,
                settings=settings,
                state=state,
                data_dir=data_dir,
                fetched_at=managed_policy_fetched_at,
                now=now,
            )

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
                    remote_policy = _fetch_policy(client, settings.url)
                    local_policy = dict(settings.local_policy)
                    # Session messages have their own explicit local opt-in. The
                    # organization may only narrow it; it can never enable it.
                    local_policy["allow_agent_session_messages"] = _agent_session_local_opt_in(data_dir)
                    policy = merge_policies(local_policy, remote_policy)
                    policy_fetched_at = now
                    state.set("policy_refreshed_at", _now())
                    # Read by the local server (server.ai_context) to lock AI
                    # context detail to Redacted when the organization requires it.
                    state.set_bool("org_force_redacted_ai_context", bool(policy.get("force_redacted_ai_context")))

                # Apply local transcript opt-in immediately even between remote
                # policy refreshes, using the last successfully fetched org policy.
                local_policy = dict(settings.local_policy)
                local_policy["allow_agent_session_messages"] = _agent_session_local_opt_in(data_dir)
                policy = merge_policies(local_policy, remote_policy)
                shared_messages = _sync_agent_session_messages(
                    client, url=settings.url, db_path=db_path, state=state,
                    policy=policy, batch_size=settings.batch_size,
                )

                cursor = state.get_int("last_local_event_id", 0)
                rows = _read_local_rows(db_path, cursor, settings.batch_size)
                if not rows:
                    state.set("status", "connected")
                    state.set("last_error", "")
                    if once:
                        return shared_messages
                    time.sleep(settings.poll_seconds)
                    continue

                state.set("status", "syncing")
                prepared: list[dict[str, Any]] = []
                local_id_by_event_id: dict[str, int] = {}
                last_local_id = cursor
                discovery = read_discovery_state(data_dir=data_dir)
                discovery_holds = bool(discovery.get("enabled"))
                discovery_boundary = int(discovery.get("gateway_event_boundary_id") or 0)
                for local_id, event in rows:
                    last_local_id = local_id
                    # Evidence created during an active/reviewing Discovery study
                    # is processed locally but never uploaded by normal Gateway sync.
                    if discovery_holds and local_id > discovery_boundary:
                        continue
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
                    return len(acknowledged) + shared_messages
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
