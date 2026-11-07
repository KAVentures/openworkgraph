"""One local read-only operations overview for saved signals across *all* event logs.

This reuses R10 signal-inbox state/fingerprint/basis semantics under the existing
DuckDB workspace lock. It never evaluates a rule, reads a source connector,
sends notifications, grants permissions, or returns case-level evidence.
"""
from __future__ import annotations

import json
from typing import Any

from . import event_log_signal_inbox as inbox
from . import event_log_workspace as workspace

MAX_RULES_TOTAL = 2500
MAX_RETURNED = 200
_STATUS_ORDER = {
    "unavailable": 0,
    "unreviewed": 1,
    "stale": 2,
    "not_evaluated": 3,
    "snoozed": 4,
    "acknowledged": 5,
    "cleared": 6,
}
_STATUS_FILTERS = frozenset(_STATUS_ORDER) | {"all", "attention"}


def _schedules() -> dict[str, dict[str, Any]]:
    # No provider source read; just the owner's existing local job metadata.
    try:
        from . import local_scheduler
        return {str(item["target_id"]): item for item in local_scheduler.status()["schedules"]
                if item.get("kind") == "event_log_signals" and item.get("enabled") is True}
    except (OSError, ValueError, KeyError):
        # A malformed local schedule must not make saved signal states disappear.
        return {}


def overview(*, status: str = "attention", limit: int = 100) -> dict[str, Any]:
    """Snapshot bounded cross-log review status, without changing saved rules.

    Full status counters are calculated BEFORE limiting displayed rows; no
    unreviewed or stale signal silently disappears behind a pagination count.
    """
    if status not in _STATUS_FILTERS:
        raise workspace.EventLogWorkspaceError("unknown signal overview filter")
    if type(limit) is not int or not 1 <= limit <= MAX_RETURNED:
        raise workspace.EventLogWorkspaceError("signal overview limit must be 1 to 200")
    schedules = _schedules()
    now = inbox._now()
    counts = {name: 0 for name in _STATUS_ORDER}
    pending: list[dict[str, Any]] = []
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            log_rows = conn.execute(
                "SELECT log_id,name FROM logs ORDER BY imported_at DESC,log_id"
            ).fetchall()
            if len(log_rows) > workspace.MAX_LOGS:
                raise workspace.EventLogWorkspaceError("local dataset limit exceeded")
            total = conn.execute("SELECT count(*) FROM signal_rules").fetchone()[0]
            if total > MAX_RULES_TOTAL:
                raise workspace.EventLogWorkspaceError("local signal rule limit exceeded")
            rows = conn.execute("""
                SELECT s.log_id,s.rule_id,s.definition,s.last_evaluated_at,s.last_state,
                       r.state_fingerprint,r.acknowledged_at,z.state_fingerprint,z.until_at
                FROM signal_rules s
                LEFT JOIN signal_reviews r ON r.log_id=s.log_id AND r.rule_id=s.rule_id
                LEFT JOIN signal_snoozes z ON z.log_id=s.log_id AND z.rule_id=s.rule_id
                ORDER BY s.log_id,s.created_at,s.rule_id
            """).fetchall()
            names = dict(log_rows)
            basis_by_log: dict[str, list[int]] = {}
            for log_id, rule_id, definition, at, state, review_fp, ack_at, snooze_fp, snooze_until in rows:
                if log_id not in names:
                    # A source-deleted orphan record cannot imply live review.
                    raise workspace.EventLogWorkspaceError("signal rule has no dataset")
                if log_id not in basis_by_log:
                    basis_by_log[log_id] = inbox.log_basis(conn, log_id)
                try:
                    parsed = json.loads(definition)
                    name = parsed["name"]
                    if not isinstance(name, str):
                        raise ValueError("bad name")
                    item = inbox._state(log_id, rule_id, name, at, state,
                                        (review_fp, ack_at) if review_fp else None,
                                        basis=basis_by_log[log_id])
                    snoozed = (snooze_fp and item["requires_review"]
                               and item["state_fingerprint"] == snooze_fp
                               and snooze_until is not None and snooze_until > now)
                    if snoozed:
                        item.update(status="snoozed", requires_review=False,
                                    snoozed_until=snooze_until.isoformat() + "Z")
                except (KeyError, ValueError, TypeError, AttributeError):
                    # Unknown/corrupted evaluations must be called out, NEVER
                    # treated as cleared, acknowledged or safe to act upon.
                    item = {
                        "rule_id": rule_id, "name": "Signal needs reevaluation",
                        "status": "unavailable", "triggered": None,
                        "evaluated_at": None, "acknowledged_at": None,
                        "snoozed_until": None, "requires_review": True,
                    }
                current = item["status"]
                if current not in counts:
                    raise workspace.EventLogWorkspaceError("unsupported signal state")
                counts[current] += 1
                job = schedules.get(log_id)
                pending.append({
                    "log_id": log_id, "log_name": names[log_id],
                    "rule_id": rule_id, "name": item["name"],
                    "status": current, "triggered": item.get("triggered"),
                    "evaluated_at": item.get("evaluated_at"),
                    "acknowledged_at": item.get("acknowledged_at"),
                    "snoozed_until": item.get("snoozed_until"),
                    "requires_review": item["requires_review"],
                    "scheduled_check": job is not None,
                    "scheduled_next_at": job.get("next_run_at") if job else None,
                })
        finally:
            conn.close()
    pending.sort(key=lambda r: (_STATUS_ORDER[r["status"]], str(r["log_name"]).casefold(),
                                r["log_id"], r["rule_id"]))
    if status == "attention":
        filtered = [r for r in pending if r["status"] in {"unavailable", "unreviewed", "stale", "not_evaluated"}]
    elif status == "all":
        filtered = pending
    else:
        filtered = [r for r in pending if r["status"] == status]
    shown = filtered[:limit]
    return {
        "format": "openworkgraph.signal-review-overview.v1",
        "filter": status,
        "datasets_scanned": len(log_rows),
        "datasets_with_rules": len({r["log_id"] for r in pending}),
        "total_rules": len(pending),
        "counts": counts,
        "attention": sum(counts[s] for s in ("unavailable", "unreviewed", "stale", "not_evaluated")),
        "matched_rules": len(filtered), "returned_rules": len(shown),
        "truncated": len(filtered) > len(shown), "limit": limit,
        "items": shown,
        "local_only": True, "source_data_read": False,
        "evaluations_performed": False,
        "notifications_sent": False, "actions_authorized": False,
        "coverage": "Saved signal definitions and latest locally evaluated states only; no live source refresh.",
    }


__all__ = ["MAX_RETURNED", "MAX_RULES_TOTAL", "overview"]
