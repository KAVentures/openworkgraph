"""Owner-chosen sharing of a saved event log's process intelligence with connected AI agents.

Imported business logs are closed to AI tokens and MCP clients by default. The
owner can share one log at a time, from the dashboard only, at one of two
levels:

* ``aggregates``: overview (cases, durations, top variants), KPIs, automatic
  insights and root-cause associations. Responses describe groups of cases.
* ``cases``: everything in ``aggregates`` plus explaining one named case
  (its steps, gaps against the typical gap, repeats) and the slowest cases.
  Case IDs and activity names are then visible to the agent.

Every agent request also needs the global AI-access switch to be on, and AI
responses pass through the existing AI-context redaction at the user's chosen
detail level. Sharing grants reading only: an agent cannot import, change,
delete or tag anything through it. Revoking (or deleting the log) takes effect
on the next request. A log that is not shared answers exactly like an unknown
log, so agents cannot enumerate unshared logs.

The MCP tool group appears once at least one log is shared (connected apps see
it after their next restart); it stays listed but empty if all shares are
later revoked until that restart.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from . import event_log_workspace as workspace
from .process_intelligence_settings import tools_enabled, write_flag as _write_flag
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-agent-sharing.v1"
LEVELS = ("aggregates", "cases")


class NotShared(EventLogWorkspaceError):
    """The log is unknown or not shared at the needed level (deliberately indistinguishable)."""


def share_status(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            log = workspace._check_log(conn, log_id)
            row = conn.execute("SELECT level, granted_at FROM event_log_agent_shares WHERE log_id = ?", [log_id]).fetchone()
        finally:
            conn.close()
    return {"format": FORMAT, "log_id": log["log_id"], "name": log["name"],
            "level": row[0] if row else None, "granted_at": row[1].isoformat() + "Z" if row else None,
            "levels": list(LEVELS), "tools_listed": tools_enabled(),
            "note": ("Agents also need AI access switched on; responses are redacted at your AI-context detail level. "
                     "Sharing never lets an agent change the log.")}


def set_share(log_id: str, level: Any) -> dict[str, Any]:
    if level is not None and level not in LEVELS:
        raise EventLogWorkspaceError("level must be aggregates, cases or null (stop sharing)")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute("DELETE FROM event_log_agent_shares WHERE log_id = ?", [log_id])
            if level is not None:
                conn.execute("INSERT INTO event_log_agent_shares VALUES (?, ?, ?)",
                             [log_id, level, datetime.now(timezone.utc).replace(tzinfo=None)])
            any_shared = conn.execute("SELECT count(*) FROM event_log_agent_shares").fetchone()[0] > 0
        finally:
            conn.close()
    if any_shared and not tools_enabled():
        _write_flag(True)
    elif not any_shared and tools_enabled() and os.getenv("OWG_PROCESS_INTELLIGENCE_TOOLS") is None:
        _write_flag(False)
    return share_status(log_id)


def _level(log_id: Any) -> str | None:
    if not isinstance(log_id, str) or not workspace._LOG_ID.fullmatch(log_id):
        return None
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            row = conn.execute("SELECT level FROM event_log_agent_shares s JOIN logs l USING (log_id) WHERE s.log_id = ?",
                               [log_id]).fetchone()
        finally:
            conn.close()
    return row[0] if row else None


def require(log_id: Any, level: str) -> None:
    shared = _level(log_id)
    if shared is None or (level == "cases" and shared != "cases"):
        raise NotShared("no process log shared with agents at that level has this id")


def shared_logs() -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            rows = conn.execute("""SELECT s.log_id, l.name, s.level, l.events, l.objects FROM event_log_agent_shares s
                JOIN logs l USING (log_id) ORDER BY l.name""").fetchall()
            types = {log_id: [t for (t,) in conn.execute(
                "SELECT DISTINCT object_type FROM objects WHERE log_id = ? ORDER BY 1 LIMIT 20", [log_id]).fetchall()]
                for log_id, *_ in rows}
        finally:
            conn.close()
    return {"format": FORMAT, "logs": [{"log_id": i, "name": n, "level": lv, "events": e, "objects": o,
                                        "case_notions": types[i]} for i, n, lv, e, o in rows],
            "note": "Only logs the owner shared with agents are listed."}


def _top(items: list[Any], n: int) -> list[Any]:
    return list(items or [])[:n]


def overview(log_id: str, case_type: str | None = None, sla_hours: float | None = None) -> dict[str, Any]:
    require(log_id, "aggregates")
    from . import event_log_analytics as analytics
    from . import event_log_insights as insights
    sla = int(sla_hours * 3600) if sla_hours else None
    summary = workspace.summary(log_id, case_type)
    variants = workspace.variants(log_id, case_type, 5)
    kpis = analytics.kpis(log_id, case_type, sla)
    found = insights.insights(log_id, {"case_type": case_type, "limit": 5, **({"sla_seconds": sla} if sla else {})})
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": summary["case_notion"],
        "cases": summary["cases"], "events": summary["events"], "activities": summary["activities"],
        "first_event_at": summary["first_event_at"], "last_event_at": summary["last_event_at"],
        "case_duration_seconds": summary["case_duration_seconds"],
        "kpis": {k: kpis.get(k) for k in ("throughput_seconds", "rework_rate", "sla") if k in kpis},
        "top_variants": [{"path": v["path"], "cases": v["cases"], "share": v.get("share")}
                         for v in _top(variants.get("variants"), 5)],
        "variants_total": variants.get("distinct_variants"),
        "insights": [{k: f.get(k) for k in ("kind", "title", "cases", "case_share", "impact_seconds", "cohort", "caveat")}
                     for f in found["findings"]],
        "interpretation": ("Describes recorded events in this log; durations are first-to-last recorded event. "
                           "Insights are associations, not causes, and nothing here authorizes an action."),
    }


def root_cause(log_id: str, outcome: str, case_type: str | None = None, hours: float | None = None,
               activity: str | None = None) -> dict[str, Any]:
    require(log_id, "aggregates")
    from . import event_log_analytics as analytics
    result = analytics.root_cause(log_id, outcome, case_type,
                                  threshold_seconds=int(hours * 3600) if hours else None, activity=activity, limit=15)
    return {"format": FORMAT, "log_id": log_id, **{k: result.get(k) for k in (
        "cases", "outcome_rate", "outcome", "features_tested", "associations", "method",
        "associations_are_not_causes", "temporal_order_not_checked", "numeric_attributes", "note") if k in result}}


def explain_case(log_id: str, case_id: str, case_type: str | None = None) -> dict[str, Any]:
    require(log_id, "cases")
    from . import event_log_case_explain as case_explain
    spec: dict[str, Any] = {"case_id": case_id}
    if case_type:
        spec["case_type"] = case_type
    return {"format": FORMAT, "log_id": log_id, **case_explain.explain(log_id, spec)}


def slowest_cases(log_id: str, case_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    require(log_id, "cases")
    from . import event_log_case_explain as case_explain
    spec: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
    if case_type:
        spec["case_type"] = case_type
    return {"format": FORMAT, "log_id": log_id, **case_explain.slowest(log_id, spec)}
