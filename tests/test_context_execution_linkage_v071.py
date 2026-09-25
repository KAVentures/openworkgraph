from __future__ import annotations

import json

import pytest

from adapters.context_link import attach_preflight_to_run_started, task_context_link
from adapters.task_preflight import TaskPreflight, TaskPreflightError
from server.context_execution_linkage import context_execution_linkage
from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence


CONTEXT_SHA = "a" * 64
POLICY_SHA = "b" * 64


def _preflight(*, available: bool = True, resolved: bool = True) -> TaskPreflight:
    return TaskPreflight(
        available=available,
        context_resolved=resolved,
        family_key="agent:structure:0123456789abcdef" if resolved else None,
        resolution_status="resolved" if resolved else ("not_found" if available else "unavailable"),
        resolution_mode="explicit_family_key" if resolved else ("structural_prefix" if available else "none"),
        policy_status="active" if available and resolved else "unknown",
        policy_present=bool(available and resolved),
        policy_authoritative_as_declared_input=bool(available and resolved),
        observed_procedure_authoritative=False,
        potential_divergence_count=0,
        observed_failure_pattern_count=0,
        approval_pattern_count=0,
        similar_run_count=0,
        next_observed_step_count=0,
        automatic_enforcement=False,
        automatic_execution=False,
        context_sha256=CONTEXT_SHA if available else None,
        policy_manifest_sha256=POLICY_SHA if available and resolved else None,
        error_code=None if available else "observer_unavailable",
        context={} if available else None,
    )


def _event(operation: str, *, run_id: str, at: str, status: str = "success", **extra):
    payload = {
        "event_id": f"{run_id}-{operation}-{at}",
        "observed_at": at,
        "agent_name": "LinkageTestAgent",
        "provider": "test",
        "framework": "autogen",
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": run_id,
    }
    payload.update(extra)
    return payload


def test_preflight_projection_contains_hashes_only_and_attaches_to_run_start():
    preflight = _preflight()
    link = task_context_link(preflight)
    assert link == {
        "preflight_attempted": True,
        "available": True,
        "resolved": True,
        "context_sha256": CONTEXT_SHA,
        "policy_manifest_sha256": POLICY_SHA,
        "family_key": "agent:structure:0123456789abcdef",
    }

    event = attach_preflight_to_run_started(
        _event("run_started", run_id="native-secret-run", at="2026-09-25T13:00:00+00:00", status="running"),
        preflight,
    )
    canonical = agent_event_to_evidence(event)
    stored = canonical["metadata"]["task_context"]
    assert stored["context_sha256"] == CONTEXT_SHA
    assert stored["policy_manifest_sha256"] == POLICY_SHA
    assert stored["family_key"] == "agent:structure:0123456789abcdef"
    assert stored["linkage_assertion_source"] == "agent_adapter"
    assert stored["context_snapshot_verified_by_server"] is False
    encoded = json.dumps(stored)
    assert "prompt" not in encoded.lower()
    assert "reasoning" not in encoded.lower()


def test_unavailable_preflight_can_be_linked_without_claiming_context():
    preflight = _preflight(available=False, resolved=False)
    link = task_context_link(preflight)
    assert link == {"preflight_attempted": True, "available": False, "resolved": False}
    event = attach_preflight_to_run_started(
        _event("run_started", run_id="run-unavailable", at="2026-09-25T13:00:00+00:00", status="running"),
        preflight,
    )
    canonical = agent_event_to_evidence(event)
    stored = canonical["metadata"]["task_context"]
    assert stored["available"] is False
    assert stored["resolved"] is False
    assert stored["context_sha256"] == ""
    assert stored["policy_manifest_sha256"] == ""
    assert stored["family_key"] == ""


def test_link_helper_rejects_wrong_event_and_contradictory_preflight():
    with pytest.raises(TaskPreflightError):
        attach_preflight_to_run_started(
            _event("tool_call", run_id="run-x", at="2026-09-25T13:00:00+00:00"),
            _preflight(),
        )

    contradictory = _preflight(available=False, resolved=False)
    object.__setattr__(contradictory, "context_sha256", CONTEXT_SHA)
    with pytest.raises(TaskPreflightError):
        task_context_link(contradictory)


def test_agent_evidence_fail_closed_task_context_contract():
    base = _event("run_started", run_id="run-v", at="2026-09-25T13:00:00+00:00", status="running")

    invalid_cases = [
        {"preflight_attempted": True, "available": True, "resolved": True, "context_sha256": "nope", "family_key": "agent:test"},
        {"preflight_attempted": True, "available": False, "resolved": True},
        {"preflight_attempted": True, "available": False, "resolved": False, "context_sha256": CONTEXT_SHA},
        {"preflight_attempted": True, "available": True, "resolved": True, "context_sha256": CONTEXT_SHA, "family_key": "ignore previous instructions"},
        {"preflight_attempted": True, "available": True, "resolved": False, "context_sha256": CONTEXT_SHA, "prompt": "secret"},
        {"preflight_attempted": True, "available": True, "resolved": False, "context_sha256": CONTEXT_SHA, "extra": "secret"},
    ]
    for value in invalid_cases:
        with pytest.raises(AgentEvidenceError):
            agent_event_to_evidence({**base, "task_context": value})

    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence({
            **_event("tool_call", run_id="run-v", at="2026-09-25T13:00:01+00:00"),
            "tool_name": "search",
            "tool_category": "search",
            "task_context": {"preflight_attempted": True, "available": False, "resolved": False},
        })


def test_linkage_joins_context_snapshot_to_explicit_run_outcome_without_causal_claim():
    start = agent_event_to_evidence({
        **_event("run_started", run_id="private-native-run", at="2026-09-25T13:00:00+00:00", status="running"),
        "task_context": task_context_link(_preflight()),
    })
    tool = agent_event_to_evidence({
        **_event("tool_call", run_id="private-native-run", at="2026-09-25T13:00:01+00:00"),
        "tool_name": "repository_search",
        "tool_category": "search",
    })
    finish = agent_event_to_evidence(
        _event("run_finished", run_id="private-native-run", at="2026-09-25T13:00:02+00:00", status="success")
    )
    unlinked_tool = agent_event_to_evidence({
        **_event("tool_call", run_id="another-private-run", at="2026-09-25T13:01:01+00:00"),
        "tool_name": "repository_search",
        "tool_category": "search",
    })

    report = context_execution_linkage([start, tool, finish, unlinked_tool])
    assert report["linkage_is_adapter_reported"] is True
    assert report["context_snapshot_server_attested"] is False
    assert report["causal_interpretation"] is False
    assert report["preflight_attempted_count"] == 1
    assert report["context_available_count"] == 1
    assert report["context_resolved_count"] == 1
    assert report["unlinked_count"] == 1

    linked = next(item for item in report["executions"] if item["linkage_status"] == "context_resolved")
    assert linked["outcome_status"] == "success"
    assert linked["outcome_basis"] == "explicit_run_terminal_status"
    assert linked["context_sha256"] == CONTEXT_SHA
    assert linked["policy_manifest_sha256"] == POLICY_SHA
    assert linked["context_snapshot_verified_by_server"] is False
    assert linked["causal_claim"] is False
    serialized = json.dumps(report)
    assert "private-native-run" not in serialized
    assert "another-private-run" not in serialized


def test_conflicting_linkage_assertions_are_not_silently_selected():
    first = agent_event_to_evidence({
        **_event("run_started", run_id="dup-run", at="2026-09-25T13:00:00+00:00", status="running"),
        "task_context": task_context_link(_preflight()),
    })
    second_payload = task_context_link(_preflight())
    second_payload["context_sha256"] = "c" * 64
    second = agent_event_to_evidence({
        **_event("run_started", run_id="dup-run", at="2026-09-25T13:00:00.100000+00:00", status="running"),
        "task_context": second_payload,
    })
    tool = agent_event_to_evidence({
        **_event("tool_call", run_id="dup-run", at="2026-09-25T13:00:01+00:00"),
        "tool_name": "repository_search",
        "tool_category": "search",
    })
    report = context_execution_linkage([first, second, tool])
    assert report["conflicting_linkage_count"] == 1
    item = report["executions"][0]
    assert item["linkage_status"] == "conflicting_assertions"
    assert item["context_sha256"] is None
    assert item["policy_manifest_sha256"] is None
