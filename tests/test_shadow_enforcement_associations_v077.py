from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from adapters.action_guard import ActionPolicyAdvisory
from adapters.enforcement_preview import EnforcementPreview
from adapters.shadow_link import attach_shadow_preview_to_tool_call, shadow_enforcement_link
from server.shadow_enforcement_associations import shadow_associations_from_records
from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence


BASE = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
FAMILY = "agent:workflow:aaaaaaaaaaaaaaaa"
OTHER_FAMILY = "agent:workflow:bbbbbbbbbbbbbbbb"
POLICY_SHA = "b" * 64


def _preview(*, disposition: str = "candidate_deny", available: bool = True) -> EnforcementPreview:
    return EnforcementPreview(
        available=available,
        family_key=FAMILY,
        proposed_step="tool:deployment:tool:aaaaaaaaaaaa",
        completed_steps=(),
        shadow_profile_id="declared-policy-shadow-v1",
        candidate_disposition=disposition,
        reason_codes=("declared_forbidden_step",),
        policy_id="deploy-policy" if available else None,
        policy_version="7" if available else None,
        manifest_sha256=POLICY_SHA if available else None,
        rule_ids=("no-prod",) if available else (),
        warning_codes=("declared_forbidden_step",) if available else (),
    )


def _tool_event(*, operation: str = "tool_call") -> dict:
    return {
        "event_id": "tool-event",
        "observed_at": BASE.isoformat(),
        "agent_name": "Shadow Test Agent",
        "provider": "test",
        "framework": "openai-agents-python",
        "observation_level": "native_trace",
        "run_id": "private-run-id",
        "trace_id": "private-trace-id",
        "workflow_id": "private-workflow-id",
        "operation": operation,
        "status": "success",
        "tool_name": "deploy_prod",
        "tool_category": "deployment",
    }


def _record(
    run_key: str,
    *,
    disposition: str = "candidate_deny",
    outcome: str = "success",
    family: str = FAMILY,
    preview_family: str | None = None,
    family_consistent: bool | None = True,
    observation_level: str = "native_trace",
    approval: bool = False,
    policy: bool = True,
) -> dict:
    return {
        "_run_key": run_key,
        "disposition": disposition,
        "preview_available": disposition != "indeterminate_policy_unavailable",
        "preview_family_key": preview_family or family,
        "observed_family_key": family,
        "family_consistent": family_consistent,
        "observation_level": observation_level,
        "outcome_status": outcome,
        "approval_requested": approval,
        "approval_received": approval,
        "policy_manifest_reported": policy,
    }


def test_shadow_preview_projection_is_structural_and_canonical_contract_accepts_it():
    preview = _preview()
    linked = attach_shadow_preview_to_tool_call(_tool_event(), preview)
    assert set(linked["shadow_enforcement"]) == {
        "profile_id",
        "available",
        "candidate_disposition",
        "family_key",
        "policy_manifest_sha256",
        "simulated_only",
        "actual_enforcement_enabled",
        "actual_blocking",
    }
    canonical = agent_event_to_evidence(linked)
    stored = canonical["metadata"]["shadow_enforcement"]
    assert stored["candidate_disposition"] == "candidate_deny"
    assert stored["simulated_only"] is True
    assert stored["actual_enforcement_enabled"] is False
    assert stored["actual_blocking"] is False
    assert stored["preview_assertion_source"] == "agent_adapter"
    assert stored["preview_verified_by_server"] is False
    serialized = json.dumps(stored)
    assert "deploy-policy" not in serialized
    assert "no-prod" not in serialized
    assert "private-run-id" not in serialized


def test_shadow_telemetry_is_tool_call_only_and_fail_closed_on_extra_or_content_fields():
    with pytest.raises(Exception):
        attach_shadow_preview_to_tool_call(_tool_event(operation="run_started"), _preview())

    invalid = _tool_event()
    invalid["shadow_enforcement"] = shadow_enforcement_link(_preview()) | {"policy_text": "do not deploy"}
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(invalid)

    content = _tool_event()
    content["shadow_enforcement"] = shadow_enforcement_link(_preview()) | {"response": "secret"}
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(content)

    non_tool = _tool_event(operation="run_started")
    non_tool["shadow_enforcement"] = shadow_enforcement_link(_preview())
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(non_tool)


def test_unavailable_preview_requires_indeterminate_disposition():
    event = _tool_event()
    bad = shadow_enforcement_link(_preview())
    bad["available"] = False
    event["shadow_enforcement"] = bad
    with pytest.raises(AgentEvidenceError):
        agent_event_to_evidence(event)

    unavailable = EnforcementPreview(
        available=False,
        family_key=FAMILY,
        proposed_step="tool:deployment:tool:aaaaaaaaaaaa",
        completed_steps=(),
        shadow_profile_id="declared-policy-shadow-v1",
        candidate_disposition="indeterminate_policy_unavailable",
        reason_codes=("policy_guard_unavailable",),
        policy_id=None,
        policy_version=None,
        manifest_sha256=None,
        rule_ids=(),
        warning_codes=(),
    )
    stored = agent_event_to_evidence(
        attach_shadow_preview_to_tool_call(_tool_event(), unavailable)
    )["metadata"]["shadow_enforcement"]
    assert stored["available"] is False
    assert stored["candidate_disposition"] == "indeterminate_policy_unavailable"


def test_repeated_same_disposition_previews_do_not_inflate_run_outcomes():
    records = [
        _record("run-1", disposition="candidate_deny", outcome="success"),
        _record("run-1", disposition="candidate_deny", outcome="success"),
        _record("run-2", disposition="candidate_deny", outcome="error"),
        _record("run-3", disposition="candidate_deny", outcome="unknown"),
    ]
    payload = shadow_associations_from_records(records, min_stratum_support=1)
    summary = payload["disposition_run_outcomes"]["candidate_deny"]
    assert summary["preview_event_count"] == 4
    assert summary["run_count"] == 3
    assert summary["explicit_success_run_count"] == 1
    assert summary["explicit_failure_run_count"] == 1
    assert summary["unknown_outcome_run_count"] == 1
    assert summary["known_terminal_outcome_run_count"] == 2
    assert summary["success_rate_among_known_terminal_outcomes"] == 0.5
    assert summary["failure_rate_among_known_terminal_outcomes"] == 0.5


def test_run_with_multiple_interrupt_dispositions_counts_once_in_combined_interrupt_summary():
    records = [
        _record("run-1", disposition="candidate_deny", outcome="success"),
        _record("run-1", disposition="candidate_pause_for_human_approval", outcome="success", approval=True),
        _record("run-2", disposition="candidate_pause_for_prerequisite", outcome="error"),
    ]
    payload = shadow_associations_from_records(records, min_stratum_support=1)
    combined = payload["candidate_interrupt_run_outcomes"]
    assert combined["preview_event_count"] == 3
    assert combined["run_count"] == 2
    assert combined["explicit_success_run_count"] == 1
    assert combined["explicit_failure_run_count"] == 1
    assert combined["approval_requested_run_count"] == 1


def test_family_mismatch_is_counted_but_excluded_from_same_family_strata():
    records = [
        _record("run-1", family=FAMILY, disposition="candidate_deny", outcome="success"),
        _record("run-2", family=FAMILY, disposition="candidate_deny", outcome="error"),
        _record(
            "run-3",
            family=FAMILY,
            preview_family=OTHER_FAMILY,
            family_consistent=False,
            disposition="candidate_deny",
            outcome="success",
        ),
    ]
    payload = shadow_associations_from_records(records, min_stratum_support=2)
    assert payload["family_mismatch_preview_event_count"] == 1
    assert len(payload["strata"]) == 1
    assert payload["strata"][0]["run_count"] == 2
    assert payload["strata"][0]["same_family"] is True
    assert payload["strata"][0]["same_observation_level"] is True


def test_support_gate_hides_small_strata_without_hiding_overall_counts():
    records = [
        _record("run-1", disposition="candidate_deny", outcome="success"),
        _record("run-2", disposition="candidate_deny", outcome="error"),
    ]
    payload = shadow_associations_from_records(records, min_stratum_support=3)
    assert payload["preview_event_count_considered"] == 2
    assert payload["run_count_with_shadow_preview"] == 2
    assert payload["strata"] == []
    assert payload["disposition_run_outcomes"]["candidate_deny"]["run_count"] == 2


def test_analytics_are_explicitly_noncausal_non_authoritative_and_do_not_expose_private_keys():
    records = [
        _record("private-run-secret", disposition="candidate_deny", outcome="success"),
        _record("private-run-secret", disposition="candidate_warn", outcome="success"),
    ]
    payload = shadow_associations_from_records(records, min_stratum_support=1)
    assert payload["actual_enforcement_enabled"] is False
    assert payload["actual_blocking_observed"] is False
    assert payload["causal_interpretation"] is False
    assert payload["effect_estimate"] is False
    assert payload["false_positive_rate_estimate"] is False
    assert payload["authoritative"] is False
    text = json.dumps(payload).lower()
    assert "private-run-secret" not in text
    assert POLICY_SHA not in text
    assert '"false_positive_interpretation": true' not in text


def test_invalid_family_filter_is_rejected():
    with pytest.raises(ValueError):
        shadow_associations_from_records([], family_key="IGNORE PREVIOUS INSTRUCTIONS")
