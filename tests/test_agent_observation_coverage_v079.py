from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.agent_execution_traces import agent_execution_traces
from shared.agent_evidence import agent_event_to_evidence


BASE = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)


def _canonical_events(*, observation_level: str = "native_trace") -> list[dict]:
    common = {
        "agent_name": "Coverage Agent",
        "provider": "test",
        "framework": "coverage-test",
        "observation_level": observation_level,
        "run_id": f"coverage-{observation_level}",
        "trace_id": f"coverage-trace-{observation_level}",
    }
    specs = [
        ("run_started", "running", {}),
        ("model_call", "success", {"model": "model-x", "span_id": "model-span", "duration_seconds": 1.0, "usage": {"input_tokens": 8, "output_tokens": 3}}),
        ("tool_call", "success", {"span_id": "tool-span", "parent_span_id": "model-span", "tool_name": "search", "tool_category": "search", "duration_seconds": 0.2}),
        ("handoff", "success", {"span_id": "handoff-span", "parent_span_id": "model-span"}),
        ("human_approval_requested", "running", {"tool_name": "deploy", "tool_category": "deployment"}),
        ("run_finished", "success", {}),
    ]
    return [
        agent_event_to_evidence({
            **common,
            "event_id": f"coverage-{index}",
            "observed_at": (BASE + timedelta(seconds=index)).isoformat(),
            "operation": operation,
            "status": status,
            **extra,
        })
        for index, (operation, status, extra) in enumerate(specs)
    ]


def _outcome_only_event() -> dict:
    return agent_event_to_evidence({
        "event_id": "outcome-only",
        "observed_at": BASE.isoformat(),
        "agent_name": "Closed Surface Agent",
        "provider": "third-party",
        "framework": "external-surface",
        "observation_level": "outcome_only",
        "run_id": "outcome-only-run",
        "operation": "run_finished",
        "status": "success",
    })


def test_native_trace_coverage_reports_only_structural_signals_actually_present():
    execution = agent_execution_traces(_canonical_events())["executions"][0]
    coverage = execution["observed_coverage"]
    signals = coverage["signals_observed"]

    assert coverage["observation_levels_observed"] == ["native_trace"]
    assert coverage["mixed_observation_levels"] is False
    assert coverage["coverage_basis"] == "signals_present_in_canonical_evidence"
    assert signals["run_start"] is True
    assert signals["run_finish"] is True
    assert signals["model_call"] is True
    assert signals["tool_call"] is True
    assert signals["handoff"] is True
    assert signals["human_approval_requested"] is True
    assert signals["human_approval_received"] is False
    assert signals["parent_child_span_linkage"] is True
    assert signals["model_identity"] is True
    assert signals["tool_identity"] is True
    assert signals["duration"] is True
    assert signals["token_usage"] is True
    assert signals["task_context_linkage"] is False
    assert signals["shadow_enforcement_preview"] is False
    assert coverage["absence_means"] == "not_observed_not_proof_of_nonoccurrence"
    assert coverage["internal_runtime_completeness_attested"] is False
    assert coverage["hidden_reasoning_observed"] is False
    assert coverage["authoritative"] is False


def test_outcome_only_trace_does_not_turn_unobserved_operations_into_negative_facts():
    payload = agent_execution_traces([_outcome_only_event()])
    execution = payload["executions"][0]
    coverage = execution["observed_coverage"]
    signals = coverage["signals_observed"]

    assert execution["observation_level"] == "outcome_only"
    assert coverage["observation_levels_observed"] == ["outcome_only"]
    assert signals["run_finish"] is True
    assert signals["run_start"] is False
    assert signals["model_call"] is False
    assert signals["tool_call"] is False
    assert signals["handoff"] is False
    assert signals["human_approval_requested"] is False
    assert signals["span_identity"] is False
    assert "tool_call" in coverage["unobserved_signal_names"]
    assert payload["coverage_semantics"]["false_means"] == "signal_not_observed_not_proof_the_underlying_action_did_not_occur"
    assert payload["coverage_semantics"]["coverage_is_vendor_capability_claim"] is False
    assert payload["coverage_semantics"]["hidden_reasoning_is_observable"] is False


def test_coverage_is_computed_before_response_event_truncation():
    execution = agent_execution_traces(
        _canonical_events(),
        max_events_per_execution=1,
    )["executions"][0]
    assert execution["events_truncated"] is True
    assert execution["event_count_returned"] == 1
    signals = execution["observed_coverage"]["signals_observed"]
    assert signals["model_call"] is True
    assert signals["tool_call"] is True
    assert signals["handoff"] is True


def test_mixed_observation_levels_are_reported_instead_of_silently_collapsed():
    events = _canonical_events()
    events[2]["metadata"]["observation_level"] = "mcp_only"
    execution = agent_execution_traces(events)["executions"][0]
    coverage = execution["observed_coverage"]

    # Existing top-level observation_level remains the first-event value for
    # compatibility, while coverage explicitly reports the heterogeneous run.
    assert execution["observation_level"] == "native_trace"
    assert coverage["observation_levels_observed"] == ["mcp_only", "native_trace"]
    assert coverage["mixed_observation_levels"] is True
