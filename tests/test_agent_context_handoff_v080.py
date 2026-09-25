from __future__ import annotations

from dataclasses import replace

import pytest

from adapters.context_handoff import build_context_handoff
from adapters.context_link import task_context_link
from adapters.task_preflight import TaskPreflight, TaskPreflightClient, TaskPreflightError


FAMILY = "agent:example.workflow"
MANIFEST_SHA = "a" * 64


def _payload(*, resolved: bool = True, automatic_context_injection: bool = False) -> dict:
    base = {
        "schema_version": "1.0",
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
        "automatic_context_injection": automatic_context_injection,
    }
    if not resolved:
        return {
            **base,
            "resolution": {
                "status": "insufficient_input",
                "mode": "structural_prefix",
                "family_key": None,
                "family_known": False,
                "candidates": [],
                "free_text_resolution_used": False,
                "input_echoed": False,
            },
            "context_available": False,
            "task_context": None,
        }
    return {
        **base,
        "resolution": {
            "status": "resolved",
            "mode": "explicit_family_key",
            "family_key": FAMILY,
            "family_known": True,
            "candidates": [],
            "free_text_resolution_used": False,
            "input_echoed": False,
        },
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
                "item": {
                    "family_key": FAMILY,
                    "status": "active",
                    "required_predecessors": ["human_approval"],
                },
            },
            "observed_procedure": {
                "authority_class": "observed_evidence",
                "authoritative": False,
                "prescriptive": False,
                "failure_patterns": [],
                "approval_patterns": [],
                "similar_runs": [],
                "next_observed_steps": [
                    {"step": "tool:search:search_repo", "support": 3}
                ],
            },
            "policy_observation_comparison": {
                "potential_divergence_count": 0,
            },
            "provenance": {
                "policy_source": "declared_policy_manifest",
                "observed_source": "canonical_evidence_via_procedural_memory",
                "comparison_source": "derived_structural_comparison",
                "evidence_is_canonical": True,
                "memory_is_regeneratable": True,
                "persisted_learned_procedure": False,
            },
        },
    }


def _preflight(*, resolved: bool = True, automatic_context_injection: bool = False):
    payload = _payload(
        resolved=resolved,
        automatic_context_injection=automatic_context_injection,
    )
    client = TaskPreflightClient(fetcher=lambda _params: payload)
    if resolved:
        return client.preflight(family_key=FAMILY), payload
    return client.preflight(), payload


def test_resolved_handoff_preserves_exact_snapshot_and_run_linkage():
    preflight, payload = _preflight()
    handoff = build_context_handoff(preflight)

    assert handoff.available is True
    assert handoff.context_resolved is True
    assert handoff.family_key == FAMILY
    assert handoff.context_sha256 == preflight.context_sha256
    assert handoff.policy_manifest_sha256 == MANIFEST_SHA
    assert handoff.linkage_dict() == task_context_link(preflight)

    envelope = handoff.as_dict()
    assert envelope["context"] == payload
    assert envelope["run_start_linkage"] == handoff.linkage_dict()
    assert envelope["consumer_contract"]["explicit_opt_in_required"] is True
    assert envelope["consumer_contract"]["automatic_context_injection"] is False
    assert envelope["consumer_contract"]["automatic_execution"] is False
    assert envelope["consumer_contract"]["automatic_policy_enforcement"] is False
    assert envelope["consumer_contract"]["observed_procedure_authoritative"] is False
    assert envelope["consumer_contract"]["observed_behavior_is_permission_source"] is False
    assert envelope["consumer_contract"]["embedded_observed_data_is_agent_instruction"] is False


def test_handoff_copy_cannot_mutate_verified_internal_snapshot():
    preflight, _payload_value = _preflight()
    handoff = build_context_handoff(preflight)

    first = handoff.as_dict()
    first["context"]["read_only"] = False
    first["run_start_linkage"]["available"] = False

    second = handoff.as_dict()
    assert second["context"]["read_only"] is True
    assert second["run_start_linkage"]["available"] is True
    assert handoff.linkage_dict()["available"] is True


def test_tampered_snapshot_is_rejected_even_when_other_contract_fields_still_look_safe():
    preflight, _payload_value = _preflight()
    tampered = dict(preflight.context or {})
    tampered["future_metadata"] = {"changed_after_preflight": True}
    changed = replace(preflight, context=tampered)

    with pytest.raises(TaskPreflightError, match="fingerprint mismatch"):
        build_context_handoff(changed)


def test_reachable_but_unresolved_context_can_be_handed_off_without_claiming_resolution():
    preflight, payload = _preflight(resolved=False)
    handoff = build_context_handoff(preflight)
    envelope = handoff.as_dict()

    assert handoff.available is True
    assert handoff.context_resolved is False
    assert handoff.family_key is None
    assert handoff.context_sha256
    assert envelope["context"] == payload
    assert envelope["run_start_linkage"]["preflight_attempted"] is True
    assert envelope["run_start_linkage"]["available"] is True
    assert envelope["run_start_linkage"]["resolved"] is False
    assert "family_key" not in envelope["run_start_linkage"]


def test_observer_unavailable_handoff_is_explicit_and_contains_no_context():
    preflight = TaskPreflight.unavailable()
    handoff = build_context_handoff(preflight)
    envelope = handoff.as_dict()

    assert envelope["available"] is False
    assert envelope["context_resolved"] is False
    assert envelope["context"] is None
    assert envelope["context_sha256"] is None
    assert envelope["run_start_linkage"] == {
        "preflight_attempted": True,
        "available": False,
        "resolved": False,
    }


def test_handoff_rejects_snapshot_that_enables_automatic_context_injection():
    preflight, _payload_value = _preflight(automatic_context_injection=True)
    with pytest.raises(TaskPreflightError, match="automatic injection"):
        build_context_handoff(preflight)
