"""Local cross-dataset timeline for evaluated signal state changes (roadmap #370 R10).

Historical transition rows are written only by explicit or opted-in scheduled
signal evaluation. This view never re-evaluates a rule, contacts a connector,
delivers an alert, changes a review, or authorizes an agent to act.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.signal-transition-timeline.v1"
MAX_RETURNED = 200
FILTERS = frozenset(("all", "triggered", "cleared"))


def _name(definition: str) -> str:
    """Do not trust persisted rule JSON, including its name."""
    try:
        value = json.loads(definition).get("name")
        if isinstance(value, str) and value.strip():
            return value[:120]
    except (ValueError, TypeError, AttributeError):
        pass
    return "Signal name unavailable"


def _utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"


def timeline(*, transition: str = "all", limit: int = 100) -> dict[str, Any]:
    """Read up to 200 recent signal transitions across retained local logs.

    Counts are computed over the entire retained journal BEFORE limiting rows.
    A source log/rule removed from the workspace is never exposed as an orphan.
    This is a historical journal, not the current signal-inbox status.
    """
    if transition not in FILTERS:
        raise workspace.EventLogWorkspaceError("unknown signal transition filter")
    if type(limit) is not int or not 1 <= limit <= MAX_RETURNED:
        raise workspace.EventLogWorkspaceError("signal timeline limit must be 1 to 200")

    base = """FROM signal_transitions t
        JOIN logs l ON l.log_id=t.log_id
        JOIN signal_rules s ON s.log_id=t.log_id AND s.rule_id=t.rule_id"""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            totals = conn.execute(
                "SELECT t.event_type,count(*) " + base + " GROUP BY t.event_type"
            ).fetchall()
            counts = {"triggered": 0, "cleared": 0}
            for kind, count in totals:
                if kind not in counts:
                    raise workspace.EventLogWorkspaceError("unsupported historical signal transition")
                counts[kind] += int(count)
            matching = counts["triggered"] + counts["cleared"] if transition == "all" else counts[transition]
            where = "" if transition == "all" else " WHERE t.event_type=?"
            params: list[Any] = [] if transition == "all" else [transition]
            rows = conn.execute(
                """SELECT t.transition_id,t.log_id,l.name,t.rule_id,s.definition,
                          t.event_type,t.occurred_at
                   """ + base + where +
                " ORDER BY t.occurred_at DESC,t.transition_id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        finally:
            conn.close()

    items = [
        {"event_id": event_id, "log_id": log_id, "log_name": log_name,
         "rule_id": rule_id, "name": _name(definition),
         "transition": kind, "evaluated_at": _utc(at)}
        for event_id, log_id, log_name, rule_id, definition, kind, at in rows
    ]
    return {
        "format": FORMAT,
        "filter": transition,
        "counts": counts,
        "retained_total": counts["triggered"] + counts["cleared"],
        "matched_transitions": matching,
        "returned_transitions": len(items),
        "truncated": len(items) < matching,
        "limit": limit,
        "items": items,
        "historical_backfill": False,
        "current_state_check": False,
        "local_only": True,
        "source_data_read": False,
        "evaluations_performed": False,
        "notifications_sent": False,
        "actions_authorized": False,
        "coverage": "Retained evaluations of saved local rules and logs, not a live provider event stream.",
    }


__all__ = ["FORMAT", "MAX_RETURNED", "timeline"]
