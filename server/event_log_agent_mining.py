"""Agent mining: turn observed AI agent runs into an event log, analysed like any business process.

Process-mining vendors now mine the work of AI agents as well as people (for example SAP Signavio's
agent mining, or UiPath's agentic orchestration views). OpenWorkGraph already records structural
agent-run evidence (``/v1/agent-execution-traces``). This module projects those runs into the
event-log workspace, so variants, KPIs, root cause, conformance and the rest apply to agents.

* one case per observed run, identified by its opaque execution ID;
* one event per structural step: run start and finish, tool calls (by readable tool name, and
  marked when they failed), approvals, handoffs and errors; model calls only on request;
* the resource is the agent's name; the step's status, tool category and duration are event attributes;
* run outcome, agent, provider, framework, model, token totals and counts are case attributes.

No prompt, tool argument, tool result, file path or message text is copied. Only the structural
fields the agent-run report already exposes are used. Nothing here judges whether a run was good.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_agent_mining.v1"
MAX_RUNS = 5000
MAX_STEPS_PER_RUN = 500
MAX_EVIDENCE = 100_000
SETTLE_MINUTES = 30
REFRESH_OVERLAP_DAYS = 7
_FAILED = {"error", "failed", "denied", "cancelled", "timeout"}
CASE_ATTRIBUTES = ["Outcome", "Agent", "Provider", "Framework", "Model", "Tool calls", "Failed tool calls",
                   "Approvals requested", "Input tokens", "Output tokens", "Complete boundary"]


def _iso(value: Any) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity(step: dict[str, Any], include_model_calls: bool) -> str | None:
    operation = str(step.get("operation") or "unknown")
    status = str(step.get("status") or "unknown").casefold()
    failed = status in _FAILED
    if operation == "run_started":
        return "Run started"
    if operation == "run_finished":
        return "Run failed" if failed else "Run finished"
    if operation == "model_call":
        return "Model call" if include_model_calls else None
    if operation == "tool_call":
        tool = step.get("tool") if isinstance(step.get("tool"), dict) else {}
        from .agent_tool_labels import readable_tool_name
        name = str(readable_tool_name(tool.get("name")) or "unknown tool")[:120]
        return f"Tool: {name}" + (f" ({status})" if failed else "")
    if operation == "human_approval_requested":
        return "Approval requested"
    if operation == "human_approval_received":
        return "Approval " + (status if status in {"denied", "cancelled"} else "received")
    if operation == "handoff":
        return "Handoff"
    if operation == "error":
        return "Error"
    return operation.replace("_", " ").capitalize()[:120] or None


def _settled(trace: dict[str, Any], now: datetime) -> bool:
    """A run is taken once it finished, or once nothing happened in it for SETTLE_MINUTES."""
    if trace.get("run_finish_observed"):
        return True
    last = max((_iso(s.get("observed_at")) or "" for s in trace.get("events") or [] if isinstance(s, dict)), default="")
    if not last:
        return False
    return (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() >= SETTLE_MINUTES * 60


def _traces(since: str | None, until: str | None, max_runs: int, *, skip: set[str] | None = None) -> tuple[list[dict[str, Any]], int]:
    from .agent_execution_traces import _agent_groups, _one_trace
    from .procedural_memory import load_recent_evidence
    raw = load_recent_evidence(limit=MAX_EVIDENCE, since=since, until=until, agent_only=True)
    groups = [events for events in _agent_groups(raw) if events]
    now = datetime.now(timezone.utc)
    traces, running = [], 0
    for events in groups:
        trace = _one_trace(events, max_events=MAX_STEPS_PER_RUN)
        if skip and trace.get("execution_id") in skip:
            continue
        if not _settled(trace, now):
            running += 1
            continue
        traces.append(trace)
    traces.sort(key=lambda t: str(t.get("started_at") or ""), reverse=True)
    return traces[:max_runs], running


def _rows(traces: list[dict[str, Any]], include_model_calls: bool) -> tuple[list[list[str]], list[list[str]], int]:
    events: list[list[str]] = []
    cases: list[list[str]] = []
    truncated = 0
    for trace in traces:
        case = str(trace.get("execution_id") or "")
        agent = trace.get("agent") if isinstance(trace.get("agent"), dict) else {}
        agent_name = str(agent.get("name") or "Agent")[:160]
        steps = [s for s in trace.get("events") or [] if isinstance(s, dict)]
        written = 0
        model = ""
        for step in steps:
            model = model or str(step.get("model") or "")
            activity = _activity(step, include_model_calls)
            at = _iso(step.get("observed_at"))
            if not activity or not at:
                continue
            tool = step.get("tool") if isinstance(step.get("tool"), dict) else {}
            events.append([case, activity, at, agent_name, str(step.get("status") or "unknown")[:40],
                           str(tool.get("category") or "")[:80], str(step.get("duration_seconds") or 0)])
            written += 1
        if not written:
            continue
        truncated += bool(trace.get("events_truncated"))
        tool_calls = [s for s in steps if s.get("operation") == "tool_call"]
        usage = trace.get("usage_totals") if isinstance(trace.get("usage_totals"), dict) else {}
        cases.append([case, str(trace.get("outcome_status") or "unknown")[:40], agent_name,
                      str(agent.get("provider") or "")[:160], str(agent.get("framework") or "")[:160], model[:200],
                      str(len(tool_calls)),
                      str(sum(1 for s in tool_calls if str(s.get("status") or "").casefold() in _FAILED)),
                      str(trace.get("approval_request_count") or 0),
                      str(usage.get("input_tokens", "")), str(usage.get("output_tokens", "")),
                      "yes" if trace.get("complete_boundary_observed") else "no"])
    return events, cases, truncated


_MAPPING = {"case_column": "run", "activity_column": "step", "timestamp_column": "time",
            "resource_column": "agent", "attribute_columns": ["status", "tool category", "step seconds"]}


def _events_csv(events: list[list[str]]) -> BytesIO:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["run", "step", "time", "agent", "status", "tool category", "step seconds"])
    writer.writerows(events)
    return BytesIO(buffer.getvalue().encode("utf-8"))


def _store_case_attributes(conn, log_id: str, cases: list[list[str]]) -> None:
    """Store structural run metadata inside the same transaction as its events."""
    rows = []
    for case in cases:
        attrs = {key: value.replace("\x00", "").strip()[:workspace.MAX_ATTRIBUTE_VALUE]
                 for key, value in zip(CASE_ATTRIBUTES, case[1:]) if value.strip()}
        rows.append((json.dumps(attrs), log_id, case[0]))
    if rows:
        conn.executemany("""UPDATE objects SET attributes = json_merge_patch(attributes, ?)::VARCHAR
            WHERE log_id = ? AND object_id = ?""", rows)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def import_agent_runs(spec: Any) -> dict[str, Any]:
    """Create an event log from observed AI agent runs. ``spec``: name, since, until, max_runs, include_model_calls."""
    if not isinstance(spec, dict) or set(spec) - {"name", "since", "until", "max_runs", "include_model_calls"}:
        raise EventLogWorkspaceError("send name and optionally since, until, max_runs and include_model_calls")
    name = spec.get("name") or "AI agent runs"
    since, until = spec.get("since") or None, spec.get("until") or None
    for label, value in (("since", since), ("until", until)):
        if value is not None and (not isinstance(value, str) or _iso(value) is None):
            raise EventLogWorkspaceError(f"{label} must be a time with a UTC offset")
    # Compare instants, not ISO strings: "00:00:00.5Z" sorts before "00:00:00Z" as text.
    if since and until and datetime.fromisoformat(_iso(since).replace("Z", "+00:00")) > datetime.fromisoformat(_iso(until).replace("Z", "+00:00")):
        raise EventLogWorkspaceError("since must be before or equal to until")
    max_runs = spec.get("max_runs", 1000)
    if type(max_runs) is not int or not 1 <= max_runs <= MAX_RUNS:
        raise EventLogWorkspaceError(f"max_runs must be an integer from 1 to {MAX_RUNS}")
    include_model_calls = spec.get("include_model_calls", False)
    if type(include_model_calls) is not bool:
        raise EventLogWorkspaceError("include_model_calls must be true or false")

    traces, running = _traces(since, until, max_runs)
    events, cases, truncated = _rows(traces, include_model_calls)
    if not events:
        raise EventLogWorkspaceError("no finished AI agent runs with timed steps in this range")

    def remember(conn, log_id):
        _store_case_attributes(conn, log_id, cases)
        conn.execute("INSERT INTO event_log_agent_run_sources VALUES (?, ?, ?, ?)",
                     [log_id, include_model_calls, _now(), _now()])
    result = workspace.import_log(_events_csv(events), name=name, source_format="csv", mapping=_MAPPING,
                                  _transaction_hook=remember)
    log_id = result["log_id"]
    return {"format": FORMAT, "log_id": log_id, "name": name.strip(), "runs": len(cases), "events": len(events),
            "runs_still_running": running, "runs_with_steps_truncated": truncated,
            "max_steps_per_run": MAX_STEPS_PER_RUN, "model_calls_included": include_model_calls,
            "case_attributes": CASE_ATTRIBUTES,
            "note": ("Each case is one observed AI agent run, each event a structural step. No prompt, tool "
                     "argument, result, path or message text is included. Runs still in progress are left out "
                     "until they finish or stay quiet for 30 minutes; refresh the log to add them.")}


def source(log_id: str) -> dict[str, Any]:
    """Whether this log was made from agent runs, and when it was last refreshed."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            row = conn.execute("""SELECT include_model_calls, created_at, last_refreshed_at
                FROM event_log_agent_run_sources WHERE log_id = ?""", [log_id]).fetchone()
        finally:
            conn.close()
    if not row:
        return {"format": FORMAT, "log_id": log_id, "agent_runs": False}
    return {"format": FORMAT, "log_id": log_id, "agent_runs": True, "include_model_calls": row[0],
            "created_at": row[1].isoformat() + "Z", "last_refreshed_at": row[2].isoformat() + "Z" if row[2] else None}


def refresh(log_id: str) -> dict[str, Any]:
    """Append agent runs that finished since the log was made or last refreshed. Runs already in it are kept as they are."""
    with workspace._LOCK:
        current = source(log_id)
        if not current["agent_runs"]:
            raise EventLogWorkspaceError("this log was not made from AI agent runs")
        last = current["last_refreshed_at"] or current["created_at"]
        since = (datetime.fromisoformat(last.replace("Z", "+00:00")) - timedelta(days=REFRESH_OVERLAP_DAYS)).isoformat()

        def known(conn):
            return {"case_ids": [r[0] for r in conn.execute("SELECT DISTINCT case_id FROM ce").fetchall()]}
        existing = set(workspace._query(log_id, None, known)["case_ids"])
        traces, running = _traces(since, None, MAX_RUNS, skip=existing)
        events, cases, truncated = _rows(traces, current["include_model_calls"])
        appended = 0
        if events:
            def commit_refresh(conn, target):
                _store_case_attributes(conn, target, cases)
                conn.execute("UPDATE event_log_agent_run_sources SET last_refreshed_at = ? WHERE log_id = ?", [_now(), target])
            appended = workspace.append_log(log_id, _events_csv(events), source_format="csv", mapping=_MAPPING,
                                            _transaction_hook=commit_refresh)["added_events"]
        if not events:
            conn = workspace._connect()
            try:
                conn.execute("UPDATE event_log_agent_run_sources SET last_refreshed_at = ? WHERE log_id = ?", [_now(), log_id])
            finally:
                conn.close()
        return {"format": FORMAT, "log_id": log_id, "runs_added": len(cases), "events_added": appended,
                "runs_still_running": running, "runs_with_steps_truncated": truncated,
                "note": "Runs already in the log are not changed. Analyse again to include the new runs."}


def run_scheduled(log_id: str) -> tuple[str, float]:
    try:
        result = refresh(log_id)
    except EventLogWorkspaceError:
        return "failed:event_log_unavailable", 0.0
    return f"succeeded:{result['runs_added']}_runs_added", 0.0


__all__ = ["import_agent_runs", "refresh", "source", "run_scheduled", "FORMAT", "CASE_ATTRIBUTES"]
