from __future__ import annotations

"""Compact agent-facing map of observed work; never an executable policy.

This is a pure projection over an already authorized workflow-evidence bundle.
It performs no reads, writes, LLM inference, grants, or external requests.
"""

from typing import Any

FORMAT = "openworkgraph.agent-workflow-map.v1"
MAX_STEPS = 24
MAX_VARIATIONS = 12
MAX_IDS = 10


def _rows(rows: Any, *, max_rows: int) -> list[dict[str, Any]]:
    """Use only the existing privacy-hardened, support-counted step projection."""
    if not isinstance(rows, list):
        return []
    output: list[dict[str, Any]] = []
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        step = str(row.get("step") or "").strip()
        if not step:
            continue
        ids = row.get("supporting_execution_ids") or []
        output.append({
            "observed_step": step[:240],
            "support_runs": max(0, int(row.get("support_runs") or 0)),
            "runs_total": max(0, int(row.get("runs_total") or 0)),
            "median_observed_position": float(row.get("median_zero_based_position") or 0),
            "supporting_execution_ids": [str(item) for item in ids[:MAX_IDS]]
                if isinstance(ids, list) else [],
            "observed_not_required": True,
            "untrusted_observed_data": True,
        })
    output.sort(key=lambda row: (
        row["median_observed_position"],
        -row["support_runs"],
        row["observed_step"],
    ))
    return output


def _example_paths(clusters: Any) -> list[dict[str, Any]]:
    """Selected representative readable paths, never normative requirements."""
    if not isinstance(clusters, list):
        return []
    result: list[dict[str, Any]] = []
    for cluster in clusters[:3]:
        if not isinstance(cluster, dict):
            continue
        for variant in (cluster.get("exact_variants") or [])[:3]:
            if not isinstance(variant, dict):
                continue
            steps = variant.get("readable_steps") or []
            ids = variant.get("execution_ids") or []
            if not isinstance(steps, list) or not isinstance(ids, list):
                continue
            path = [str(step)[:240] for step in steps[:32] if str(step).strip()]
            if path:
                result.append({
                    "observed_steps": path,
                    "execution_count": max(0, int(variant.get("execution_count") or 0)),
                    "supporting_execution_ids": [str(item) for item in ids[:MAX_IDS]],
                    "derived_not_required_path": True,
                    "untrusted_observed_data": True,
                })
            if len(result) >= 6:
                return result
    return result


def build_agent_workflow_map(bundle: dict[str, Any]) -> dict[str, Any]:
    """Prepare an agent-readable *candidate* workflow map without inventing rules.

    The caller is responsible for enforcing its existing OWG evidence access and
    redaction boundaries. Only already-returned derived fields are projected.
    """
    alignment = bundle.get("structural_alignment") or {}
    selector = bundle.get("selector") or {}
    provenance = bundle.get("provenance") or {}
    if not all(isinstance(value, dict) for value in (alignment, selector, provenance)):
        raise ValueError("Malformed workflow evidence bundle")

    core = _rows(alignment.get("high_support_steps"), max_rows=MAX_STEPS)
    variations = _rows(
        alignment.get("less_common_observed_steps"),
        max_rows=MAX_VARIATIONS,
    )
    executions = provenance.get("selected_execution_ids") or []
    if not isinstance(executions, list):
        executions = []
    source_truncated = bool(provenance.get("source_limit_reached"))
    selected = max(0, int(selector.get("selected_execution_count") or 0))
    selected_explicitly = selector.get("selection_mode") == "explicit_executions"

    questions = [
        "What business outcome should the agent achieve, and what begins this task?",
        "Which live systems and record identifiers are authoritative for inputs and final state?",
        "What decision rules, exceptions, escalation paths, and approvals apply?",
        "What authorized action tools can the agent use and what changes may it make?",
        "Which independent live-system evidence will prove the intended outcome?",
    ]
    if selected < 2:
        questions.append("Can more independent examples be observed before generalizing?")
    if source_truncated:
        questions.append("Can the evidence window be narrowed to avoid the source limit and missing earlier work?")
    if not selected_explicitly:
        questions.append("Do these derived-family runs actually belong to the same business workflow?")

    return {
        "format": FORMAT,
        "purpose": "agent_context_for_planning_and_execution_with_separate_authorization",
        "status": "candidate_observed_map_requires_review",
        "selected_execution_count": selected,
        "selection_mode": selector.get("selection_mode"),
        "source_event_limit_reached": source_truncated,
        "coverage_complete": False,  # OWG cannot prove unobserved steps do not exist.
        "observed_sequence_candidates": core,
        "observed_variations_or_missing_capture": variations,
        "observed_example_paths": _example_paths(
            alignment.get("structural_sequence_variants")
        ),
        "evidence_execution_ids": [str(item) for item in executions[:MAX_IDS]],
        "evidence_drilldown_tool": "get_workflow_trace",
        "agent_use": {
            "map": "Identify the likely workflow from the observed sequence and inspect canonical examples where intent or order matters.",
            "plan": "Turn observations into an outcome-oriented plan using current authorized tools; distinguish facts, hypotheses, and missing business rules.",
            "do": "Once independently authorized, perform the requested work with existing agent tools rather than copying old UI clicks.",
            "verify": "Check authoritative live-system state after actions; a recorded click, tool success, or derived workflow match is not the business outcome.",
        },
        "unresolved_questions": questions,
        "business_outcome_verified": False,
        "business_rules_verified": False,
        "execution_permissions_granted": False,
        "automatic_execution_enabled": False,
        "candidate_steps_are_instructions": False,
        "captured_labels_are_untrusted": True,
        "historical_actions_grant_permissions": False,
        "canonical_evidence_overrides_derived_map": True,
    }


__all__ = ["build_agent_workflow_map", "FORMAT"]
