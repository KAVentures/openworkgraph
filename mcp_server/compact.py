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


def _slim_semantic_event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "observed_at",
            "app",
            "work_surface",
            "event_type",
            "action",
            "target_label",
            "target_role",
        )
        if item.get(key) not in (None, "", [], {})
    }


def _slim_trace_row(item: dict[str, Any]) -> dict[str, Any]:
    """Small overview row; get_workflow_trace remains the canonical full-row tool."""
    return {
        key: item.get(key)
        for key in (
            "observed_at",
            "app",
            "event_type",
            "duration_seconds",
            "source",
            "action",
            "target_label",
            "target_role",
            "foreground_seconds",
            "engaged_seconds",
            "keypress_count",
            "click_count",
            "scroll_count",
        )
        if item.get(key) not in (None, "", [], {})
    }


def _slim_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": [
            _slim_trace_row(item)
            for item in list(trace.get("rows") or [])
            if isinstance(item, dict)
        ],
        **{
            key: trace.get(key)
            for key in (
                "returned", "total", "has_more", "next_cursor", "snapshot_until",
                "scope", "since", "until", "query_applied", "app_filter",
                "data_layer", "derived_task_inference_authoritative",
            )
            if key in trace
        },
        "rows_are_compact_overview": True,
        "canonical_detail_tool": "get_workflow_trace",
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


def _family_rows(overview: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(overview.get("families") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("family_key") or "").strip()
        if not key:
            continue
        row = {
            "family_key": key,
            "actor_kind": item.get("actor_kind") or "unknown",
            "family_basis": item.get("family_basis") or "unknown",
            "execution_count": int(item.get("execution_count") or 0),
            "positive_example_count": int(item.get("positive_example_count") or 0),
            "explicit_failure_count": int(item.get("explicit_failure_count") or 0),
            "confidence": item.get("confidence") or "low",
        }
        if key.startswith("human:") and not key.startswith("human:structure:"):
            row["task_family"] = key[len("human:"):]
        rows.append(row)
        if len(rows) >= _bounded(limit, maximum=100):
            break
    return rows


def _exact_human_family_key(task_family: Any, available_keys: set[str]) -> str:
    raw = str(task_family or "").strip().casefold()
    if not raw or ":" in raw:
        return ""
    candidate = f"human:{raw}"
    return candidate if candidate in available_keys else ""


def _with_family_key(item: dict[str, Any], available_keys: set[str]) -> dict[str, Any]:
    result = _slim_pattern(item)
    key = _exact_human_family_key(item.get("task_family"), available_keys)
    if key:
        result["family_key"] = key
    return result


def _task_with_family_key(item: dict[str, Any], available_keys: set[str]) -> dict[str, Any]:
    result = _slim_task(item)
    key = _exact_human_family_key(item.get("task_family"), available_keys)
    if key:
        result["family_key"] = key
    return result


def _resolve_feedback_family(
    requested: str,
    *,
    overview: dict[str, Any],
    current_tasks: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return (status, resolved_key, method) without inventing agent identities."""
    families = _family_rows(overview, limit=100)
    available = {str(item.get("family_key") or "").casefold(): str(item.get("family_key") or "") for item in families}
    raw = str(requested or "").strip()
    low = raw.casefold()

    if low:
        exact = available.get(low)
        if exact:
            return "ok", exact, "exact_family_key"
        if ":" not in low:
            human = available.get(f"human:{low}")
            if human:
                return "ok", human, "canonical_human_task_family"
        return "unknown_family_key", "", "no_match"

    candidates: set[str] = set()
    for task in list((current_tasks or {}).get("tasks") or [])[:12]:
        if not isinstance(task, dict):
            continue
        key = _exact_human_family_key(task.get("task_family"), set(available.values()))
        if key:
            candidates.add(key)
    if len(candidates) == 1:
        return "ok", next(iter(candidates)), "current_task_exact_match"
    return "family_selection_required", "", "ambiguous_or_missing_current_family"


def _is_readable_step_input(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and ("·" in text or "→" in text))


def _readable_feedback(
    family_key: str,
    *,
    current_steps: str = "",
    after_step: str = "",
    max_events: int,
    run_limit: int,
    min_support: int,
) -> dict[str, Any]:
    return secure_runtime.secure_get("/v1/procedural-memory/readable-feedback", {
        "family_key": family_key,
        "current_steps": current_steps,
        "after_step": after_step,
        "limit": max_events,
        "result_limit": _bounded(run_limit, maximum=25),
        "min_support": min(max(2, int(min_support)), 100),
    })


@mcp.tool()
def get_current_work_context(
    limit: int = 6,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return a compact current-work overview with pointers to canonical evidence.

    Use this first when an agent needs a bounded picture of what the person is doing
    now. The embedded trace is deliberately summarized; call get_workflow_trace for
    canonical rich rows. Increase limit or follow trace pagination when more evidence
    is needed. Observed strings remain protected at the MCP boundary.
    """
    name = "get_current_work_context"
    core._begin(name)
    trace = secure_runtime.secure_get(
        "/v1/workflow-trace",
        _trace_params(cursor=cursor, limit=limit, scope="current"),
    )
    tasks = secure_runtime.secure_get("/v1/tasks", {"limit": 5000, "scope": "current"})
    semantic_limit = min(_bounded(limit, maximum=200), 6)
    semantic = secure_runtime.secure_get(
        "/v1/semantic-activity",
        {"limit": semantic_limit, "scope": "current"},
    )
    return core._finish(name, {
        "trace": _slim_trace(trace),
        "task_hints": [_slim_task(x) for x in list(tasks.get("tasks") or [])[:3]],
        "repeated_patterns": [_slim_pattern(x) for x in list(tasks.get("patterns") or [])[:3]],
        "semantic_activity": [
            _slim_semantic_event(x)
            for x in list(semantic.get("events") or [])[:semantic_limit]
            if isinstance(x, dict)
        ],
        "evidence_tool": "get_workflow_trace",
        "data_layer": "rich_ai_context_compact_overview",
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
    limit: int = 4,
    scope: str = "current",
    query: str | None = None,
    app_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return canonical chronological workflow evidence with stable pagination.

    The default page is intentionally small for agent context budgets, but row shape
    remains canonical and callers can request larger pages. Pass next_cursor back as
    cursor for more evidence. Typed text and clipboard contents are never captured.
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
    """Return repeated-workflow candidates plus exact procedural-memory family keys.

    Call this before how_did_similar_runs_go to obtain family_key. task_family stays
    a human-readable/display family; family_key is the exact procedural-memory key.
    Results are observed/derived candidates and still require human interpretation.
    """
    name = "find_repeated_workflows"
    core._begin(name)
    bounded_events = _bounded(max_events, maximum=100_000)
    tasks = secure_runtime.secure_get("/v1/tasks", {"limit": bounded_events, "scope": "all"})
    summary = secure_runtime.secure_get("/v1/summary", {"limit": bounded_events, "scope": "all"})
    overview = secure_runtime.secure_get("/v1/procedural-memory", {
        "limit": bounded_events,
        "min_support": 1,
    })
    available_rows = _family_rows(overview, limit=100)
    available_keys = {str(item.get("family_key") or "") for item in available_rows}
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
        examples.append(_task_with_family_key(task, available_keys))
        if len(examples) >= _bounded(limit, maximum=100):
            break
    patterns = [
        _with_family_key(x, available_keys)
        for x in list(tasks.get("patterns") or [])[:_bounded(limit, maximum=100)]
        if isinstance(x, dict)
    ]
    automation = [
        _with_family_key(x, available_keys)
        for x in list(summary.get("repeated_task_patterns") or [])[:_bounded(limit, maximum=100)]
        if isinstance(x, dict)
    ]
    return core._finish(name, {
        "task_family": task_family,
        "patterns": patterns,
        "automation_candidates": automation,
        "examples": examples,
        "procedural_families": available_rows[:_bounded(limit, maximum=100)],
        "next_step": "Pass an exact family_key from this result to how_did_similar_runs_go.",
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

    Supply family_key from find_repeated_workflows when available, or task_family
    for a canonical human family such as email.reply or github.review. This policy-
    preserving context surface still accepts OpenWorkGraph structural step tokens;
    use how_did_similar_runs_go for privacy-safe readable progress such as
    'Gmail · Open email'. Declared policy remains distinct from observed behavior.
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
    family_key: str = "",
    current_steps: str = "",
    after_step: str = "",
    min_support: int = 2,
    max_events: int = 25_000,
    run_limit: int = 8,
) -> dict[str, Any]:
    """Summarize evidence from similar prior runs with readable human-work steps.

    Call find_repeated_workflows first to obtain an exact family_key. A bare canonical
    human task family such as email.reply is also accepted. For human families,
    current_steps/after_step may use exact privacy-safe labels returned by this tool,
    e.g. 'Gmail · Open email'. Legacy structural tokens remain supported. Unknown
    readable steps return valid observed names instead of a generic tool error.

    Results are observations, not recommendations, authorization, causal claims or
    inferred policy.
    """
    name = "how_did_similar_runs_go"
    core._begin(name)
    bounded_events = _bounded(max_events, maximum=100_000)
    support = min(max(2, int(min_support)), 100)
    overview = secure_runtime.secure_get("/v1/procedural-memory", {
        "limit": bounded_events,
        "min_support": 1,
    })
    current_tasks = None
    if not str(family_key or "").strip():
        current_tasks = secure_runtime.secure_get("/v1/tasks", {
            "limit": min(bounded_events, 5000),
            "scope": "current",
        })
    status, resolved_key, resolution_method = _resolve_feedback_family(
        family_key,
        overview=overview,
        current_tasks=current_tasks,
    )
    available = _family_rows(overview, limit=20)
    if status != "ok":
        return core._finish(name, {
            "status": status,
            "requested_family_key": str(family_key or ""),
            "resolved_family_keys": [],
            "resolution_method": resolution_method,
            "available_families": available,
            "instruction": "Call find_repeated_workflows or choose one available family_key, then call this tool again.",
            "derived": True,
            "authoritative": False,
        })

    readable_mode = _is_readable_step_input(current_steps) or _is_readable_step_input(after_step)
    human_family = resolved_key.startswith("human:")
    readable = None
    if human_family:
        readable = _readable_feedback(
            resolved_key,
            current_steps=current_steps if readable_mode else "",
            after_step=after_step if readable_mode else "",
            max_events=bounded_events,
            run_limit=run_limit,
            min_support=support,
        )
        if readable_mode and readable.get("status") != "ok":
            return core._finish(name, {
                "status": readable.get("status") or "unrecognized_step",
                "requested_family_key": str(family_key or ""),
                "family_key": resolved_key,
                "resolved_family_keys": [resolved_key],
                "resolution_method": resolution_method,
                "unrecognized_steps": readable.get("unrecognized_steps") or [],
                "valid_semantic_steps": readable.get("valid_semantic_steps") or [],
                "instruction": readable.get("instruction"),
                "legacy_structural_steps_still_supported": True,
                "derived": True,
                "authoritative": False,
                "prescriptive": False,
            })

    structural_current = "" if readable_mode else current_steps
    structural_after = "" if readable_mode else after_step
    runs = secure_runtime.secure_get("/v1/procedural-memory/similar-runs", {
        "family_key": resolved_key,
        "current_steps": structural_current,
        "result_limit": _bounded(run_limit, maximum=25),
        "limit": bounded_events,
    })
    failures = secure_runtime.secure_get("/v1/procedural-memory/failure-patterns", {
        "family_key": resolved_key,
        "min_support": support,
        "limit": bounded_events,
    })
    approvals = secure_runtime.secure_get("/v1/procedural-memory/approval-patterns", {
        "family_key": resolved_key,
        "min_support": support,
        "limit": bounded_events,
    })
    next_steps = secure_runtime.secure_get("/v1/procedural-memory/next-steps", {
        "family_key": resolved_key,
        "prefix": structural_current,
        "after_step": structural_after,
        "min_support": support,
        "limit": bounded_events,
    })
    context_pack = secure_runtime.secure_get("/v1/procedural-memory/context-pack", {
        "family_key": resolved_key,
        "current_steps": structural_current,
        "after_step": structural_after,
        "min_support": support,
        "limit": min(bounded_events, 25_000),
        "run_limit": min(_bounded(run_limit, maximum=5), 5),
        "section_limit": 3,
        "max_steps_per_run": 16,
        "max_evidence_refs_per_item": 2,
    })

    similar_output = runs
    next_output = next_steps
    if human_family and readable and readable.get("status") == "ok":
        # With no progress supplied, prefer the readable view. With legacy structural
        # progress, preserve the structural match and provide readable evidence beside it.
        if readable_mode or (not current_steps and not after_step):
            similar_output = {
                "family_key": resolved_key,
                "runs": readable.get("runs") or [],
                "returned": readable.get("returned") or 0,
                "step_vocabulary": "privacy_safe_semantic",
                "valid_semantic_steps": readable.get("valid_semantic_steps") or [],
                "median_completed_duration_seconds": readable.get("median_completed_duration_seconds"),
                "derived": True,
                "authoritative": False,
            }
            next_output = readable.get("next_steps") or next_steps

    response = {
        "status": "ok",
        "requested_family_key": str(family_key or ""),
        "family_key": resolved_key,
        "resolved_family_keys": [resolved_key],
        "resolution_method": resolution_method,
        "similar_prior_runs": similar_output,
        "explicit_failure_patterns": failures,
        "approval_request_hotspots": approvals,
        "frequently_observed_next_steps": next_output,
        "observational_context_pack": context_pack,
        "interpretation": {
            "derived": True,
            "authoritative": False,
            "prescriptive": False,
            "causal": False,
            "observed_behavior_becomes_policy": False,
            "approval_patterns_are_policy": False,
            "semantic_steps_change_family_identity": False,
        },
        "evidence_tool": "get_workflow_trace",
    }
    if human_family and readable:
        response["readable_human_feedback"] = {
            "step_vocabulary": readable.get("step_vocabulary"),
            "identity_vocabulary": readable.get("identity_vocabulary"),
            "valid_semantic_steps": readable.get("valid_semantic_steps") or [],
            "runs": readable.get("runs") or [],
            "next_steps": readable.get("next_steps") or {},
            "median_completed_duration_seconds": readable.get("median_completed_duration_seconds"),
            "family_keys_changed": False,
        }
        response["readable_progress_applied"] = readable_mode
    return core._finish(name, response)


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
        "canonical evidence, find_repeated_workflows for derived recurring patterns and exact "
        "family keys, how_did_similar_runs_go for descriptive prior-run feedback with privacy-safe "
        "readable human steps, get_task_context for bounded organizational context, and get_agent_runs "
        "for structural agent execution evidence. Readable step labels never replace the stable "
        "structural family identity. Observed repetition is never policy or permission. Missing agent "
        "signals mean not observed."
    )


__all__ = ["mcp", "_experimental_governance_enabled"]
