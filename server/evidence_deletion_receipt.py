"""Independent post-deletion verification and a content-free deletion receipt.

``/v1/evidence/delete`` reports what it removed. This module re-reads every
local time-indexed store afterwards and counts rows that still overlap the
range, using the same overlap rules as the deletion itself. Stores that the
range deletion deliberately does not cover are counted separately so the
receipt never overstates what was erased. Only counts, the range and hashes
are returned: no event, title, URL or message content.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from shared.evidence_deletion import event_overlaps_range, normalize_range

from .db import DATA_DIR, connect

RECEIPT_FORMAT = "openworkgraph.local-deletion-receipt.v1"


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _overlapping_events(conn: sqlite3.Connection, table: str, since: str, until: str, *, duration: bool) -> int:
    columns = "observed_at, duration_seconds" if duration else "observed_at"
    count = 0
    for row in conn.execute(f"SELECT {columns} FROM {table}"):
        event = {"observed_at": row[0], "duration_seconds": row[1] if duration else 0}
        if event_overlaps_range(event, since, until):
            count += 1
    return count


def _in_range(conn: sqlite3.Connection, table: str, column: str, start: datetime, end: datetime) -> int:
    count = 0
    for (value,) in conn.execute(f"SELECT {column} FROM {table}"):
        stamp = _parse(value)
        if stamp is not None and start <= stamp < end:
            count += 1
    return count


def _run_memory(conn: sqlite3.Connection, start: datetime, end: datetime) -> int:
    count = 0
    for began_raw, ended_raw in conn.execute("SELECT started_at, ended_at FROM run_memory"):
        began = _parse(began_raw)
        ended = _parse(ended_raw) or began
        if began is not None and began < end and ended >= start:
            count += 1
    return count


def _outbox(path: Path, since: str, until: str) -> int:
    if not path.is_file():
        return 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as conn:
        if "pending_events" not in _tables(conn):
            return 0
        count = 0
        for (payload,) in conn.execute("SELECT payload_json FROM pending_events"):
            try:
                event = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if isinstance(event, dict) and event_overlaps_range(event, since, until):
                count += 1
        return count


def _jsonl(paths: list[Path], since: str, until: str) -> int:
    count = 0
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event_overlaps_range(event, since, until):
                    count += 1
    return count


def _work_text(start: datetime, end: datetime) -> int:
    from . import work_text_capture

    path = Path(work_text_capture._DB)
    if not path.is_file():
        return 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as conn:
        if "work_text" not in _tables(conn):
            return 0
        return _in_range(conn, "work_text", "observed_at", start, end)


def verify_range(since: str, until: str, *, include_agent_conversations: bool = False) -> dict[str, Any]:
    """Count rows that still overlap [since, until) in every local time-indexed store.

    When the deletion also covered agent conversations, those stores must be
    empty too; otherwise they are reported separately as intentionally kept.
    """
    since, until = normalize_range(since, until)
    start, end = _parse(since), _parse(until)
    covered: dict[str, int] = {}
    separate: dict[str, int] = {}
    with connect() as conn:
        tables = _tables(conn)
        covered["events"] = _overlapping_events(conn, "events", since, until, duration=True)
        covered["normalized_events"] = _overlapping_events(conn, "normalized_events", since, until, duration=True)
        covered["context_events"] = _overlapping_events(conn, "context_events", since, until, duration=False)
        covered["run_memory"] = _run_memory(conn, start, end) if "run_memory" in tables else 0
        covered["outcome_watch"] = (
            _in_range(conn, "outcome_watch", "created_at", start, end) if "outcome_watch" in tables else 0)
        covered["agent_brief_log"] = (
            _in_range(conn, "agent_brief_log", "delivered_at", start, end) if "agent_brief_log" in tables else 0)
        # Deleted only by their own explicit "delete all" controls, not by a time range.
        separate["agent_session_messages"] = (
            _in_range(conn, "agent_session_messages", "observed_at", start, end)
            if "agent_session_messages" in tables else 0)
        separate["agent_working_detail"] = (
            _in_range(conn, "agent_working_detail", "observed_at", start, end)
            if "agent_working_detail" in tables else 0)
    if include_agent_conversations:
        covered.update(separate)
        separate = {}
    covered["work_text"] = _work_text(start, end)
    covered["collector_outbox"] = _outbox(DATA_DIR / "collector_outbox.db", since, until)
    covered["jsonl"] = _jsonl([DATA_DIR / "events.jsonl", DATA_DIR / "events.jsonl.1"], since, until)
    return {
        "remaining_in_deleted_stores": covered,
        "verified_clean": not any(covered.values()),
        "remaining_in_separately_deleted_stores": separate,
        "agent_conversations_included": include_agent_conversations,
        "separately_deleted_stores_note": (
            "Agent conversation messages and working detail were included in this deletion."
            if include_agent_conversations else
            "Agent conversation messages and working detail were kept; choose the option to delete "
            "them with the time range, or use their own delete controls."
        ),
    }


def receipt(deletion: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    """A content-free digest of the deletion result and its independent verification."""
    body = {
        "format": RECEIPT_FORMAT,
        "since": deletion.get("since"),
        "until": deletion.get("until"),
        "deleted": {key: value for key, value in sorted(deletion.items()) if isinstance(value, int)
                    and not isinstance(value, bool)},
        "verification": verification,
        "gateway_recall_performed": bool(deletion.get("gateway_recall_performed")),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "sha256": digest}


__all__ = ["verify_range", "receipt", "RECEIPT_FORMAT"]
