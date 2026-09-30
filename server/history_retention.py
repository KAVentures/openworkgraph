from __future__ import annotations

"""User-controlled local history catalogue and retention cleanup."""

import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collector.outbox import EventOutbox
from connector.state import SyncState
from shared.history_policy import (
    add_session_tombstones,
    initialize_policy,
    read_policy,
    retention_for_kind,
)
from . import analytics, run_memory
from .agent_execution_traces import agent_execution_traces
from .context_layers import factual_context_timeline
from .db import DATA_DIR, connect
from .evidence_delete import remove_local_screenshots

_ACTIVITY_BREAK_SECONDS = 30 * 60


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _event(row: Any) -> dict[str, Any]:
    value = dict(row)
    value.pop("id", None)
    raw = value.pop("metadata_json", "{}")
    try:
        value["metadata"] = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
    except Exception:
        value["metadata"] = {}
    return value


def _is_agent(row: dict[str, Any]) -> bool:
    if str(row.get("source") or "").lower() == "agent":
        return True
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(meta.get("actor_kind") or "").lower() == "agent"


def _all_rows() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY observed_at ASC, id ASC").fetchall()
    return [_event(row) for row in rows]


def _kind_rows(kind: str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source = rows if rows is not None else _all_rows()
    agent = str(kind).lower() == "agent"
    return [row for row in source if _is_agent(row) == agent]


def _session_ids(kind: str, rows: list[dict[str, Any]] | None = None) -> list[str]:
    return sorted({str(row.get("session_id") or "") for row in _kind_rows(kind, rows) if row.get("session_id")})


def _delete_ids(conn, table: str, event_ids: list[str]) -> None:
    for offset in range(0, len(event_ids), 500):
        chunk = event_ids[offset : offset + 500]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM {table} WHERE event_id IN ({placeholders})", tuple(chunk))


def _rewrite_jsonl_sessions(kind: str, session_ids: set[str]) -> int:
    removed = 0
    if not session_ids:
        return 0
    for path in (DATA_DIR / "events.jsonl", DATA_DIR / "events.jsonl.1"):
        if not path.exists() or not path.is_file():
            continue
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".history-", suffix=".tmp", dir=str(path.parent))
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source, os.fdopen(fd, "w", encoding="utf-8") as target:
                for line in source:
                    try:
                        event = json.loads(line)
                    except Exception:
                        target.write(line)
                        continue
                    if isinstance(event, dict):
                        row_kind = "agent" if _is_agent(event) else "human"
                        if row_kind == kind and str(event.get("session_id") or "") in session_ids:
                            removed += 1
                            continue
                    target.write(line)
                target.flush()
                os.fsync(target.fileno())
            os.replace(tmp_name, path)
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
    return removed


def delete_sessions(kind: str, session_ids: list[str], *, reason: str) -> dict[str, Any]:
    selected = "agent" if str(kind).lower() == "agent" else "human"
    ids = {str(value) for value in session_ids if str(value)}
    if not ids:
        return {"kind": selected, "sessions_deleted": 0, "events_deleted": 0, "local_ids": []}

    # Tombstone first so late native/browser/outbox delivery cannot recreate a
    # session while cleanup is in progress or after a crash between cleanup steps.
    add_session_tombstones(selected, sorted(ids), reason=reason)
    user_deleted = reason.startswith("user_")

    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
        chosen = []
        for raw in rows:
            item = _event(raw)
            if str(item.get("session_id") or "") not in ids:
                continue
            if ("agent" if _is_agent(item) else "human") != selected:
                continue
            chosen.append(raw)
        chosen_events = [_event(row) for row in chosen]
        event_ids = [str(row["event_id"]) for row in chosen]
        local_ids = [int(row["id"]) for row in chosen]
        screenshots = [str(row["screenshot_path"]) for row in chosen if row["screenshot_path"]]
    # Retention keeps a content-free record of each run (run memory) before the
    # raw evidence goes; a person deleting the session deletes that memory too.
    memory_kept = memory_deleted = 0
    try:
        if user_deleted:
            memory_deleted = run_memory.forget_sessions(ids)
        elif chosen_events:
            memory_kept = run_memory.remember(chosen_events)
    except Exception:
        memory_kept = 0
    with connect() as conn:
        if event_ids:
            _delete_ids(conn, "context_events", event_ids)
            _delete_ids(conn, "normalized_events", event_ids)
            _delete_ids(conn, "events", event_ids)

    jsonl_removed = _rewrite_jsonl_sessions(selected, ids)
    outbox = EventOutbox(DATA_DIR / "collector_outbox.db")
    outbox_removed = outbox.prune_sessions(selected, sorted(ids))
    state = SyncState(DATA_DIR / "gateway_sync_state.db")
    skipped_gateway = state.add_skip_ids(local_ids, f"history_{reason}") if local_ids else 0
    screenshots_removed = remove_local_screenshots(screenshots)
    analytics.clear_summary_cache()
    return {
        "kind": selected,
        "sessions_deleted": len(ids),
        "events_deleted": len(event_ids),
        "local_ids": local_ids,
        "jsonl_events_removed": jsonl_removed,
        "collector_outbox_events_removed": outbox_removed,
        "gateway_local_rows_marked_never_share": skipped_gateway,
        "screenshots_removed": screenshots_removed,
        "late_delivery_suppressed": True,
        "run_memory_kept": memory_kept,
        "run_memory_deleted": memory_deleted,
    }


def initialize_history_retention() -> dict[str, Any]:
    with connect() as conn:
        has_existing = bool(conn.execute("SELECT 1 FROM events LIMIT 1").fetchone())
    policy = initialize_policy(has_existing_evidence=has_existing)
    cleanup_expired_history(startup=True)
    return policy


def cleanup_expired_history(*, startup: bool = False) -> dict[str, Any]:
    rows = _all_rows()
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for kind in ("human", "agent"):
        retention = retention_for_kind(kind)
        by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _kind_rows(kind, rows):
            sid = str(row.get("session_id") or "")
            if sid:
                by_session[sid].append(row)
        delete_ids: list[str] = []
        if retention.get("mode") == "ephemeral" and startup:
            # No current-run evidence exists when called from lifespan startup, so
            # every surviving ephemeral session is a stale/crash-recovery session.
            delete_ids = sorted(by_session)
        elif retention.get("mode") == "days":
            cutoff = now - timedelta(days=int(retention.get("days") or 1))
            for sid, members in by_session.items():
                latest = max((_parse(row.get("observed_at")) for row in members), default=None)
                if latest is not None and latest < cutoff:
                    delete_ids.append(sid)
        if delete_ids:
            results.append(delete_sessions(kind, delete_ids, reason="retention_expired"))
    try:
        memory_pruned = run_memory.prune(now=now)
    except Exception:
        memory_pruned = 0
    return {"startup": startup, "cleanups": results, "run_memory_pruned": memory_pruned}


def cleanup_ephemeral_session(kind: str, session_id: str) -> dict[str, Any]:
    retention = retention_for_kind(kind)
    if retention.get("mode") != "ephemeral" or not str(session_id or ""):
        return {"kind": kind, "sessions_deleted": 0, "events_deleted": 0}
    return delete_sessions(kind, [session_id], reason="ephemeral_session_closed")


def _activity_blocks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("observed_at") or ""))
    blocks: list[dict[str, Any]] = []
    for row in ordered:
        start = _parse(row.get("observed_at"))
        if start is None:
            continue
        duration = max(0.0, float(row.get("duration_seconds") or 0.0))
        end = start + timedelta(seconds=duration)
        if not blocks:
            blocks.append({"started_at": start.isoformat(), "ended_at": end.isoformat()})
            continue
        previous_end = _parse(blocks[-1]["ended_at"]) or start
        if (start - previous_end).total_seconds() > _ACTIVITY_BREAK_SECONDS:
            blocks.append({"started_at": start.isoformat(), "ended_at": end.isoformat()})
        elif end > previous_end:
            blocks[-1]["ended_at"] = end.isoformat()
    return blocks


def _human_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _kind_rows("human", rows):
        sid = str(row.get("session_id") or "")
        if sid:
            grouped[sid].append(row)

    # Reuse the factual focus projection for engaged time/surfaces while keeping
    # session identity and raw event counts grounded in canonical rows.
    timeline = factual_context_timeline(_raw_events=_kind_rows("human", rows))
    spans_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in timeline:
        spans_by_session[str(span.get("session_id") or "")].append(span)

    output: list[dict[str, Any]] = []
    retention = retention_for_kind("human")
    for sid, members in grouped.items():
        starts = [_parse(row.get("observed_at")) for row in members]
        starts = [value for value in starts if value is not None]
        if not starts:
            continue
        ended = []
        for row in members:
            start = _parse(row.get("observed_at"))
            if start is not None:
                ended.append(start + timedelta(seconds=max(0.0, float(row.get("duration_seconds") or 0.0))))
        spans = spans_by_session.get(sid, [])
        surfaces = sorted({str(span.get("work_surface") or span.get("container_app") or "") for span in spans if span.get("work_surface") or span.get("container_app")})
        expires_at = None
        if retention.get("mode") == "days":
            expires_at = (max(ended or starts) + timedelta(days=int(retention.get("days") or 1))).isoformat()
        output.append({
            "history_session_id": f"human:{sid}",
            "kind": "human",
            "started_at": min(starts).isoformat(),
            "ended_at": max(ended or starts).isoformat(),
            "activity_blocks": _activity_blocks(members),
            "event_count": len(members),
            "engaged_seconds": round(sum(float(span.get("engaged_seconds") or 0.0) for span in spans), 3),
            "surfaces": surfaces[:20],
            "retention_mode": retention.get("mode"),
            "expires_at": expires_at,
        })
    return output


def _agent_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agents = _kind_rows("agent", rows)
    if not agents:
        return []
    payload = agent_execution_traces(agents, limit=1000, max_events_per_execution=1)
    retention = retention_for_kind("agent")
    output = []
    for execution in payload.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        expires_at = None
        end = _parse(execution.get("ended_at") or execution.get("started_at"))
        if retention.get("mode") == "days" and end is not None:
            expires_at = (end + timedelta(days=int(retention.get("days") or 1))).isoformat()
        output.append({
            "history_session_id": str(execution.get("execution_id") or ""),
            "kind": "agent",
            "started_at": execution.get("started_at"),
            "ended_at": execution.get("ended_at"),
            "agent": execution.get("agent"),
            "observation_level": execution.get("observation_level"),
            "outcome_status": execution.get("outcome_status"),
            "event_count": int(execution.get("event_count_total") or 0),
            "retention_mode": retention.get("mode"),
            "expires_at": expires_at,
        })
    return output


def list_history(*, since: str | None = None, until: str | None = None, limit: int = 200) -> dict[str, Any]:
    rows = _all_rows()
    start = _parse(since) if since else None
    end = _parse(until) if until else None
    if since and start is None:
        raise ValueError("invalid history since")
    if until and end is None:
        raise ValueError("invalid history until")
    if start and end and end <= start:
        raise ValueError("history until must be after since")
    if start or end:
        filtered = []
        for row in rows:
            observed = _parse(row.get("observed_at"))
            if observed is None:
                continue
            if start and observed < start:
                continue
            if end and observed >= end:
                continue
            filtered.append(row)
        rows = filtered
    sessions = [*_human_sessions(rows), *_agent_sessions(rows)]
    sessions.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    bounded = max(1, min(int(limit), 1000))
    returned = sessions[:bounded]
    policy = read_policy()
    return {
        "sessions": returned,
        "returned": len(returned),
        "total": len(sessions),
        "has_more": len(sessions) > len(returned),
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
        "history_generation": int(policy.get("history_generation") or 1),
        "retention": {
            "human": policy.get("human_retention"),
            "agent": policy.get("agent_retention"),
        },
        "session_semantics": {
            "human": "one recording run; idle gaps over 30 minutes are activity blocks inside the same session",
            "agent": "observed agent execution boundary; surface-only boundaries are best-effort and declare observation_level",
        },
        "derived_task_labels_used": False,
    }


def delete_history_session(history_session_id: str) -> dict[str, Any]:
    raw = str(history_session_id or "").strip()
    if raw.startswith("human:"):
        return delete_sessions("human", [raw[len("human:"):]], reason="user_deleted_session")

    # Agent history exposes OWG execution IDs, not native run/session identifiers.
    rows = _kind_rows("agent")
    payload = agent_execution_traces(rows, execution_id=raw, limit=1, max_events_per_execution=1000)
    executions = list(payload.get("executions") or [])
    if not executions:
        # Raw history already gone: the run may survive only as run memory.
        forgotten = run_memory.forget_execution(raw)
        if forgotten:
            return {"kind": "agent", "sessions_deleted": 0, "events_deleted": 0, "local_ids": [], "run_memory_deleted": forgotten}
        raise ValueError("history session not found")
    event_ids = {str(event.get("event_id") or "") for event in executions[0].get("events") or [] if event.get("event_id")}
    native_sessions = sorted({str(row.get("session_id") or "") for row in rows if str(row.get("event_id") or "") in event_ids and row.get("session_id")})
    if not native_sessions:
        raise ValueError("agent history session has no deletable canonical session")
    return delete_sessions("agent", native_sessions, reason="user_deleted_session")


__all__ = [
    "cleanup_ephemeral_session",
    "cleanup_expired_history",
    "delete_history_session",
    "delete_sessions",
    "initialize_history_retention",
    "list_history",
]
