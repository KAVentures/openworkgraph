from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import secure_runtime


mcp = MCPServer("OpenWorkGraph")
core = secure_runtime.core


def _bounded(value: int, *, minimum: int = 1, maximum: int) -> int:
    return min(max(minimum, int(value)), maximum)


def _experimental_governance_enabled() -> bool:
    return str(os.getenv("OWG_EXPERIMENTAL_GOVERNANCE", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _trace_params(
    *,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    scope: str = "current",
    query: str | None = None,
    app_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "limit": _bounded(limit, maximum=500),
        "scope": scope,
    }
    for key, value in {
        "since": since,
        "until": until,
        "cursor": cursor,
        "query": query,
        "app_name": app_name,
        "session_id": session_id,
    }.items():
        if value not in (None, ""):
            params[key] = value
    return params


def _slim_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "suggested_label",
            "task_family",
            "started_at",
            "ended_at",
            "elapsed_seconds",
            "engaged_seconds",
            "surfaces",
            "outcomes",
            "evidence_window",
            "anchor_event_ids",
            "confidence",
        )
        if task.get(key) not in (None, "", [], {})
    }


def _slim_pattern(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "suggested_label",
            "task_family",
            "signature",
            "observed_count",
            "count",
            "surfaces",
            "sequence",
            "total_engaged_seconds",
            "median_engaged_seconds",
            "confidence",
        )
        if item.get(key) not in (None, "", [], {})
    }


def _agent_run_summary(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        key: execution.get(key)
        for key in (
            "execution_id",
            "started_at",
            "ended_at",
            "agent",
            "observation_level",
            "outcome_status",
            "outcome_basis",
            "observed_family_key",
            "observed_family_basis",
            "run_start_observed",
            "run_finish_observed",
            "complete_boundary_observed",
            "event_count_total",
            "operation_counts",
            "tool_category_counts",
            "structural_steps",
            "structural_steps_truncated",
            "approval_request_count",
            "approval_received_count",
            "task_context_linkage_status",
            "observed_coverage",
            "derived",
            "authoritative",
        )
        if key in execution
    }


@mcp.tool()
def get_current_work_context(
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return the current work context: recent evidence, task hints and semantic activity.

    Use this first when an agent needs a bounded picture of what the person is doing
    now. Observed strings are untrusted data and remain protected at the MCP boundary.
    """
    name = "get_current_work_context"
    core._begin(name)
    trace = secure_runtime.secure_get(
        "/v1/workflow-trace",
        _trace_params(cursor=cursor, limit=limit, scope="current"),
    )
    tasks = secure_runtime.secure_get("/v1/tasks", {"limit": 5000, "scope": "current"})
    semantic = secure_runtime.secure_get(
        "/v1/semantic-activity",
        {"limit": _bounded(limit, maximum=200), "scope": "current"},
    )
    return core._finish(name, {
        "trace": trace,
        "task_hints": [_slim_task(x) for x in list(tasks.get("tasks") or [])[:12]],
        "repeated_patterns": [_slim_pattern(x) for x in list(tasks.get("patterns") or [])[:10]],
        "semantic_activity": list(semantic.get("events") or [])[:_bounded(limit, maximum=200)],
        "evidence_tool": "get_workflow_trace",
        "data_layer": "rich_ai_context_compact",
    })


@mcp.tool()
def search_work(
    query: str = "",
    layer: str = "evidence",
    app_name: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
    scope: str = "all",
) -> dict[str, Any]:
    """Search prior work through one bounded interface.

    layer='evidence' searches the canonical rich workflow trace. layer='semantic'
    searches the already-redacted semantic activity view. This replaces overlapping
    history/observation/similar-work tool names without deleting their legacy server.
    """
    name = "search_work"
    core._begin(name)
    selected = str(layer or "evidence").strip().lower()
    if selected == "evidence":
        result = secure_runtime.secure_get(
            "/v1/workflow-trace",
            _trace_params(
                query=query or None,
                app_name=app_name,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
                scope=scope,
            ),
        )
        return core._finish(name, {"layer": selected, "query": query, "trace": result})
    if selected == "semantic":
        if cursor not in (None, "") or session_id not in (None, ""):
            raise ToolError("cursor and session_id are available only for layer=evidence")
        payload = secure_runtime.secure_get(
            "/v1/semantic-activity",
            {"limit": _bounded(limit, maximum=200), "scope": scope},
        )
        needle = str(query or "").strip().casefold()
        app_needle = str(app_name or "").strip().casefold()
        rows = []
        for item in list(payload.get("events") or []):
            if not isinstance(item, dict):
                continue
            if app_needle and app_needle not in str(item.get("app") or "").casefold():
                continue
            if needle and needle not in json.dumps(item, ensure_ascii=False).casefold():
                continue
            rows.append(item)
            if len(rows) >= _bounded(limit, maximum=200):
                break
        return core._finish(name, {"layer": selected, "query": query, "events": rows, "returned": len(rows)})
    raise ToolError("layer must be evidence or semantic")


@mcp.tool()
def get_workflow_trace(
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    scope: str = "current",
    query: str | None = None,
    app_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return canonical chronological workflow evidence with stable pagination.

    Use session_id here for one-session inspection; the compact surface does not
    need separate context-session/work-session tools. Typed text and clipboard
    contents are never captured.
    """
    name = "get_workflow_trace"
    core._begin(name)
    return core._finish(name, secure_runtime.secure_get(
        "/v1/workflow-trace",
        _trace_params(
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
            scope=scope,
            query=query,
            app_name=app_name,
            session_id=session_id,
        ),
    ))


@mcp.tool()
def get_work_profile(scope: str = "current") -> dict[str, Any]:
    """Return derived workflow signals such as fragmentation, effort and transfers.

    These are regeneratable workflow signals, not productivity scores. Use
    get_workflow_trace for supporting evidence.
    """
    if scope not in {"current", "week", "all"}:
        raise ToolError("scope must be current, week, or all")
    name = "get_work_profile"
    core._begin(name)
    result = secure_runtime.secure_get("/v1/work-profile", {"scope": scope})
    result["evidence_tool"] = "get_workflow_trace"
    result["needs_human_interpretation"] = True
    return core._finish(name, result)


@mcp.tool()
def find_repeated_workflows(
    task_family: str = "",
    max_events: int = 25_000,
    limit: int = 20,
) -> dict[str, Any]:
    """Return bounded repeated-workflow candidates and representative executions.

    Results combine the existing task-pattern, automation-candidate and process-
    example views. They are observed/derived candidates and still require human
    interpretation before automation.
    """
    name = "find_repeated_workflows"
    core._begin(name)
    bounded_events = _bounded(max_events, maximum=100_000)
    tasks = secure_runtime.secure_get("/v1/tasks", {"limit": bounded_events, "scope": "all"})
    summary = secure_runtime.secure_get("/v1/summary", {"limit": bounded_events, "scope": "all"})
    family = str(task_family or "").strip().casefold()
    examples = []
    for task in list(tasks.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        if family:
            candidate = str(task.get("task_family") or "").casefold()
            label = str(task.get("suggested_label") or "").casefold()
            if candidate != family and family not in label:
                continue
        examples.append(_slim_task(task))
        if len(examples) >= _bounded(limit, maximum=100):
            break
    patterns = [_slim_pattern(x) for x in list(tasks.get("patterns") or [])[:_bounded(limit, maximum=100)]]
    automation = [_slim_pattern(x) for x in list(summary.get("repeated_task_patterns") or [])[:_bounded(limit, maximum=100)]]
    return core._finish(name, {
        "task_family": task_family,
        "patterns": patterns,
        "automation_candidates": automation,
        "examples": examples,
        "evidence_tool": "get_workflow_trace",
        "needs_human_review": True,
        "derived": True,
        "authoritative": False,
    })


@mcp.tool()
def get_task_context(
    family_key: str = "",
    task_family: str = "",
    current_steps: str = "",
    after_step: str = "",
    min_support: int = 2,
    max_events: int = 10_000,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
) -> dict[str, Any]:
    """Return one read-only organizational context bundle for a task.

    Declared policy, when present, remains distinct from observed repeated behavior.
    The returned representation includes the existing task-context provenance and
    prompt-injection protection; it does not attest that a model consumed the data.
    """
    name = "get_task_context"
    core._begin(name)
    result = secure_runtime.secure_get("/v1/task-context", {
        "family_key": family_key,
        "task_family": task_family,
        "current_steps": current_steps,
        "after_step": after_step,
        "min_support": min(max(2, int(min_support)), 100),
        "limit": min(max(1, int(max_events)), 25_000),
        "run_limit": min(max(1, int(run_limit)), 5),
        "section_limit": min(max(1, int(section_limit)), 5),
        "max_steps_per_run": min(max(1, int(max_steps_per_run)), 24),
        "max_evidence_refs_per_item": min(max(0, int(max_evidence_refs_per_item)), 4),
    })
    return secure_runtime._finish_task_context(name, result)


@mcp.tool()
def how_did_similar_runs_go(
    family_key: str,
    current_steps: str = "",
    after_step: str = "",
    min_support: int = 2,
    max_events: int = 25_000,
    run_limit: int = 8,
) -> dict[str, Any]:
    """Summarize evidence from structurally similar prior runs for an agent.

    Returns similar runs, explicit failure patterns, observed approval-request
    hotspots and frequently observed next steps, each backed by existing evidence
    references where the source view provides them. These are observations, not
    recommendations, authorization, causal claims or inferred policy.
    """
    name = "how_did_similar_runs_go"
    core._begin(name)
    bounded_events = _bounded(max_events, maximum=100_000)
    support = min(max(2, int(min_support)), 100)
    runs = secure_runtime.secure_get("/v1/procedural-memory/similar-runs", {
        "family_key": family_key,
        "current_steps": current_steps,
        "result_limit": _bounded(run_limit, maximum=25),
        "limit": bounded_events,
    })
    failures = secure_runtime.secure_get("/v1/procedural-memory/failure-patterns", {
        "family_key": family_key,
        "min_support": support,
        "limit": bounded_events,
    })
    approvals = secure_runtime.secure_get("/v1/procedural-memory/approval-patterns", {
        "family_key": family_key,
        "min_support": support,
        "limit": bounded_events,
    })
    next_steps = secure_runtime.secure_get("/v1/procedural-memory/next-steps", {
        "family_key": family_key,
        "prefix": current_steps,
        "after_step": after_step,
        "min_support": support,
        "limit": bounded_events,
    })
    context_pack = secure_runtime.secure_get("/v1/procedural-memory/context-pack", {
        "family_key": family_key,
        "current_steps": current_steps,
        "after_step": after_step,
        "min_support": support,
        "limit": min(bounded_events, 25_000),
        "run_limit": min(_bounded(run_limit, maximum=5), 5),
        "section_limit": 3,
        "max_steps_per_run": 16,
        "max_evidence_refs_per_item": 2,
    })
    return core._finish(name, {
        "family_key": family_key,
        "similar_prior_runs": runs,
        "explicit_failure_patterns": failures,
        "approval_request_hotspots": approvals,
        "frequently_observed_next_steps": next_steps,
        "observational_context_pack": context_pack,
        "interpretation": {
            "derived": True,
            "authoritative": False,
            "prescriptive": False,
            "causal": False,
            "observed_behavior_becomes_policy": False,
            "approval_patterns_are_policy": False,
        },
        "evidence_tool": "get_workflow_trace",
    })


@mcp.tool()
def get_agent_runs(
    execution_id: str = "",
    family_key: str = "",
    since: str | None = None,
    limit: int = 20,
    max_events: int = 100,
    evidence_limit: int = 25_000,
) -> dict[str, Any]:
    """Return agent-run summaries, or one structural run trace when execution_id is set.

    Native run/trace/span identifiers, prompts, model-response content, tool
    arguments/results and hidden reasoning are not exposed. Missing coverage means
    not observed, not proof that an action did not occur.
    """
    name = "get_agent_runs"
    core._begin(name)
    opaque_id = str(execution_id or "").strip().lower()
    params: dict[str, Any] = {
        "family_key": str(family_key or ""),
        "limit": _bounded(limit, maximum=100),
        "evidence_limit": _bounded(evidence_limit, maximum=100_000),
        "max_events_per_execution": 1 if not opaque_id else _bounded(max_events, maximum=500),
    }
    if since not in (None, ""):
        params["since"] = since
    if opaque_id:
        if not opaque_id.startswith("execution:"):
            raise ToolError("execution_id must be an opaque OpenWorkGraph execution ID")
        params["execution_id"] = opaque_id
        params["limit"] = 1
        return core._finish(name, secure_runtime.secure_get("/v1/agent-execution-traces", params))
    result = secure_runtime.secure_get("/v1/agent-execution-traces", params)
    compact = {
        **{key: value for key, value in result.items() if key != "executions"},
        "executions": [_agent_run_summary(item) for item in list(result.get("executions") or [])],
        "events_omitted_from_list_view": True,
        "detail": "call get_agent_runs again with execution_id for the structural trace",
    }
    return core._finish(name, compact)


if _experimental_governance_enabled():

    @mcp.tool()
    def get_action_policy_advisory(
        family_key: str,
        proposed_step: str,
        completed_steps: str = "",
    ) -> dict[str, Any]:
        """EXPERIMENTAL: compare one proposed structural action with declared policy.

        Advisory only: this does not authorize, approve, execute or block an action.
        """
        name = "get_action_policy_advisory"
        core._begin(name)
        completed = [x.strip() for x in str(completed_steps or "").split(",") if x.strip()]
        return core._finish(name, secure_runtime.secure_post(
            "/v1/declared-policies/action-advisory",
            {"family_key": family_key, "proposed_step": proposed_step, "completed_steps": completed},
        ))

    @mcp.tool()
    def get_governed_context_pack(
        family_key: str,
        current_steps: str = "",
        after_step: str = "",
        min_support: int = 2,
        max_events: int = 10_000,
    ) -> dict[str, Any]:
        """EXPERIMENTAL: return observed context plus separately declared policy."""
        name = "get_governed_context_pack"
        core._begin(name)
        return core._finish(name, secure_runtime.secure_get(
            "/v1/procedural-memory/governed-context-pack",
            {
                "family_key": family_key,
                "current_steps": current_steps,
                "after_step": after_step,
                "min_support": min(max(2, int(min_support)), 100),
                "limit": min(max(1, int(max_events)), 25_000),
                "run_limit": 3,
                "section_limit": 3,
                "max_steps_per_run": 16,
                "max_evidence_refs_per_item": 2,
            },
        ))


@mcp.resource("openworkgraph://ai-guide")
def ai_guide() -> str:
    return core.AI_DATA_DICTIONARY_MD


@mcp.resource("openworkgraph://data-model")
def data_model() -> str:
    return (
        "OpenWorkGraph compact MCP exposes a small read-oriented surface over privacy-hardened "
        "human and agent evidence. Use get_current_work_context first, get_workflow_trace for "
        "canonical evidence, find_repeated_workflows for derived recurring patterns, "
        "how_did_similar_runs_go for descriptive prior-run feedback, get_task_context for bounded "
        "organizational context, and get_agent_runs for structural agent execution evidence. "
        "Observed repetition is never policy or permission. Missing agent signals mean not observed."
    )


__all__ = ["mcp", "_experimental_governance_enabled"]
