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
import tempfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_agent_mining.v1"
MAX_RUNS = 5000
MAX_STEPS_PER_RUN = 500
MAX_EVIDENCE = 100_000
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


def _traces(since: str | None, until: str | None, max_runs: int) -> list[dict[str, Any]]:
    from .agent_execution_traces import _agent_groups, _one_trace
    from .procedural_memory import load_recent_evidence
    raw = load_recent_evidence(limit=MAX_EVIDENCE, since=since, until=until, agent_only=True)
    groups = [events for events in _agent_groups(raw) if events]
    traces = [_one_trace(events, max_events=MAX_STEPS_PER_RUN) for events in groups]
    traces.sort(key=lambda t: str(t.get("started_at") or ""), reverse=True)
    return traces[:max_runs]


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


def import_agent_runs(spec: Any) -> dict[str, Any]:
    """Create an event log from observed AI agent runs. ``spec``: name, since, until, max_runs, include_model_calls."""
    if not isinstance(spec, dict) or set(spec) - {"name", "since", "until", "max_runs", "include_model_calls"}:
        raise EventLogWorkspaceError("send name and optionally since, until, max_runs and include_model_calls")
    name = spec.get("name") or "AI agent runs"
    since, until = spec.get("since") or None, spec.get("until") or None
    for label, value in (("since", since), ("until", until)):
        if value is not None and (not isinstance(value, str) or _iso(value) is None):
            raise EventLogWorkspaceError(f"{label} must be a time with a UTC offset")
    max_runs = spec.get("max_runs", 1000)
    if type(max_runs) is not int or not 1 <= max_runs <= MAX_RUNS:
        raise EventLogWorkspaceError(f"max_runs must be an integer from 1 to {MAX_RUNS}")
    include_model_calls = spec.get("include_model_calls", False)
    if type(include_model_calls) is not bool:
        raise EventLogWorkspaceError("include_model_calls must be true or false")

    traces = _traces(since, until, max_runs)
    events, cases, truncated = _rows(traces, include_model_calls)
    if not events:
        raise EventLogWorkspaceError("no observed AI agent runs with timed steps in this range")
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["run", "step", "time", "agent", "status", "tool category", "step seconds"])
    writer.writerows(events)
    mapping = {"case_column": "run", "activity_column": "step", "timestamp_column": "time",
               "resource_column": "agent", "attribute_columns": ["status", "tool category", "step seconds"]}
    result = workspace.import_log(BytesIO(buffer.getvalue().encode("utf-8")), name=name,
                                  source_format="csv", mapping=mapping)
    log_id = result["log_id"]
    from . import event_log_case_attributes as case_attributes
    with tempfile.TemporaryDirectory(prefix="owg-agentmine-", dir=str(workspace.DATA_DIR)) as tmp:
        path = Path(tmp) / "runs.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            table = csv.writer(handle)
            table.writerow(["run", *CASE_ATTRIBUTES])
            table.writerows(cases)
        try:
            case_attributes.add(log_id, path, id_column="run", columns=CASE_ATTRIBUTES)
        except Exception:
            workspace.delete_log(log_id)
            raise
    return {"format": FORMAT, "log_id": log_id, "name": name.strip(), "runs": len(cases), "events": len(events),
            "runs_with_steps_truncated": truncated, "max_steps_per_run": MAX_STEPS_PER_RUN,
            "model_calls_included": include_model_calls, "case_attributes": CASE_ATTRIBUTES,
            "note": ("Each case is one observed AI agent run, each event a structural step. No prompt, tool "
                     "argument, result, path or message text is included. Runs only partly observed have "
                     "Complete boundary = no.")}


__all__ = ["import_agent_runs", "FORMAT", "CASE_ATTRIBUTES"]
