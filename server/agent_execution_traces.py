from __future__ import annotations

"""Privacy-safe, provider-neutral structural traces for observed agent runs."""

from collections import Counter
import hashlib
import re
from typing import Any

from .agent_tool_labels import readable_structural_step
from shared.tool_detail import sanitize_detail

from .context_execution_linkage import _agent_groups, _meta, _one_execution
from .procedural_memory import _event_ref


_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_EXECUTION_ID_RE = re.compile(r"^execution:[0-9a-f]{16}$")
_FAILURE_STATUSES = frozenset({"error", "cancelled", "denied"})
_EDIT_TOOL_NAMES = frozenset({
    "edit", "multiedit", "write", "notebookedit", "edit_file", "write_file", "delete_file",
    "file_edit", "file_write", "file_delete", "apply_patch",
})
_USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")


def _opaque_ref(prefix: str, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return f"{prefix}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _agent_descriptor(events: list[dict[str, Any]]) -> dict[str, str]:
    for event in events:
        meta, _trace = _meta(event)
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        if agent:
            return {
                "name": str(agent.get("name") or "Agent")[:160],
                "provider": str(agent.get("provider") or "")[:160],
                "framework": str(agent.get("framework") or "")[:160],
            }
    return {"name": "Agent", "provider": "", "framework": ""}


def _observation_levels(events: list[dict[str, Any]]) -> list[str]:
    levels: set[str] = set()
    for event in events:
        meta, _trace = _meta(event)
        levels.add(str(meta.get("observation_level") or "unknown"))
    return sorted(levels)


def _safe_usage(meta: dict[str, Any]) -> dict[str, int]:
    raw = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    out: dict[str, int] = {}
    for key in _USAGE_KEYS:
        try:
            amount = int(raw.get(key))
        except Exception:
            continue
        if 0 <= amount <= 1_000_000_000:
            out[key] = amount
    return out


def _usage_totals(projected: list[dict[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for item in projected:
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        for key in _USAGE_KEYS:
            if key in usage:
                totals[key] += int(usage.get(key) or 0)
    return {key: int(totals[key]) for key in _USAGE_KEYS if key in totals}


def _token_observability(
    projected: list[dict[str, Any]],
    *,
    agent: dict[str, str],
    observation_levels: list[str],
) -> dict[str, Any]:
    totals = _usage_totals(projected)
    if totals:
        return {
            "status": "observed",
            "basis": "canonical_provider_or_sdk_usage_telemetry",
            "counts": totals,
            "estimated": False,
        }
    framework = str(agent.get("framework") or "").strip().lower()
    if framework == "cursor":
        basis = "cursor_hook_does_not_expose_token_usage"
    elif observation_levels and set(observation_levels) <= {"native_trace"}:
        basis = "native_structural_observation_has_no_token_usage_signal"
    else:
        basis = "token_usage_signal_not_observed"
    return {
        "status": "not_observed",
        "basis": basis,
        "counts": {},
        "estimated": False,
        "absence_means": "not_observed_not_zero",
    }


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove the same tool call observed through hook/OTel and transcript fallback.

    Exact opaque span identity is the only cross-sensor de-duplication key here;
    adjacent calls with the same tool name are never collapsed. If both exist,
    prefer a non-native-session sensor because hooks/OTel carry richer facts.
    """
    output: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for event in events:
        meta, trace = _meta(event)
        operation = str(meta.get("operation") or "")
        span = str(trace.get("span_id") or "").strip()
        key = (operation, span) if operation == "tool_call" and span else None
        if key is None:
            output.append(event)
            continue
        if key not in positions:
            positions[key] = len(output)
            output.append(event)
            continue
        current_index = positions[key]
        current = output[current_index]
        current_native = str(current.get("sensor_id") or "").startswith("agent:native-session:")
        candidate_native = str(event.get("sensor_id") or "").startswith("agent:native-session:")
        if current_native and not candidate_native:
            output[current_index] = event
    output.sort(key=lambda item: str(item.get("observed_at") or ""))
    return output


def _work_summary(projected: list[dict[str, Any]]) -> dict[str, Any]:
    """What the run did, from content-free tool detail: commands, git/gh
    operations, test results (including unknowns), files, lines and observed tokens."""
    commands: Counter[str] = Counter()
    git_ops: Counter[str] = Counter()
    gh_ops: Counter[str] = Counter()
    file_types: Counter[str] = Counter()
    edited: set[str] = set()
    read_only: set[str] = set()
    lines_added = lines_removed = 0
    test_runs: list[dict[str, Any]] = []
    totals = _usage_totals(projected)
    for item in projected:
        tool = item.get("tool") if isinstance(item.get("tool"), dict) else {}
        detail = (tool.get("detail") or {}) if item.get("operation") == "tool_call" else {}
        if not detail:
            continue
        commands.update(detail.get("commands") or [])
        git_ops.update(detail.get("git") or [])
        gh_ops.update(detail.get("gh") or [])
        file_types.update(detail.get("file_types") or [])
        tool_name = str(tool.get("name") or "").strip().lower()
        changed = "lines_added" in detail or "lines_removed" in detail or tool_name in _EDIT_TOOL_NAMES
        for ref in detail.get("file_refs") or []:
            (edited if changed else read_only).add(ref)
        lines_added += int(detail.get("lines_added") or 0)
        lines_removed += int(detail.get("lines_removed") or 0)
        test_status = str(detail.get("test_status") or "")
        if not test_status and ("tests_passed" in detail or "tests_failed" in detail):
            test_status = "failing" if int(detail.get("tests_failed") or 0) else "passing"
        if test_status in {"passing", "failing", "unknown"}:
            test_runs.append({
                "passed": int(detail.get("tests_passed") or 0),
                "failed": int(detail.get("tests_failed") or 0),
                "status": test_status,
            })
    summary: dict[str, Any] = {}
    if commands:
        summary["commands"] = dict(commands.most_common(12))
    if git_ops:
        summary["git"] = dict(git_ops.most_common())
    if gh_ops:
        summary["gh"] = dict(gh_ops.most_common())
    if test_runs:
        last = test_runs[-1]
        known_failing = sum(1 for t in test_runs if t["status"] == "failing" or t["failed"])
        known_passing = sum(1 for t in test_runs if t["status"] == "passing")
        unknown = sum(1 for t in test_runs if t["status"] == "unknown")
        tests: dict[str, Any] = {
            "runs": len(test_runs),
            "known_passing_runs": known_passing,
            "known_failing_runs": known_failing,
            "unknown_result_runs": unknown,
            "ended": last["status"],
        }
        if known_failing or unknown == 0:
            tests["runs_with_failures"] = known_failing
        else:
            tests["runs_with_failures"] = None
            tests["runs_with_failures_status"] = "unknown_no_failure_count_observed"
        if last["status"] != "unknown":
            tests["last_passed"] = last["passed"]
            tests["last_failed"] = last["failed"]
        summary["tests"] = tests
    if edited or read_only:
        summary["files"] = {"edited": len(edited), "read_only": len(read_only - edited)}
    if file_types:
        summary["file_types"] = dict(file_types.most_common(8))
    if lines_added or lines_removed:
        summary["lines"] = {"added": lines_added, "removed": lines_removed}
    if totals:
        summary["token_usage_observed"] = True
        if "total_tokens" in totals:
            summary["total_tokens"] = totals["total_tokens"]
        elif "input_tokens" in totals or "output_tokens" in totals:
            summary["total_tokens"] = int(totals.get("input_tokens") or 0) + int(totals.get("output_tokens") or 0)
    return summary


def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
    meta, trace = _meta(event)
    agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
    tool = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
    task_context = meta.get("task_context") if isinstance(meta.get("task_context"), dict) else {}
    shadow = meta.get("shadow_enforcement") if isinstance(meta.get("shadow_enforcement"), dict) else {}
    operation = str(meta.get("operation") or "unknown")
    status = str(meta.get("status") or "unknown")
    tool_name = str(tool.get("name") or "")[:200]
    tool_category = str(tool.get("category") or "none")[:80]

    item: dict[str, Any] = {
        "event_ref": _event_ref(event.get("event_id")),
        "observed_at": event.get("observed_at"),
        "operation": operation,
        "status": status,
        "duration_seconds": event.get("duration_seconds") or 0.0,
        "span_ref": _opaque_ref("span", trace.get("span_id")),
        "parent_span_ref": _opaque_ref("span", trace.get("parent_span_id")),
        "structural_step": readable_structural_step(
            operation=operation,
            status=status,
            tool_name=tool_name,
            tool_category=tool_category,
        ) or None,
    }

    model = str(agent.get("model") or "")[:200]
    if model:
        item["model"] = model

    if operation in {"tool_call", "human_approval_requested", "human_approval_received"} or tool_name:
        item["tool"] = {
            "name": tool_name or None,
            "category": tool_category,
        }
        detail = sanitize_detail(tool.get("detail"))
        if detail:
            item["tool"]["detail"] = detail

    usage = _safe_usage(meta)
    if usage:
        item["usage"] = usage

    if task_context:
        handoff_status = str(task_context.get("handoff_status") or "").strip().lower() or "not_asserted"
        item["task_context"] = {
            "preflight_attempted": task_context.get("preflight_attempted") is True,
            "available": task_context.get("available") is True,
            "resolved": task_context.get("resolved") is True,
            "family_key": str(task_context.get("family_key") or "") or None,
            "adapter_reported": str(task_context.get("linkage_assertion_source") or "") == "agent_adapter",
            "server_attested": task_context.get("context_snapshot_verified_by_server") is True,
            "handoff_status": handoff_status,
            "handoff_adapter_reported": bool(task_context.get("handoff_status"))
            and str(task_context.get("handoff_assertion_source") or "") == "agent_adapter",
            "handoff_server_attested": task_context.get("handoff_verified_by_server") is True,
            "model_context_consumption_attested": task_context.get("model_context_consumption_attested") is True,
        }

    if shadow:
        item["shadow_enforcement"] = {
            "profile_id": str(shadow.get("profile_id") or "") or None,
            "available": shadow.get("available") is True,
            "candidate_disposition": str(shadow.get("candidate_disposition") or "") or None,
            "family_key": str(shadow.get("family_key") or "") or None,
            "simulated_only": shadow.get("simulated_only") is True,
            "actual_enforcement_enabled": shadow.get("actual_enforcement_enabled") is True,
            "actual_blocking": shadow.get("actual_blocking") is True,
            "adapter_reported": str(shadow.get("preview_assertion_source") or "") == "agent_adapter",
            "server_attested": shadow.get("preview_verified_by_server") is True,
        }

    return item


def _observed_coverage(
    projected: list[dict[str, Any]],
    *,
    observation_levels: list[str],
) -> dict[str, Any]:
    """Describe only which structural signal classes are present in evidence.

    A false value means the signal was not observed in this evidence. It must not
    be interpreted as proof that the underlying runtime never performed it.
    """
    operations = {str(item.get("operation") or "unknown") for item in projected}
    signals = {
        "run_start": "run_started" in operations,
        "run_finish": "run_finished" in operations,
        "model_call": "model_call" in operations,
        "tool_call": "tool_call" in operations,
        "handoff": "handoff" in operations,
        "human_approval_requested": "human_approval_requested" in operations,
        "human_approval_received": "human_approval_received" in operations,
        "error_event": "error" in operations,
        "failure_status": any(str(item.get("status") or "") in _FAILURE_STATUSES for item in projected),
        "span_identity": any(bool(item.get("span_ref")) for item in projected),
        "parent_child_span_linkage": any(bool(item.get("span_ref")) and bool(item.get("parent_span_ref")) for item in projected),
        "model_identity": any(bool(item.get("model")) for item in projected),
        "tool_identity": any(
            isinstance(item.get("tool"), dict)
            and (bool((item.get("tool") or {}).get("name")) or str((item.get("tool") or {}).get("category") or "none") != "none")
            for item in projected
        ),
        "duration": any(float(item.get("duration_seconds") or 0.0) > 0 for item in projected),
        "token_usage": any(isinstance(item.get("usage"), dict) and bool(item.get("usage")) for item in projected),
        "tool_detail": any(bool((item.get("tool") or {}).get("detail")) for item in projected),
        "structural_step": any(bool(item.get("structural_step")) for item in projected),
        "task_context_linkage": any(isinstance(item.get("task_context"), dict) for item in projected),
        "context_handoff_assertion": any(
            isinstance(item.get("task_context"), dict)
            and str((item.get("task_context") or {}).get("handoff_status") or "not_asserted") != "not_asserted"
            for item in projected
        ),
        "context_handoff_delivered_to_runtime": any(
            isinstance(item.get("task_context"), dict)
            and str((item.get("task_context") or {}).get("handoff_status") or "") == "delivered_to_runtime"
            for item in projected
        ),
        "shadow_enforcement_preview": any(isinstance(item.get("shadow_enforcement"), dict) for item in projected),
    }
    return {
        "observation_levels_observed": observation_levels,
        "mixed_observation_levels": len(observation_levels) > 1,
        "coverage_basis": "signals_present_in_canonical_evidence",
        "signals_observed": signals,
        "observed_signal_names": sorted(key for key, observed in signals.items() if observed),
        "unobserved_signal_names": sorted(key for key, observed in signals.items() if not observed),
        "absence_means": "not_observed_not_proof_of_nonoccurrence",
        "internal_runtime_completeness_attested": False,
        "hidden_reasoning_observed": False,
        "authoritative": False,
    }


def _workspace(events: list[dict[str, Any]]) -> dict[str, str]:
    """The run's keyed workspace hash, when the adapter reported one."""
    from shared.tool_detail import valid_workspace_ref

    for event in events:
        meta, _trace = _meta(event)
        ref = valid_workspace_ref(meta.get("workspace_ref"))
        if ref:
            return {"workspace_ref": ref}
    return {}


def _resolve_outcome(base: dict[str, Any], work_summary: dict[str, Any]) -> tuple[Any, Any]:
    outcome = base.get("outcome_status")
    basis = base.get("outcome_basis")
    if str(outcome or "").lower() not in {"", "unknown", "not_observed"}:
        return outcome, basis
    tests = work_summary.get("tests") if isinstance(work_summary.get("tests"), dict) else {}
    ended = str(tests.get("ended") or "")
    if ended == "passing":
        return "success", "latest_observed_test_run_passing"
    if ended == "failing":
        return "error", "latest_observed_test_run_failing"
    return outcome, basis


def _one_trace(events: list[dict[str, Any]], *, max_events: int) -> dict[str, Any]:
    events = _dedupe_events(events)
    base = _one_execution(events)
    projected = [_event_projection(event) for event in events]
    operation_counts = Counter(str(item.get("operation") or "unknown") for item in projected)
    tool_category_counts = Counter(
        str((item.get("tool") or {}).get("category") or "none")
        for item in projected
        if isinstance(item.get("tool"), dict)
    )
    structural_steps = [
        str(item.get("structural_step"))
        for item in projected
        if item.get("structural_step")
    ]
    run_start_observed = any(item.get("operation") == "run_started" for item in projected)
    run_finish_observed = any(item.get("operation") == "run_finished" for item in projected)
    bounded_events = projected[:max_events]
    levels = _observation_levels(events)
    coverage = _observed_coverage(projected, observation_levels=levels)
    agent = _agent_descriptor(events)
    work_summary = _work_summary(projected)
    outcome_status, outcome_basis = _resolve_outcome(base, work_summary)
    usage_totals = _usage_totals(projected)

    return {
        "execution_id": base["execution_id"],
        "started_at": base.get("started_at"),
        "ended_at": base.get("ended_at"),
        "agent": agent,
        "observation_level": base.get("observation_level"),
        "outcome_status": outcome_status,
        "outcome_basis": outcome_basis,
        "observed_family_key": base.get("observed_family_key"),
        "observed_family_basis": base.get("observed_family_basis"),
        "run_start_observed": run_start_observed,
        "run_finish_observed": run_finish_observed,
        "work_summary": work_summary,
        "usage_totals": usage_totals,
        "token_usage": _token_observability(projected, agent=agent, observation_levels=levels),
        **_workspace(events),
        "complete_boundary_observed": run_start_observed and run_finish_observed,
        "event_count_total": len(projected),
        "event_count_returned": len(bounded_events),
        "events_truncated": len(projected) > len(bounded_events),
        "operation_counts": dict(sorted(operation_counts.items())),
        "tool_category_counts": dict(sorted(tool_category_counts.items())),
        "structural_steps": structural_steps[:96],
        "structural_steps_truncated": len(structural_steps) > 96,
        "approval_request_count": base.get("approval_request_count", 0),
        "approval_received_count": base.get("approval_received_count", 0),
        "task_context_linkage_status": base.get("linkage_status"),
        "observed_coverage": coverage,
        "events": bounded_events,
        "derived": True,
        "authoritative": False,
    }


def _native_keys(events: list[dict[str, Any]]) -> tuple[str, set[str], set[str]]:
    """Return the native trace (session) plus parent handoff and child run-start spans.

    A child run (a subagent) has its own run id but shares the parent's trace, so
    parent and child are matched within one trace rather than one run.
    """
    run = ""
    handoff_spans: set[str] = set()
    start_spans: set[str] = set()
    for event in events:
        meta, trace = _meta(event)
        run = run or str(trace.get("trace_id") or trace.get("run_id") or event.get("session_id") or "")
        span = str(trace.get("span_id") or "")
        if not span:
            continue
        operation = str(meta.get("operation") or "")
        if operation == "handoff":
            handoff_spans.add(span)
        elif operation == "run_started":
            start_spans.add(span)
    return run, handoff_spans, start_spans


def _parent_child_links(groups: list[list[dict[str, Any]]]) -> dict[int, dict[str, Any]]:
    """Link subagent executions to the parent handoff without exposing native IDs."""
    keys = [_native_keys(events) for events in groups]
    ids = [_one_execution(events)["execution_id"] if events else "" for events in groups]
    links: dict[int, dict[str, Any]] = {}
    for child_index, (child_run, _child_handoffs, child_starts) in enumerate(keys):
        if not child_run or not child_starts:
            continue
        for parent_index, (parent_run, parent_handoffs, _parent_starts) in enumerate(keys):
            if parent_index == child_index or parent_run != child_run:
                continue
            if child_starts & parent_handoffs:
                links.setdefault(child_index, {})["parent"] = ids[parent_index]
                links.setdefault(parent_index, {}).setdefault("children", []).append(ids[child_index])
                break
    return links


def _run_material(events: list[dict[str, Any]]) -> str:
    from .outcome_tracker import run_material

    _meta_value, trace = _meta(events[0])
    return run_material(events[0].get("actor_id"), trace, events[0].get("session_id"))


def agent_execution_traces(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    execution_id: str = "",
    limit: int = 20,
    max_events_per_execution: int = 100,
) -> dict[str, Any]:
    """Return bounded structural traces without native run/trace/span identifiers."""
    family = str(family_key or "").strip().lower()
    if family and not _FAMILY_KEY_RE.fullmatch(family):
        raise ValueError("invalid family_key")
    execution = str(execution_id or "").strip().lower()
    if execution and not _EXECUTION_ID_RE.fullmatch(execution):
        raise ValueError("invalid execution_id")

    run_limit = max(1, min(int(limit), 100))
    event_limit = max(1, min(int(max_events_per_execution), 500))
    groups = [events for events in _agent_groups(raw_events) if events]
    links = _parent_child_links(groups)
    materials = [_run_material(events) for events in groups]
    from .human_agent_join import human_context

    human = human_context(groups, raw_events)
    try:
        from .outcome_tracker import delivery_outcomes

        outcomes = delivery_outcomes(materials)
    except Exception:
        outcomes = {}

    traces: list[dict[str, Any]] = []
    considered = 0
    for index, events in enumerate(groups):
        candidate = _one_trace(events, max_events=event_limit)
        link = links.get(index, {})
        candidate["parent_execution_id"] = link.get("parent")
        candidate["child_execution_ids"] = list(link.get("children") or [])
        if materials[index] in outcomes:
            candidate["delivery_outcome"] = outcomes[materials[index]]
        if index in human:
            candidate["human_context"] = human[index]
        if family and candidate.get("observed_family_key") != family:
            continue
        if execution and candidate.get("execution_id") != execution:
            continue
        considered += 1
        if len(traces) < run_limit:
            traces.append(candidate)

    return {
        "executions": traces,
        "returned": len(traces),
        "agent_execution_count_considered": considered,
        "max_events_per_execution": event_limit,
        "coverage_semantics": {
            "true_means": "at_least_one_matching_structural_signal_was_observed",
            "false_means": "signal_not_observed_not_proof_the_underlying_action_did_not_occur",
            "coverage_is_vendor_capability_claim": False,
            "hidden_reasoning_is_observable": False,
            "token_usage_not_observed_means_zero": False,
        },
        "context_handoff_semantics": {
            "not_asserted": "no adapter assertion about handoff preparation or runtime delivery",
            "prepared": "the integration reports that the verified handoff envelope was prepared",
            "delivered_to_runtime": "the integration reports that the verified handoff envelope was passed to the runtime",
            "delivery_proves_model_read_or_use": False,
            "delivery_proves_model_compliance": False,
            "server_attestation_of_delivery": False,
        },
        "native_run_ids_exposed": False,
        "native_trace_ids_exposed": False,
        "native_span_ids_exposed": False,
        "prompt_content_exposed": False,
        "model_response_content_exposed": False,
        "tool_arguments_exposed": False,
        "tool_results_exposed": False,
        "chain_of_thought_exposed": False,
        "token_counts_are_estimated": False,
        "derived": True,
        "authoritative": False,
        "source": "canonical_agent_evidence",
        "interpretation": "provider-neutral structural execution traces; completeness depends on which lifecycle and usage signals the instrumented agent runtime exposed",
    }
