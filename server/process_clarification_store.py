"""Local ledger of human decisions on process clarification questions (R11)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import DATA_DIR

_LOCK = threading.RLock()
_MAX_PER_PROCESS = 500


def _path() -> Path:
    return DATA_DIR / "process_clarifications.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "processes": {}}
    return data if isinstance(data, dict) and isinstance(data.get("processes"), dict) else {"version": 1, "processes": {}}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-clarify-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def decisions(workflow_id: str, revision: int, content_sha256: str) -> dict[str, dict[str, Any]]:
    """Decisions recorded for this exact revision content; other revisions ask again."""
    with _LOCK:
        entries = _read()["processes"].get(workflow_id, [])
    return {e["question_id"]: e for e in entries
            if e.get("revision") == revision and e.get("content_sha256") == content_sha256}


def record(workflow_id: str, revision: int, content_sha256: str, question: dict[str, Any],
           decision: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    entry = {
        "question_id": question["question_id"], "kind": question["kind"], "target_id": question["target_id"],
        "prompt": question["prompt"], "revision": revision, "content_sha256": content_sha256,
        "decision": decision["decision"], "answer": decision.get("answer", ""),
        "cited_statements": citations, "decided_at": datetime.now(timezone.utc).isoformat(),
        "decided_by": "local_owner", "changes_model_automatically": False,
    }
    with _LOCK:
        data = _read()
        entries = [e for e in data["processes"].get(workflow_id, [])
                   if not (e["question_id"] == entry["question_id"] and e["revision"] == revision)]
        entries.append(entry)
        data["processes"][workflow_id] = entries[-_MAX_PER_PROCESS:]
        _write(data)
    return entry


def history(workflow_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_read()["processes"].get(workflow_id, []))


def forget(workflow_id: str) -> None:
    with _LOCK:
        data = _read()
        if data["processes"].pop(workflow_id, None) is not None:
            _write(data)
