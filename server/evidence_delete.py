from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.evidence_deletion import event_overlaps_range, normalize_range
from .db import DATA_DIR, connect


def _event_from_row(row: Any) -> dict[str, Any]:
    value = dict(row)
    try:
        value["metadata"] = json.loads(value.pop("metadata_json", "{}") or "{}")
    except Exception:
        value["metadata"] = {}
    return value


def _delete_ids(conn, table: str, event_ids: list[str]) -> None:
    for offset in range(0, len(event_ids), 500):
        chunk = event_ids[offset : offset + 500]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM {table} WHERE event_id IN ({placeholders})", tuple(chunk))


def delete_database_range(since: str, until: str) -> dict[str, Any]:
    """Delete canonical evidence and its two derived local representations.

    Duration events are deleted when any part overlaps [since, until), rather than
    only when their start timestamp falls inside the range. This avoids retaining
    a focus span that still reveals work inside the deleted interval.
    """
    normalized_since, normalized_until = normalize_range(since, until)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, event_id, observed_at, duration_seconds, screenshot_path, metadata_json FROM events ORDER BY id ASC"
        ).fetchall()
        selected = [row for row in rows if event_overlaps_range(_event_from_row(row), normalized_since, normalized_until)]
        local_ids = [int(row["id"]) for row in selected]
        event_ids = [str(row["event_id"]) for row in selected]
        screenshots = [str(row["screenshot_path"]) for row in selected if row["screenshot_path"]]
        if event_ids:
            _delete_ids(conn, "context_events", event_ids)
            _delete_ids(conn, "normalized_events", event_ids)
            _delete_ids(conn, "events", event_ids)
    # Deleting a time range also forgets any remembered run inside it.
    from .run_memory import forget_range

    memory_deleted = forget_range(normalized_since, normalized_until)
    return {
        "deleted_events": len(event_ids),
        "run_memory_deleted": memory_deleted,
        "local_ids": local_ids,
        "event_ids": event_ids,
        "screenshot_paths": screenshots,
        "since": normalized_since,
        "until": normalized_until,
    }


def rewrite_jsonl_range(since: str, until: str) -> int:
    """Remove deleted evidence from the collector's auxiliary JSONL copies."""
    normalized_since, normalized_until = normalize_range(since, until)
    removed = 0
    for path in (DATA_DIR / "events.jsonl", DATA_DIR / "events.jsonl.1"):
        if not path.exists() or not path.is_file():
            continue
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".delete-", suffix=".tmp", dir=str(path.parent))
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source, os.fdopen(fd, "w", encoding="utf-8") as target:
                for line in source:
                    try:
                        event = json.loads(line)
                    except Exception:
                        # Malformed legacy lines cannot be reliably ranged. Preserve
                        # them rather than silently deleting unrelated evidence.
                        target.write(line)
                        continue
                    if isinstance(event, dict) and event_overlaps_range(event, normalized_since, normalized_until):
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


def remove_local_screenshots(paths: list[str]) -> int:
    """Delete only screenshot paths that resolve inside the local data directory."""
    root = DATA_DIR.resolve()
    removed = 0
    for raw in paths:
        try:
            path = Path(raw).expanduser().resolve()
            path.relative_to(root)
        except Exception:
            continue
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except Exception:
            continue
    return removed


def delete_agent_conversations_range(since: str, until: str) -> dict[str, int]:
    """Delete agent conversation messages and working detail observed in [since, until)."""
    normalized_since, normalized_until = normalize_range(since, until)
    start, end = _parse_utc(normalized_since), _parse_utc(normalized_until)
    counts = {"agent_session_messages": 0, "agent_working_detail": 0}
    with connect() as conn:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, key in (("agent_session_messages", "id"), ("agent_working_detail", "detail_ref")):
            if table not in tables:
                continue
            doomed = [row[0] for row in conn.execute(f"SELECT {key}, observed_at FROM {table}")
                      if (stamp := _parse_utc(row[1])) is not None and start <= stamp < end]
            for offset in range(0, len(doomed), 500):
                chunk = doomed[offset:offset + 500]
                conn.execute(f"DELETE FROM {table} WHERE {key} IN ({','.join('?' for _ in chunk)})", chunk)
            counts[table] = len(doomed)
        if counts["agent_session_messages"] and "agent_sessions" in tables:
            conn.execute(
                """UPDATE agent_sessions SET visible_message_count = (SELECT COUNT(*) FROM agent_session_messages m
                   WHERE m.session_ref = agent_sessions.session_ref)""")
    return counts


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
