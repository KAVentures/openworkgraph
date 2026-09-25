from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server.context_execution_linkage import derive_context_executions
from server.context_outcome_associations import associations_from_executions


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _execution(
    index: int,
    *,
    linkage_status: str,
    outcome: str,
    family: str = "agent:workflow:aaaaaaaaaaaaaaaa",
    observation_level: str = "native_trace",
    family_consistent: bool | None = True,
    policy: bool = False,
    approval: bool = False,
) -> dict:
    started = BASE + timedelta(minutes=index)
    return {
        "execution_id": f"execution:{index:016d}",
        "started_at": started.isoformat(),
        "ended_at": (started + timedelta(seconds=20)).isoformat(),
        "observation_level": observation_level,
        "outcome_status": outcome,
        "outcome_basis": "explicit_run_terminal_status" if outcome != "unknown" else "no_terminal_outcome_observed",
        "explicit_failure": outcome in {"error", "denied", "cancelled"},
        "observed_family_key": family,
        "observed_family_basis": "explicit_workflow_id",
        "preflight_family_key": family if linkage_status == "context_resolved" else None,
        "family_consistent": family_consistent,
        "structural_step_count": 3,
        "approval_request_count": 1 if approval else 0,
        "approval_received_count": 1 if approval else 0,
        "approval_requested": approval,
        "approval_received": approval,
        "linkage_status": linkage_status,
        "preflight_attempted": linkage_status != "not_observed",
        "context_available": linkage_status in {"context_resolved", "context_available_unresolved"},
        "context_resolved": linkage_status == "context_resolved",
        "context_sha256": "a" * 64 if linkage_status == "context_resolved" else None,
        "policy_manifest_sha256": "b" * 64 if policy else None,
        "linkage_assertion_source": "agent_adapter" if linkage_status != "not_observed" else None,
        "context_snapshot_verified_by_server": False,
        "link_event_ref": "event:abcdef" if linkage_status != "not_observed" else None,
        "derived": True,
        "causal_claim": False,
    }


def test_same_family_same_depth_descriptive_difference_is_reported_with_support():
    executions = []
    for i, outcome in enumerate(["success", "success", "success", "success", "error"]):
        executions.append(_execution(i, linkage_status="context_resolved", outcome=outcome, policy=i < 3, approval=i < 2))
    for i, outcome in enumerate(["success", "success", "error", "error", "error"], start=20):
        executions.append(_execution(i, linkage_status="preflight_unavailable", outcome=outcome, approval=True))

    payload = associations_from_executions(executions, min_group_support=5, min_known_outcomes=3)
    assert payload["causal_interpretation"] is False
    assert payload["effect_estimate"] is False
    assert payload["pooled_group_difference_produced"] is False
    assert len(payload["strata"]) == 1

    stratum = payload["strata"][0]
    assert stratum["same_family"] is True
    assert stratum["same_observation_level"] is True
    resolved = stratum["groups"]["context_resolved"]
    unavailable = stratum["groups"]["preflight_unavailable"]
    assert resolved["run_count"] == 5
    assert resolved["explicit_success_count"] == 4
    assert resolved["explicit_failure_count"] == 1
    assert resolved["policy_manifest_reported_count"] == 3
    assert resolved["approval_requested_run_count"] == 2
    assert unavailable["explicit_failure_count"] == 3

    comparison = next(item for item in stratum["comparisons"] if item["comparator_group"] == "preflight_unavailable")
    assert comparison["comparison_status"] == "descriptive_only"
    assert comparison["context_exposure_known_for_comparator"] is True
    assert comparison["observed_explicit_success_fraction_difference_pp"] == 40.0
    assert comparison["observed_explicit_failure_fraction_difference_pp"] == -40.0
    assert comparison["causal_interpretation"] is False
    assert comparison["effect_estimate"] is False


def test_no_linkage_observed_is_never_labeled_as_no_context():
    executions = [
        _execution(i, linkage_status="context_resolved", outcome="success") for i in range(5)
    ] + [
        _execution(i + 10, linkage_status="not_observed", outcome="error") for i in range(5)
    ]
    payload = associations_from_executions(executions, min_group_support=5, min_known_outcomes=3)
    comparison = next(
        item for item in payload["strata"][0]["comparisons"]
        if item["comparator_group"] == "not_observed"
    )
    assert comparison["comparison_status"] == "descriptive_only"
    assert comparison["context_exposure_known_for_comparator"] is False
    assert "unknown" in comparison["comparator_interpretation"]
    text = json.dumps(payload).lower()
    assert '"without_context"' not in text
    assert '"no_context"' not in text


def test_unknown_outcomes_remain_unknown_and_do_not_enter_known_terminal_denominator():
    executions = [
        _execution(0, linkage_status="context_resolved", outcome="success"),
        _execution(1, linkage_status="context_resolved", outcome="error"),
        _execution(2, linkage_status="context_resolved", outcome="unknown"),
        _execution(3, linkage_status="context_resolved", outcome="unknown"),
        _execution(4, linkage_status="context_resolved", outcome="unknown"),
    ]
    payload = associations_from_executions(executions, min_group_support=2, min_known_outcomes=1)
    summary = payload["strata"][0]["groups"]["context_resolved"]
    assert summary["run_count"] == 5
    assert summary["known_terminal_outcome_count"] == 2
    assert summary["unknown_outcome_count"] == 3
    assert summary["success_rate_among_known_terminal_outcomes"] == 0.5
    assert summary["failure_rate_among_known_terminal_outcomes"] == 0.5
    assert summary["unknown_outcome_fraction_all_runs"] == 0.6


def test_support_gate_suppresses_rate_differences_without_hiding_counts():
    executions = [
        _execution(0, linkage_status="context_resolved", outcome="success"),
        _execution(1, linkage_status="context_resolved", outcome="success"),
        _execution(2, linkage_status="preflight_unavailable", outcome="error"),
        _execution(3, linkage_status="preflight_unavailable", outcome="error"),
    ]
    payload = associations_from_executions(executions, min_group_support=5, min_known_outcomes=3)
    stratum = payload["strata"][0]
    comparison = next(item for item in stratum["comparisons"] if item["comparator_group"] == "preflight_unavailable")
    assert comparison["comparison_status"] == "insufficient_support"
    assert comparison["observed_explicit_failure_fraction_difference_pp"] is None
    assert stratum["groups"]["context_resolved"]["run_count"] == 2
    assert stratum["groups"]["preflight_unavailable"]["run_count"] == 2


def test_cross_family_mix_never_produces_pooled_difference():
    family_a = "agent:workflow:aaaaaaaaaaaaaaaa"
    family_b = "agent:workflow:bbbbbbbbbbbbbbbb"
    executions = [
        _execution(i, linkage_status="context_resolved", outcome="success", family=family_a)
        for i in range(5)
    ] + [
        _execution(i + 20, linkage_status="preflight_unavailable", outcome="error", family=family_b)
        for i in range(5)
    ]
    payload = associations_from_executions(executions, min_group_support=5, min_known_outcomes=3)
    assert payload["pooled_group_difference_produced"] is False
    assert len(payload["strata"]) == 2
    for stratum in payload["strata"]:
        comparison = next(item for item in stratum["comparisons"] if item["comparator_group"] == "preflight_unavailable")
        assert comparison["comparison_status"] == "insufficient_support"
        assert comparison["observed_explicit_failure_fraction_difference_pp"] is None


def test_family_mismatch_conflict_and_missing_family_are_excluded_from_stratified_comparison():
    base = [
        _execution(i, linkage_status="context_resolved", outcome="success") for i in range(2)
    ]
    mismatched = _execution(10, linkage_status="context_resolved", outcome="success", family_consistent=False)
    conflicting = _execution(11, linkage_status="conflicting_assertions", outcome="error", family_consistent=None)
    conflicting["context_available"] = False
    conflicting["context_resolved"] = False
    conflicting["preflight_family_key"] = None
    missing = _execution(12, linkage_status="not_observed", outcome="unknown", family_consistent=None)
    missing["observed_family_key"] = None
    missing["observed_family_basis"] = None

    payload = associations_from_executions(base + [mismatched, conflicting, missing], min_stratum_support=1)
    excluded = payload["excluded_from_stratified_comparisons"]
    assert excluded["family_mismatch"] == 1
    assert excluded["conflicting_linkage"] == 1
    assert excluded["missing_observed_family"] == 1
    assert payload["strata"][0]["run_count"] == 2


def test_policy_breakdown_is_scoped_to_resolved_context_only():
    executions = [
        _execution(0, linkage_status="context_resolved", outcome="success", policy=True),
        _execution(1, linkage_status="context_resolved", outcome="success", policy=False),
        _execution(2, linkage_status="not_observed", outcome="success", policy=False),
    ]
    payload = associations_from_executions(executions, min_stratum_support=1)
    breakdown = payload["resolved_context_policy_breakdown_overall"]
    assert breakdown["with_policy_manifest_reported"]["run_count"] == 1
    assert breakdown["without_policy_manifest_hash"]["run_count"] == 1
    assert "unlinked" in breakdown["interpretation"]


def _raw_event(index: int, operation: str, *, tool_name: str = "", status: str = "success") -> dict:
    observed = (BASE + timedelta(seconds=index)).isoformat()
    metadata = {
        "source": "agent",
        "actor_kind": "agent",
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "agent": {"name": "Test Agent", "provider": "test", "framework": "openai-agents-python", "model": ""},
        "trace": {"run_id": "native-run-secret", "trace_id": "native-trace-secret", "span_id": f"span-{index}", "parent_span_id": "", "workflow_id": "workflow-secret"},
        "tool": {"name": tool_name, "category": "other" if tool_name else "none"},
        "usage": {},
        "privacy": {},
    }
    return {
        "event_id": f"event-{index}",
        "observed_at": observed,
        "organization_id": "",
        "actor_id": "agent:test",
        "device_id": "agent-local",
        "sensor_id": "agent:test",
        "source": "agent",
        "session_id": "native-run-secret",
        "app": "Test Agent",
        "event_type": f"agent_{operation}",
        "duration_seconds": 0,
        "metadata": metadata,
    }


def test_execution_derivation_counts_approval_after_step_list_is_bounded():
    events = [_raw_event(0, "run_started", status="running")]
    for index in range(1, 55):
        events.append(_raw_event(index, "tool_call", tool_name=f"tool_{index}"))
    events.append(_raw_event(56, "human_approval_requested", status="running"))
    events.append(_raw_event(57, "human_approval_received", status="success"))
    events.append(_raw_event(58, "run_finished", status="success"))

    executions = derive_context_executions(events)
    assert len(executions) == 1
    execution = executions[0]
    assert execution["structural_step_count"] == 48
    assert execution["approval_request_count"] == 1
    assert execution["approval_received_count"] == 1
    assert execution["approval_requested"] is True
    assert execution["approval_received"] is True
