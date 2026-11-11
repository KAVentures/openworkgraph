"""Bounded local audit trail for explicit signal review decisions.

These entries are evidence of dashboard-side review commands, not proof of a
named human's identity, external notification, source delivery, or execution.
All inserts happen inside the existing inbox mutation transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Any

from . import event_log_workspace as workspace

MAX_RETAINED = 3000
MAX_PAGE = 200
_ALLOWED = frozenset({"acknowledged", "reopened", "snoozed", "resumed", "batch_acknowledged"})


def record(conn, log_id: str, rule_id: str, fingerprint: str, action: str,
           at: datetime, *, minutes: int = 0) -> None:
    """Append only after a valid state-pinned review change.

    Retries that do not actually change the review state must not record a
    second command. The caller owns the transaction and validates the state.
    """
    if action not in _ALLOWED or type(minutes) is not int or not 0 <= minutes <= 43200:
        raise workspace.EventLogWorkspaceError("invalid signal review audit action")
    if action != "snoozed" and minutes != 0:
        raise workspace.EventLogWorkspaceError("duration is only for snooze")
    conn.execute(
        """INSERT INTO signal_review_audit
           (log_id,rule_id,event_id,recorded_at,action,state_fingerprint,snooze_minutes)
           VALUES (?,?,?,?,?,?,?)""",
        [log_id, rule_id, "srev_" + secrets.token_hex(12), at,
         action, fingerprint, minutes])
    conn.execute(
        """DELETE FROM signal_review_audit WHERE log_id = ? AND event_id NOT IN (
             SELECT event_id FROM signal_review_audit WHERE log_id = ?
             ORDER BY recorded_at DESC, event_id DESC LIMIT ?
           )""", [log_id, log_id, MAX_RETAINED])


def list_actions(log_id: str, *, limit: int = 50) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise workspace.EventLogWorkspaceError("signal review audit limit must be 1 to 200")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            total = int(conn.execute(
                "SELECT COUNT(*) FROM signal_review_audit WHERE log_id=?", [log_id]).fetchone()[0])
            rows = conn.execute(
                """SELECT a.event_id,a.rule_id,a.recorded_at,a.action,a.state_fingerprint,
                          a.snooze_minutes,r.definition
                   FROM signal_review_audit a
                   JOIN signal_rules r ON r.log_id=a.log_id AND r.rule_id=a.rule_id
                   WHERE a.log_id=? ORDER BY a.recorded_at DESC,a.event_id DESC LIMIT ?""",
                [log_id, limit]).fetchall()
        finally:
            conn.close()
    items = [{
        "event_id": event_id,
        "rule_id": rule_id,
        "name": str(json.loads(definition).get("name", "Unnamed signal"))[:120],
        "action": action,
        "recorded_at": timestamp.isoformat() + "Z",
        "state_fingerprint": fingerprint,
        "snooze_minutes": minutes,
        "identity": "local_dashboard_session_unattributed",
    } for event_id, rule_id, timestamp, action, fingerprint, minutes, definition in rows]
    return {
        "format": "openworkgraph.signal-review-audit.v1",
        "items": items,
        "retained_total": total,
        "returned": len(items),
        "truncated": total > len(items),
        "retention_limit": MAX_RETAINED,
        "identity": "local_dashboard_session_unattributed",
        "historical_backfill": False,
        "external_delivery": False,
        "action_authority": False,
    }
