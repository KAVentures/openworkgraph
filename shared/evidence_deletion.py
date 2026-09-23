from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    path = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return data_dir() / "evidence_deletions.json"


def audit_path() -> Path:
    return data_dir() / "evidence_deletion_audit.jsonl"


def normalize_range(since: str, until: str) -> tuple[str, str]:
    start = _parse(since)
    end = _parse(until)
    if start is None or end is None:
        raise ValueError("since and until must be timezone-aware ISO timestamps")
    if end <= start:
        raise ValueError("until must be later than since")
    return start.isoformat(), end.isoformat()


def event_overlaps_range(event: dict[str, Any], since: str, until: str) -> bool:
    """Return whether an event contributes evidence inside [since, until)."""
    normalized_since, normalized_until = normalize_range(since, until)
    range_start = _parse(normalized_since)
    range_end = _parse(normalized_until)
    observed = _parse(str(event.get("observed_at") or ""))
    if range_start is None or range_end is None or observed is None:
        return False
    duration = max(0.0, float(event.get("duration_seconds") or 0.0))
    if duration <= 0:
        return range_start <= observed < range_end
    finish = observed + timedelta(seconds=duration)
    return observed < range_end and finish > range_start


def _coalesce(intervals: list[dict[str, Any]]) -> list[dict[str, str]]:
    parsed: list[tuple[datetime, datetime]] = []
    for item in intervals:
        if not isinstance(item, dict):
            continue
        start = _parse(str(item.get("since") or ""))
        end = _parse(str(item.get("until") or ""))
        if start is None or end is None or end <= start:
            continue
        parsed.append((start, end))
    parsed.sort(key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = []
    for start, end in parsed:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return [
        {"since": start.isoformat(), "until": end.isoformat(), "reason": "user_deleted"}
        for start, end in merged
    ]


def _read_unlocked() -> dict[str, Any]:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("deletion state must be an object")
    except Exception:
        value = {"version": 1, "intervals": []}
    intervals = value.get("intervals") if isinstance(value.get("intervals"), list) else []
    return {"version": 1, "intervals": _coalesce(intervals)}


def _write_unlocked(value: dict[str, Any]) -> dict[str, Any]:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="evidence-deletions-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
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
    return copy.deepcopy(value)


def read_state() -> dict[str, Any]:
    with _LOCK:
        return _read_unlocked()


def add_tombstone(since: str, until: str) -> dict[str, Any]:
    normalized_since, normalized_until = normalize_range(since, until)
    with _LOCK:
        value = _read_unlocked()
        value["intervals"] = _coalesce([
            *(value.get("intervals") or []),
            {"since": normalized_since, "until": normalized_until, "reason": "user_deleted"},
        ])
        return _write_unlocked(value)


def timestamp_is_deleted(observed_at: str | None, *, state: dict[str, Any] | None = None) -> bool:
    observed = _parse(observed_at)
    if observed is None:
        return False
    value = state or read_state()
    for item in value.get("intervals") or []:
        start = _parse(str(item.get("since") or ""))
        end = _parse(str(item.get("until") or ""))
        if start is not None and end is not None and start <= observed < end:
            return True
    return False


def prepare_recordable_event(event: dict[str, Any], *, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Prevent deleted local evidence from being recreated by late delivery.

    Events whose timestamp falls inside a deleted range are discarded. A duration
    event that starts before a deleted range but crosses into it is clipped at the
    first deletion boundary. We intentionally do not synthesize a post-deletion
    tail because doing so could reconstruct evidence the user asked to remove.
    """
    value = state or read_state()
    start = _parse(str(event.get("observed_at") or ""))
    if start is None:
        return dict(event)
    if timestamp_is_deleted(str(event.get("observed_at") or ""), state=value):
        return None

    duration = max(0.0, float(event.get("duration_seconds") or 0.0))
    if duration <= 0:
        return dict(event)
    finish = start + timedelta(seconds=duration)
    boundary: datetime | None = None
    for item in value.get("intervals") or []:
        deleted_start = _parse(str(item.get("since") or ""))
        deleted_end = _parse(str(item.get("until") or ""))
        if deleted_start is None or deleted_end is None:
            continue
        if deleted_start <= start < deleted_end:
            return None
        if start < deleted_start < finish and (boundary is None or deleted_start < boundary):
            boundary = deleted_start
    if boundary is None:
        return dict(event)

    clipped = copy.deepcopy(event)
    clipped["duration_seconds"] = max(0.0, (boundary - start).total_seconds())
    metadata = clipped.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        clipped["metadata"] = metadata
    metadata["local_deletion_clipped"] = True
    metadata["local_deletion_original_duration_seconds"] = duration
    return clipped if clipped["duration_seconds"] > 0 else None


def audit_deletion(
    *,
    since: str,
    until: str,
    deleted_events: int,
    pruned_outbox_events: int,
    skipped_gateway_rows: int,
    screenshots_removed: int,
) -> None:
    normalized_since, normalized_until = normalize_range(since, until)
    record = {
        "deleted_at": _now(),
        "since": normalized_since,
        "until": normalized_until,
        "deleted_events": max(0, int(deleted_events)),
        "pruned_outbox_events": max(0, int(pruned_outbox_events)),
        "skipped_gateway_rows": max(0, int(skipped_gateway_rows)),
        "screenshots_removed": max(0, int(screenshots_removed)),
    }
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
