from __future__ import annotations

"""Content-free run memory that outlives raw history.

Retention ("Don't keep after session", or a number of days) deletes raw
evidence. Without memory, repeated-workflow and similar-run features would then
have nothing to learn from. So just before retention removes a session, each run
in it is kept as one small, content-free record: the same structural execution
that procedural memory derives (family, readable step tokens, outcome, timing,
approval points) plus, for agents, the work summary (commands, test outcome,
file counts, lines, tokens).

Rules:

* Nothing new is read: a record is derived only from evidence that was already
  stored, and holds no titles, URLs, paths, prompts or tool content.
* A person deleting a session or a time range deletes its memory too. Only
  retention (ephemeral close, expiry, crash recovery) keeps memory.
* Memory has its own retention (``run_memory.days``, default 90) and switch.
  Turning it off deletes all of it.
* It stays on this computer; the Gateway connector syncs only the events table.
* Native session and run identifiers are stored only as keyed hashes; the key
  (``.run_memory_key``) never leaves this computer.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from shared.history_policy import run_memory_policy

from .db import connect

_TABLE = """
CREATE TABLE IF NOT EXISTS run_memory (
  run_key TEXT PRIMARY KEY,
  actor_kind TEXT NOT NULL,
  family_key TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  ended_at TEXT NOT NULL DEFAULT '',
  session_refs TEXT NOT NULL DEFAULT '[]',
  record_json TEXT NOT NULL,
  remembered_at TEXT NOT NULL
)
"""
_RECORD_KEYS = (
    "execution_id", "actor_kind", "family_key", "family_basis", "started_at", "ended_at",
    "duration_seconds", "outcome_status", "outcome_basis", "positive_example", "explicit_failure",
    "steps", "observation_level", "evidence_window", "_approval_points",
)
_AGENT_KEYS = ("agent", "work_summary", "usage_totals", "models_observed", "parent_execution_id", "child_execution_ids")


def _key() -> bytes:
    from .local_auth import _read_or_create_secret

    return _read_or_create_secret(".run_memory_key").encode("utf-8")


def _digest(material: str, *, key: bytes | None = None) -> str:
    return hmac.new(key if key is not None else _key(), material.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def run_key(material: str, *, key: bytes | None = None) -> str:
    return "run:" + _digest("run|" + str(material), key=key)


def _session_ref(session_id: str, *, key: bytes) -> str:
    return "s:" + _digest("session|" + str(session_id), key=key)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _ensure(conn) -> None:
    conn.execute(_TABLE)


def enabled() -> bool:
    try:
        return bool(run_memory_policy().get("enabled"))
    except Exception:
        return False


def remember(events: Iterable[dict[str, Any]]) -> int:
    """Keep one content-free record per run found in ``events``. Returns the count."""
    rows = [dict(e) for e in events if isinstance(e, dict)]
    if not rows or not enabled():
        return 0
    from .agent_execution_traces import agent_execution_traces
    from .agent_observability import enrich_agent_execution_payload
    from .procedural_memory import derive_executions

    executions = derive_executions(rows)
    if not executions:
        return 0
    agent_extras: dict[str, dict[str, Any]] = {}
    if any(item.get("actor_kind") == "agent" for item in executions):
        try:
            payload = agent_execution_traces(rows, limit=1000, max_events_per_execution=1)
            payload = enrich_agent_execution_payload(payload, rows)
            for trace in payload.get("executions") or []:
                agent_extras[str(trace.get("execution_id") or "")] = {k: trace.get(k) for k in _AGENT_KEYS if trace.get(k) is not None}
        except Exception:
            agent_extras = {}

    key = _key()
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    with connect() as conn:
        _ensure(conn)
        for item in executions:
            material = item.get("_run_material")
            if not material:
                continue
            record = {k: item.get(k) for k in _RECORD_KEYS if k in item}
            if item.get("actor_kind") == "agent":
                record.update(agent_extras.get(str(item.get("execution_id") or ""), {}))
            record["evidence_refs"] = []  # the evidence these pointed to is being removed
            record["source"] = "run_memory"
            refs = sorted({_session_ref(sid, key=key) for sid in item.get("_session_ids") or []})
            rkey = run_key(material, key=key)
            existing = conn.execute("SELECT ended_at FROM run_memory WHERE run_key = ?", (rkey,)).fetchone()
            if existing is not None and str(existing[0] or "") > str(item.get("ended_at") or ""):
                continue  # an earlier snapshot already covers more of this run
            conn.execute(
                "INSERT OR REPLACE INTO run_memory(run_key, actor_kind, family_key, started_at, ended_at, session_refs, record_json, remembered_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rkey, str(item.get("actor_kind") or ""), str(item.get("family_key") or ""),
                 str(item.get("started_at") or ""), str(item.get("ended_at") or ""),
                 json.dumps(refs), json.dumps(record, ensure_ascii=False), now),
            )
            stored += 1
    return stored


def memory_runs(*, since: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    """Remembered runs as procedural executions (with ``_run_key`` for de-duplication)."""
    if not enabled():
        return []
    start = _parse(since) if since else None
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT run_key, record_json FROM run_memory ORDER BY ended_at DESC LIMIT ?",
            (max(1, min(int(limit), 100_000)),),
        ).fetchall()
    output: list[dict[str, Any]] = []
    for rkey, raw in rows:
        try:
            record = json.loads(raw)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        if start is not None:
            began = _parse(record.get("started_at"))
            if began is None or began < start:
                continue
        record["_run_key"] = rkey
        output.append(record)
    return output


def _delete_where(predicate) -> int:
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT run_key, started_at, ended_at, session_refs, record_json FROM run_memory").fetchall()
        doomed = [row[0] for row in rows if predicate(row)]
        for offset in range(0, len(doomed), 500):
            chunk = doomed[offset:offset + 500]
            conn.execute(f"DELETE FROM run_memory WHERE run_key IN ({','.join('?' for _ in chunk)})", tuple(chunk))
    return len(doomed)


def forget_sessions(session_ids: Iterable[str]) -> int:
    ids = [str(s) for s in session_ids if str(s)]
    if not ids:
        return 0
    key = _key()
    refs = {_session_ref(sid, key=key) for sid in ids}

    def hit(row) -> bool:
        try:
            return bool(refs & set(json.loads(row[3] or "[]")))
        except Exception:
            return False

    return _delete_where(hit)


def forget_range(since: str, until: str) -> int:
    start, end = _parse(since), _parse(until)
    if start is None or end is None:
        return 0

    def overlaps(row) -> bool:
        began = _parse(row[1])
        ended = _parse(row[2]) or began
        if began is None:
            return False
        return began < end and (ended or began) >= start

    return _delete_where(overlaps)


def forget_execution(execution_id: str) -> int:
    wanted = str(execution_id or "").strip().lower()

    def hit(row) -> bool:
        try:
            return str(json.loads(row[4]).get("execution_id") or "").lower() == wanted
        except Exception:
            return False

    return _delete_where(hit) if wanted else 0


def forget_all() -> int:
    return _delete_where(lambda _row: True)


def prune(*, now: datetime | None = None) -> int:
    """Apply the memory switch and its retention."""
    policy = run_memory_policy()
    if not policy.get("enabled"):
        return forget_all()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=int(policy.get("days") or 90))

    def expired(row) -> bool:
        ended = _parse(row[2]) or _parse(row[1])
        return ended is not None and ended < cutoff

    return _delete_where(expired)


def summary(*, limit: int = 200) -> dict[str, Any]:
    policy = run_memory_policy()
    with connect() as conn:
        _ensure(conn)
        total = int(conn.execute("SELECT COUNT(*) FROM run_memory").fetchone()[0])
    runs = memory_runs(limit=limit)
    public = [{k: v for k, v in run.items() if not k.startswith("_")} for run in runs]
    return {
        "policy": policy,
        "total": total,
        "returned": len(public),
        "runs": public,
        "content_free": True,
        "leaves_this_computer": False,
    }


class EvidenceWithMemory(list):
    """Raw evidence plus remembered runs, for procedural-memory readers."""

    memory_runs: list[dict[str, Any]]

    def __init__(self, events: Iterable[dict[str, Any]], memory: list[dict[str, Any]]):
        super().__init__(events)
        self.memory_runs = memory


def with_memory(events: list[dict[str, Any]], *, since: str | None = None) -> list[dict[str, Any]]:
    try:
        memory = memory_runs(since=since)
    except Exception:
        memory = []
    return EvidenceWithMemory(events, memory) if memory else events


__all__ = [
    "EvidenceWithMemory", "enabled", "forget_all", "forget_execution", "forget_range", "forget_sessions",
    "memory_runs", "prune", "remember", "run_key", "summary", "with_memory",
]
