"""Which agent tools read a shared log, and when: a short local record for the owner.

Sharing a log with AI agents is the owner's choice; seeing what the agents then asked for keeps that
choice informed. Every call an agent (the local AI bearer, not the owner's dashboard) makes that reaches one of a log's
process tools is recorded with its time, the tool's operation name and the outcome: ``ok``,
``not_shared`` (the log is not shared at the level the tool needs), ``not_allowed`` (for example
tagging without permission), ``invalid`` (input the tool rejected) or ``error`` (the tool failed).
Requests the route rejects before any tool runs, such as malformed JSON or out-of-range query
parameters, are not recorded: no tool read anything.

Only these four facts are kept: no parameters, no case IDs and no answers. The record is local, keeps the
latest ``MAX_ROWS_PER_LOG`` calls per log, and is deleted with the log.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_agent_access.v1"
MAX_ROWS_PER_LOG = 1000
OUTCOMES = ("ok", "not_shared", "not_allowed", "invalid", "error")
# The agent_access_log table is part of the workspace schema and is erased with its log by workspace.delete_log.


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record(log_id: Any, operation: str, outcome: str) -> None:
    """Note one agent call. Never raises: recording must not change the agent's answer."""
    if not isinstance(log_id, str) or not log_id.startswith("log_") or len(log_id) > 64 or outcome not in OUTCOMES:
        return
    try:
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                if not conn.execute("SELECT 1 FROM logs WHERE log_id = ?", [log_id]).fetchone():
                    return
                conn.execute("INSERT INTO agent_access_log VALUES (?, ?, ?, ?)", [log_id, _now(), operation[:80], outcome])
                count = conn.execute("SELECT count(*) FROM agent_access_log WHERE log_id = ?", [log_id]).fetchone()[0]
                if count > MAX_ROWS_PER_LOG:
                    conn.execute("""DELETE FROM agent_access_log WHERE log_id = ? AND called_at < (
                        SELECT min(called_at) FROM (SELECT called_at FROM agent_access_log WHERE log_id = ?
                                             ORDER BY called_at DESC LIMIT ?))""", [log_id, log_id, MAX_ROWS_PER_LOG])
            finally:
                conn.close()
    except Exception:  # noqa: BLE001 - an audit write failure must not fail the agent's request
        return


def erase(conn, log_id: str) -> None:
    conn.execute("DELETE FROM agent_access_log WHERE log_id = ?", [log_id])


def access(log_id: str, limit: int = 50) -> dict[str, Any]:
    """The latest agent calls for the owner, newest first, with counts per tool over the last 30 days."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise EventLogWorkspaceError("limit must be a whole number from 1 to 500")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("""SELECT called_at, operation, outcome FROM agent_access_log WHERE log_id = ?
                ORDER BY called_at DESC LIMIT ?""", [log_id, limit]).fetchall()
            total = conn.execute("SELECT count(*) FROM agent_access_log WHERE log_id = ?", [log_id]).fetchone()[0]
            recent = conn.execute("""SELECT operation, outcome FROM agent_access_log WHERE log_id = ? AND called_at >= ?""",
                                  [log_id, _now() - timedelta(days=30)]).fetchall()
        finally:
            conn.close()
    by_tool = Counter(op for op, _ in recent)
    refused = Counter(op for op, outcome in recent if outcome != "ok")
    return {
        "format": FORMAT, "log_id": log_id, "recorded": total, "kept_at_most": MAX_ROWS_PER_LOG,
        "calls": [{"at": at.isoformat() + "Z", "operation": op, "outcome": outcome} for at, op, outcome in rows],
        "last_30_days": [{"operation": op, "calls": n, "refused": refused.get(op, 0)}
                         for op, n in sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))],
        "note": ("Calls by connected AI agents to this log's process tools. Only the time, the tool and whether it "
                 "was answered are kept; no parameters or answers."),
    }


__all__ = ["record", "access", "erase", "FORMAT"]
