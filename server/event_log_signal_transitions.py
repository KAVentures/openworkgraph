"""Local, bounded lifecycle history for evaluated event-log signals (R10).

This journal records transitions of the *evaluated boolean*, not business
actions, deliveries, real-time events, or authorizations. Never store matching
case IDs, source contents, metric values, or recipients in the journal.
"""
from __future__ import annotations

from datetime import datetime
import json
import secrets
from typing import Any

from . import event_log_signal_inbox as inbox
from . import event_log_workspace as workspace

MAX_RETAINED_PER_LOG = 5000
MAX_RETURNED = 200


def append(conn, log_id: str, rule_id: str, previous: dict | None,
           current: dict, evaluated_at: datetime) -> bool:
    """Record only a transition to triggered or cleared; retries dedupe.

    A first-ever false evaluation is a baseline, not a clearance. Subsequent
    changes in case membership, log revision or metric without changing the
    boolean do not create spurious repeated alerts.
    """
    triggered = current.get("triggered")
    if type(triggered) is not bool:
        raise workspace.EventLogWorkspaceError("signal has no reliable triggered state")
    prior = previous.get("triggered") if isinstance(previous, dict) else None
    if prior is not None and type(prior) is not bool:
        raise workspace.EventLogWorkspaceError("previous signal state is invalid")
    if triggered is prior or (prior is None and not triggered):
        return False
    transition = "triggered" if triggered else "cleared"
    # The saved inbox uses this exact digest for its state-pinned review.
    fingerprint = inbox._hash(log_id, rule_id, json.dumps(current))
    conn.execute(
        """INSERT INTO signal_transitions
           (log_id,rule_id,transition_id,occurred_at,event_type,state_fingerprint)
           VALUES (?,?,?,?,?,?)""",
        [log_id, rule_id, "sevt_" + secrets.token_hex(12),
         evaluated_at, transition, fingerprint])
    return True


def trim(conn, log_id: str) -> None:
    """Bound history size without removing other datasets' records."""
    conn.execute(
        """DELETE FROM signal_transitions WHERE log_id=?
           AND transition_id NOT IN (
             SELECT transition_id FROM signal_transitions WHERE log_id=?
             ORDER BY occurred_at DESC, transition_id DESC LIMIT ?
           )""",
        [log_id, log_id, MAX_RETAINED_PER_LOG])


def list_history(log_id: str, *, limit: int = 50) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_RETURNED:
        raise workspace.EventLogWorkspaceError("signal history limit must be 1 to 200")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            count = int(conn.execute(
                "SELECT count(*) FROM signal_transitions WHERE log_id=?", [log_id]).fetchone()[0])
            rows = conn.execute(
                """SELECT t.rule_id,t.transition_id,t.occurred_at,t.event_type,
                          t.state_fingerprint,s.definition
                   FROM signal_transitions t
                   JOIN signal_rules s ON s.log_id=t.log_id AND s.rule_id=t.rule_id
                   WHERE t.log_id=?
                   ORDER BY t.occurred_at DESC,t.transition_id DESC LIMIT ?""",
                [log_id, limit]).fetchall()
        finally:
            conn.close()
    items = []
    for rid, event_id, at, event, fingerprint, definition in rows:
        name = json.loads(definition).get("name", "Unnamed signal")
        items.append({
            "event_id": event_id,
            "rule_id": rid,
            "name": str(name)[:120],
            "transition": event,
            "evaluated_at": at.isoformat() + "Z",
            "state_fingerprint": fingerprint,
        })
    return {
        "format": "openworkgraph.signal-transitions.v1",
        "items": items, "retained_total": count, "returned": len(items),
        "truncated": count > len(items), "retention_limit": MAX_RETAINED_PER_LOG,
        "historical_backfill": False,
        "source": "locally evaluated saved log rules; not a live provider event stream",
        "delivery_enabled": False, "actions_authorized": False,
    }
