from __future__ import annotations

import json

import pytest

from adapters.context_delivery import attach_context_delivery_to_run_started, context_delivery_link
from adapters.context_handoff import build_context_handoff
from adapters.task_preflight import TaskPreflight, TaskPreflightClient, TaskPreflightError
from server.agent_execution_traces import agent_execution_traces
from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence


FAMILY = "agent:delivery.example"
MANIFEST_SHA = "a" * 64


def _payload() -> dict:
    return {
        "schema_version": "1.0",
        "resolution": {
            "status": "resolved",
            "mode": "explicit_family_key",
            "family_key": FAMILY,
            "family_known": True,
            "candidates": [],
            "free_text_resolution_used": False,
            "input_echoed": False,
        },
        "authority_model": {
            "declared_policy": "normative_input_when_active",
            "observed_procedure": "non_authoritative_derived_evidence",
            "policy_observation_comparison": "derived_assessment_not_enforcement",
            "family_resolution": "derived_routing_only",
            "policy_inferred_from_behavior": False,
            "observed_behavior_becomes_policy": False,
            "automatic_execution": False,
            "automatic_policy_enforcement": False,
        },
        "read_only": True,
        "writes_performed": False,
        "free_text_task_matching": False,
        "automatic_context_injection": False,
        "context_available": True,
        "task_context": {
            "family_key": FAMILY,
            "policy": {
                "status": "active",
                "authority_class": "declared_normative",
                "authoritative_as_declared_input": True,
                "policy_inferred_from_behavior": False,
                "cryptographic_verification_asserted_by_task_context": False,
                "manifest_sha256": MANIFEST_SHA,
                "manifest_present": True,
                "item": {"family_key": FAMILY, "status": "active"},
            },
            "observed_procedure": {
                "authority_class": "observed_evidence",
                "authoritative": False,
                "prescriptive": False,
                "failure_patterns": [],
                "approval_patterns": [],
                "similar_runs": [],
                "next_observed_steps": [],
            },
            "policy_observation_comparison": None,
            "provenance": {
                "policy_source": "declared_policy_manifest",
                "observed_source": "canonical_evidence_via_procedural_memory",
                "comparison_source": None,
                "evidence_is_canonical": True,
                "memory_is_regeneratable": True,
                "persisted_learned_procedure": False,
            },
        },
    }


def _handoff():
    payload = _payload()
    preflight = TaskPreflightClient(fetcher=lambda _params: payload).preflight(family_key=FAMILY)
    return build_context_handoff(preflight)


def _run_started(task_context: dict | None = None) -> dict:
    event = {
        "event_id": "delivery-event-secret",
        "observed_at": "2026-09-26T00:00:00Z",
        "agent_name": "Delivery Test Agent",
        "provider": "test",
        "framework": "custom",
        "operation": "run_started",
        "status": "running",
        "observation_level": "native_trace",
        "run_id": "delivery-run-secret",
    }
    if task_context is not None:
        event["task_context"] = task_context
    return event


def test_legacy_task_context_without_delivery_status_remains_valid():
    handoff = _handoff()
    event = _run_started(handoff.linkage_dict())
    evidence = agent_event_to_evidence(event)
    stored = evidence["metadata"]["task_context"]

    assert "handoff_status" not in stored
    assert "handoff_assertion_source" not in stored
    assert stored["model_context_consumption_attested"] is False

    trace = agent_execution_traces([evidence])["executions"][0]
    projected = trace["events"][0]["task_context"]
    assert projected["handoff_status"] == "not_asserted"
    assert projected["handoff_adapter_reported"] is False
    assert projected["model_context_consumption_attested"] is False
    assert trace["observed_coverage"]["signals_observed"]["context_handoff_assertion"] is False
    assert trace["observed_coverage"]["signals_observed"]["context_handoff_delivered_to_runtime"] is False


def test_prepared_status_is_adapter_reported_but_not_model_consumption():
    handoff = _handoff()
    linked = context_delivery_link(handoff, status="prepared")
    evidence = agent_event_to_evidence(_run_started(linked))
    stored = evidence["metadata"]["task_context"]

    assert stored["handoff_status"] == "prepared"
    assert stored["handoff_assertion_source"] == "agent_adapter"
    assert stored["handoff_verified_by_server"] is False
    assert stored["model_context_consumption_attested"] is False

    trace = agent_execution_traces([evidence])["executions"][0]
    projected = trace["events"][0]["task_context"]
    assert projected["handoff_status"] == "prepared"
    assert projected["handoff_adapter_reported"] is True
    assert projected["handoff_server_attested"] is False
    assert projected["model_context_consumption_attested"] is False
    assert trace["observed_coverage"]["signals_observed"]["context_handoff_assertion"] is True
    assert trace["observed_coverage"]["signals_observed"]["context_handoff_delivered_to_runtime"] is False


def test_delivered_to_runtime_never_becomes_read_use_or_compliance_claim():
    handoff = _handoff()
    event = attach_context_delivery_to_run_started(
        _run_started(),
        handoff,
        status="delivered_to_runtime",
    )
    evidence = agent_event_to_evidence(event)
    payload = agent_execution_traces([evidence])
    trace = payload["executions"][0]
    projected = trace["events"][0]["task_context"]

    assert projected["handoff_status"] == "delivered_to_runtime"
    assert projected["model_context_consumption_attested"] is False
    assert trace["observed_coverage"]["signals_observed"]["context_handoff_delivered_to_runtime"] is True
    assert payload["context_handoff_semantics"]["delivery_proves_model_read_or_use"] is False
    assert payload["context_handoff_semantics"]["delivery_proves_model_compliance"] is False
    assert payload["context_handoff_semantics"]["server_attestation_of_delivery"] is False

    serialized = json.dumps(evidence)
    assert "delivery-run-secret" in serialized
    assert _payload()["task_context"]["observed_procedure"] != {}
    assert "next_observed_steps" not in serialized


def test_invalid_or_unavailable_delivery_assertions_fail_closed():
    handoff = _handoff()
    with pytest.raises(TaskPreflightError):
        context_delivery_link(handoff, status="model_consumed")

    unavailable = build_context_handoff(TaskPreflight.unavailable())
    with pytest.raises(TaskPreflightError):
        context_delivery_link(unavailable, status="prepared")

    invalid = handoff.linkage_dict()
    invalid["handoff_status"] = "model_consumed"
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(_run_started(invalid))

    unavailable_link = {
        "preflight_attempted": True,
        "available": False,
        "resolved": False,
        "handoff_status": "prepared",
    }
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(_run_started(unavailable_link))


def test_delivery_helper_refuses_non_run_start_or_existing_task_context():
    handoff = _handoff()
    with pytest.raises(TaskPreflightError):
        attach_context_delivery_to_run_started(
            {**_run_started(), "operation": "tool_call"},
            handoff,
            status="prepared",
        )
    with pytest.raises(TaskPreflightError):
        attach_context_delivery_to_run_started(
            _run_started(handoff.linkage_dict()),
            handoff,
            status="prepared",
        )
