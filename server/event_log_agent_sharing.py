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
AGENT_TAG_PREFIX = "agent: "


class NotAllowed(EventLogWorkspaceError):
    """The log is shared, but the owner did not grant this write."""


class NotShared(EventLogWorkspaceError):
    """The log is unknown or not shared at the needed level (deliberately indistinguishable)."""


def share_status(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            log = workspace._check_log(conn, log_id)
            row = conn.execute("SELECT level, granted_at FROM event_log_agent_shares WHERE log_id = ?", [log_id]).fetchone()
            tags = conn.execute("SELECT 1 FROM event_log_agent_tag_grants WHERE log_id = ?", [log_id]).fetchone() is not None
        finally:
            conn.close()
    return {"format": FORMAT, "log_id": log["log_id"], "name": log["name"],
            "level": row[0] if row else None, "granted_at": row[1].isoformat() + "Z" if row else None,
            "agents_may_tag_cases": tags and bool(row) and row[0] == "cases", "agent_tag_prefix": AGENT_TAG_PREFIX,
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
            if level != "cases":
                conn.execute("DELETE FROM event_log_agent_tag_grants WHERE log_id = ?", [log_id])
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


def set_tag_grant(log_id: str, allowed: Any) -> dict[str, Any]:
    """Let agents add case tags (always prefixed and noted as agent-made) on a log shared at the cases level."""
    if type(allowed) is not bool:
        raise EventLogWorkspaceError("allowed must be true or false")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            shared = conn.execute("SELECT level FROM event_log_agent_shares WHERE log_id = ?", [log_id]).fetchone()
            if allowed and (not shared or shared[0] != "cases"):
                raise EventLogWorkspaceError("share the log with agents at the cases level first")
            conn.execute("DELETE FROM event_log_agent_tag_grants WHERE log_id = ?", [log_id])
            if allowed:
                conn.execute("INSERT INTO event_log_agent_tag_grants VALUES (?, ?)",
                             [log_id, datetime.now(timezone.utc).replace(tzinfo=None)])
        finally:
            conn.close()
    return share_status(log_id)


def agent_tag_cases(log_id: str, spec: Any) -> dict[str, Any]:
    """An agent records a decision as a case tag. Needs the cases level and the separate tag grant."""
    require(log_id, "cases")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            granted = conn.execute("SELECT 1 FROM event_log_agent_tag_grants WHERE log_id = ?", [log_id]).fetchone()
        finally:
            conn.close()
    if not granted:
        raise NotAllowed("the owner has not allowed agents to tag cases in this log")
    if not isinstance(spec, dict) or set(spec) - {"tag", "case_ids", "note", "case_type"}:
        raise EventLogWorkspaceError("send tag, case_ids and an optional note")
    tag, note = spec.get("tag"), spec.get("note") or ""
    if not isinstance(tag, str) or not tag.strip() or not isinstance(note, str):
        raise EventLogWorkspaceError("tag must be a non-empty text and note a text")
    from . import event_log_case_tags as case_tags
    case_ids, ambiguous, not_found = _resolve_ai_case_ids(log_id, spec.get("case_type"), spec.get("case_ids"))
    body = {"tag": (AGENT_TAG_PREFIX + tag.strip())[:60], "case_ids": case_ids,
            "note": ("Added by an AI agent. " + note.strip())[:500]}
    if spec.get("case_type"):
        body["case_type"] = spec["case_type"]
    result = case_tags.add(log_id, body)
    return {"format": FORMAT, "log_id": log_id, "tag": body["tag"], "tagged": result["tagged"],
            "already_tagged": result.get("already_tagged"),
            "unknown_case_ids": (result.get("unknown_case_ids") or 0) + not_found,
            "ambiguous_case_ids": ambiguous,
            "note": "Agent tags are always prefixed and noted as agent-made; the owner can remove them under Case tags."}


MAX_PSEUDONYM_CASES = 200_000


def _resolve_ai_case_ids(log_id: str, case_type: Any, case_ids: Any) -> tuple[Any, int, int]:
    """Map the pseudonymous case IDs an agent saw back to cases, locally and without revealing them.

    At the redacted AI-context detail level agents only ever see pseudonyms (for example CASE_ID_A40E30).
    Each case ID of the log is redacted the same way and matched; a pseudonym that matches several cases
    is not used. At full detail, or outside an AI request, IDs are used as given.
    """
    if not isinstance(case_ids, list) or not case_ids or not all(isinstance(c, str) for c in case_ids):
        return case_ids, 0, 0
    try:
        from . import ai_context
        redacted = ai_context.ai_request_active() and ai_context.effective_detail()["detail_level"] == ai_context.DETAIL_REDACTED
    except Exception:
        redacted = True  # fail closed: never treat agent input as real identifiers when unsure
    if not redacted:
        return case_ids, 0, 0

    def run(conn):
        rows = conn.execute(f"SELECT DISTINCT case_id FROM ce ORDER BY 1 LIMIT {MAX_PSEUDONYM_CASES + 1}").fetchall()
        if len(rows) > MAX_PSEUDONYM_CASES:
            raise EventLogWorkspaceError("this log has too many cases to match pseudonymous case IDs; "
                                         "use a narrower case notion or full AI detail")
        return {"case_ids": [r[0] for r in rows]}

    real = workspace._query(log_id, case_type if isinstance(case_type, str) else None, run)["case_ids"]
    from . import ai_context
    tokens = ai_context.redact_contextually([{"case_id": c} for c in real])
    by_token: dict[str, list[str]] = {}
    for cid, item in zip(real, tokens):
        by_token.setdefault(str(item.get("case_id")).casefold(), []).append(cid)
    resolved, ambiguous, unknown = [], 0, 0
    for supplied in case_ids:
        hits = by_token.get(supplied.strip().casefold(), [])
        if len(hits) == 1:
            resolved.append(hits[0])
        elif len(hits) > 1:
            ambiguous += 1
        else:
            unknown += 1
    if not resolved:
        raise EventLogWorkspaceError("no cases to tag: none of these case IDs is a case of this log")
    return resolved, ambiguous, unknown


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
            taggable = {r[0] for r in conn.execute("SELECT log_id FROM event_log_agent_tag_grants").fetchall()}
        finally:
            conn.close()
    return {"format": FORMAT, "logs": [{"log_id": i, "name": n, "level": lv, "events": e, "objects": o,
                                        "case_notions": types[i], "agents_may_tag_cases": i in taggable}
                     for i, n, lv, e, o in rows],
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


MAX_SIGNAL_CASE_IDS = 50


def signals(log_id: str) -> dict[str, Any]:
    """The owner's saved signal rules and their last evaluated state. Never evaluates a rule.

    Evaluating would reset "new since last evaluation" and the owner's reviews, so agents only read
    what the owner's own evaluations (manual or scheduled) recorded. Case IDs need the cases level.
    """
    require(log_id, "aggregates")
    level = _level(log_id)
    import json as _json
    from . import event_log_signal_inbox as inbox
    states = {item["rule_id"]: item for item in inbox.list_inbox(log_id)["items"]}
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            rows = conn.execute("""SELECT rule_id, definition, last_state FROM signal_rules WHERE log_id = ?
                ORDER BY created_at, rule_id""", [log_id]).fetchall()
        finally:
            conn.close()
    items = []
    for rule_id, definition, last_state in rows:
        rule = _json.loads(definition)
        state = _json.loads(last_state) if last_state else {}
        review = states.get(rule_id, {})
        item = {"rule_id": rule_id, "name": rule.get("name"), "condition": rule.get("when"),
                "case_type": rule.get("case_type"), "filtered": bool(rule.get("cohort")),
                "status": review.get("status", "not_evaluated"), "triggered": review.get("triggered"),
                "evaluated_at": review.get("evaluated_at"), "reviewed_by_owner": review.get("status") == "acknowledged"}
        if "value" in state:
            item["value"] = state["value"]
        if "matching_cases" in state:
            item["matching_cases"] = state["matching_cases"]
            if level == "cases":
                ids = list(state.get("case_ids") or [])
                item["case_ids"] = ids[:MAX_SIGNAL_CASE_IDS]
                item["case_ids_truncated"] = len(ids) > MAX_SIGNAL_CASE_IDS
        items.append(item)
    return {"format": FORMAT, "log_id": log_id, "signals": items,
            "triggered": sum(1 for i in items if i["triggered"] and i["status"] != "stale"),
            "statuses": {"unreviewed": "triggered, not yet reviewed by the owner",
                         "acknowledged": "triggered, reviewed by the owner", "snoozed": "triggered, snoozed by the owner",
                         "cleared": "not triggered at the last evaluation",
                         "stale": "the log changed since the last evaluation; the owner must evaluate again",
                         "not_evaluated": "never evaluated"},
            "note": ("Read-only: the last result of the owner's own evaluations. Agents cannot evaluate, acknowledge "
                     "or snooze signals. A triggered signal is not a verified problem or an instruction to act.")}


def automation_candidates(log_id: str, case_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Activities ranked by volume, routine routing and waiting (aggregates level)."""
    require(log_id, "aggregates")
    from . import event_log_automation_candidates as automation
    result = automation.candidates(log_id, case_type, limit=max(1, min(int(limit), 50)))
    keep = ("case_notion", "cases", "weeks_observed", "weights", "min_cases", "activities_below_min_cases",
            "candidates", "routing_patterns", "caveat")
    return {"format": FORMAT, "log_id": log_id, **{k: result[k] for k in keep if k in result}}


def slowest_cases(log_id: str, case_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    require(log_id, "cases")
    from . import event_log_case_explain as case_explain
    spec: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
    if case_type:
        spec["case_type"] = case_type
    return {"format": FORMAT, "log_id": log_id, **case_explain.slowest(log_id, spec)}
