from __future__ import annotations

"""Time ranges in which the user also deleted AI conversation records.

Kept separate from evidence tombstones: a range deletion only blocks re-import
of agent conversation messages and working detail when the user explicitly
chose to delete those too. Native AI-tool session files are never modified.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .evidence_deletion import _coalesce, _parse, data_dir, normalize_range

_LOCK = threading.RLock()


def state_path() -> Path:
    return data_dir() / "agent_conversation_deletions.json"


def read_state() -> dict[str, Any]:
    with _LOCK:
        try:
            value = json.loads(state_path().read_text(encoding="utf-8"))
            intervals = value.get("intervals") if isinstance(value, dict) else []
        except Exception:
            intervals = []
        return {"version": 1, "intervals": _coalesce(intervals if isinstance(intervals, list) else [])}


def add_tombstone(since: str, until: str) -> dict[str, Any]:
    normalized_since, normalized_until = normalize_range(since, until)
    with _LOCK:
        value = read_state()
        value["intervals"] = _coalesce([*value["intervals"], {"since": normalized_since, "until": normalized_until}])
        path = state_path()
        fd, tmp = tempfile.mkstemp(prefix="agent-conversation-deletions-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(value, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return value


def is_deleted(observed_at: Any, *, state: dict[str, Any] | None = None) -> bool:
    observed = _parse(str(observed_at or ""))
    if observed is None:
        return False
    for item in (state or read_state())["intervals"]:
        start, end = _parse(item["since"]), _parse(item["until"])
        if start is not None and end is not None and start <= observed < end:
            return True
    return False


__all__ = ["add_tombstone", "is_deleted", "read_state", "state_path"]
