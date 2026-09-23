from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_MAX_INTERVALS = 500
_VALID_STATES = {"recording", "paused", "stopped"}


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
    return data_dir() / "capture_control.json"


def _default(run_started_at: str | None = None) -> dict[str, Any]:
    start = str(run_started_at or os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or _now())
    return {
        "version": 1,
        "state": "recording",
        "run_started_at": start,
        "state_changed_at": start,
        "generation": 1,
        "skip_intervals": [],
    }


def _read_unlocked() -> dict[str, Any]:
    path = state_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("capture state must be an object")
    except Exception:
        return _default()
    state = str(value.get("state") or "recording")
    if state not in _VALID_STATES:
        state = "recording"
    value["state"] = state
    if not isinstance(value.get("skip_intervals"), list):
        value["skip_intervals"] = []
    value["skip_intervals"] = [
        item for item in value["skip_intervals"][-_MAX_INTERVALS:]
        if isinstance(item, dict) and item.get("since")
    ]
    value.setdefault("generation", 1)
    value.setdefault("run_started_at", os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or _now())
    value.setdefault("state_changed_at", value["run_started_at"])
    return value


def _write_unlocked(value: dict[str, Any]) -> dict[str, Any]:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="capture-control-", suffix=".tmp", dir=str(path.parent))
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
    return dict(value)


def _close_open_interval(value: dict[str, Any], at: str) -> None:
    intervals = value.setdefault("skip_intervals", [])
    for item in reversed(intervals):
        if not item.get("until"):
            item["until"] = at
            return


def initialize_run(run_started_at: str | None = None) -> dict[str, Any]:
    """Start the launcher run in recording mode while preserving old skip tombstones."""
    start = str(run_started_at or os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") or _now())
    with _LOCK:
        value = _read_unlocked()
        previous_start = str(value.get("run_started_at") or "")
        if previous_start == start:
            return value
        _close_open_interval(value, start)
        value["state"] = "recording"
        value["run_started_at"] = start
        value["state_changed_at"] = start
        value["generation"] = int(value.get("generation") or 0) + 1
        value["skip_intervals"] = list(value.get("skip_intervals") or [])[-_MAX_INTERVALS:]
        return _write_unlocked(value)


def read_state() -> dict[str, Any]:
    with _LOCK:
        return _read_unlocked()


def set_state(action: str, *, at: str | None = None) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in {"pause", "resume", "stop", "start"}:
        raise ValueError("capture action must be pause, resume, stop, or start")
    changed = str(at or _now())
    with _LOCK:
        value = _read_unlocked()
        current = str(value.get("state") or "recording")
        intervals = value.setdefault("skip_intervals", [])

        if action == "pause":
            if current == "recording":
                intervals.append({"since": changed, "until": None, "reason": "capture_paused"})
                value["state"] = "paused"
                value["state_changed_at"] = changed
        elif action == "resume":
            if current == "paused":
                _close_open_interval(value, changed)
                value["state"] = "recording"
                value["state_changed_at"] = changed
        elif action == "stop":
            if current == "recording":
                intervals.append({"since": changed, "until": None, "reason": "capture_stopped"})
            elif current == "paused":
                for item in reversed(intervals):
                    if not item.get("until"):
                        item["reason"] = "capture_paused_then_stopped"
                        break
            value["state"] = "stopped"
            value["state_changed_at"] = changed
        else:  # start a new run without restarting the local API
            _close_open_interval(value, changed)
            value["state"] = "recording"
            value["run_started_at"] = changed
            value["state_changed_at"] = changed
            value["generation"] = int(value.get("generation") or 0) + 1

        value["skip_intervals"] = intervals[-_MAX_INTERVALS:]
        return _write_unlocked(value)


def timestamp_is_skipped(observed_at: str | None, *, state: dict[str, Any] | None = None) -> bool:
    observed = _parse(observed_at)
    if observed is None:
        return False
    value = state or read_state()
    for interval in value.get("skip_intervals") or []:
        if not isinstance(interval, dict):
            continue
        start = _parse(str(interval.get("since") or ""))
        end = _parse(str(interval.get("until") or "")) if interval.get("until") else None
        if start is None:
            continue
        if observed >= start and (end is None or observed < end):
            return True
    return False


def filter_recordable(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    state = read_state()
    kept = [event for event in events if not timestamp_is_skipped(str(event.get("observed_at") or ""), state=state)]
    return kept, len(events) - len(kept)
