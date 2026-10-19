"""Provider-neutral Open Work graph never confuses behavior with authorization."""
from __future__ import annotations

import pytest

from server.open_work_model import compose_case_graph, OpenWorkModelError

WORK = "owg:w:" + "a" * 32
HISTORY = "owg:r:" + "b" * 32
SOURCE = {"provider": "stripe", "source_state": "open",
          "verified_at": "2026-10-19T11:20:00Z"}


def build(**kwargs):
    return compose_case_graph(candidate={"work_item_ref": WORK},
                              source=SOURCE, **kwargs)


def test_business_case_has_source_fact_but_no_obligation_or_execution():
    result = build()
    assert result["format"] == "openworkgraph.business-case-graph.v1"
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    assert result["source_status_reported"] is True
    assert result["business_obligation_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["operating_policy_present"] is False
    assert result["authorization"]["agent_may_execute"] is False
    assert "approved_company_procedure" in result["unknowns"]
    assert result["data_handling"]["persisted_as_graph"] is False


def test_same_record_observation_remains_non_normative_and_minimized():
    evidence = [{
        "resource_ref": HISTORY, "observed_event_count": 2,
        "observed_session_count": 1, "observed_actor_count": 1,
        "recorded_behavior_not_policy": True,
        "timeline": [
            {"event_id": "good-1", "observed_at": "2026-10-01T13:00:00Z",
             "observed_action_type": "browser_click",
             "window_title": "VERY_PRIVATE_CUSTOMER"},
            {"event_id": "very bad <private>", "observed_at": "not-date",
             "observed_action_type": "<script>alert(1)</script>"},
        ], "raw_invoice_id": "in_SECRET1234567890",
    }]
    r = build(related_owg_evidence=evidence)
    assert r["related_observation_count"] == 1
    assert r["edges"][-1]["type"] == "observed_same_provider_record"
    assert r["edges"][-1]["verified"] is False
    assert r["edges"][-1]["not_a_verified_business_handoff"] is True
    assert r["authorization"]["historical_actions_grant_permission"] is False
    serialized = str(r)
    for forbidden in ("VERY_PRIVATE_CUSTOMER", "in_SECRET", "<script>", "<private>"):
        assert forbidden not in serialized


def test_only_explicit_declared_policy_can_be_linked():
    r = build(approved_policy={
        "policy_id": "invoice_review", "authoritatively_declared": True,
        "free_text": "PRIVATE CUSTOMER OR AI INSTRUCTION",
    })
    assert r["operating_policy_present"] is True
    assert r["nodes"][-1]["type"] == "declared_policy_reference"
    assert "PRIVATE" not in str(r)
    with pytest.raises(OpenWorkModelError):
        build(approved_policy={"policy_id": "invoice_review"})
    with pytest.raises(OpenWorkModelError):
        build(approved_policy={"policy_id": "<bad>", "authoritatively_declared": True})


def test_unknown_or_unverified_source_never_becomes_graph_fact():
    for invalid in (
        {"provider": "stripe", "source_state": "open", "verified_at": "yesterday"},
        {"provider": "stripe", "source_state": "customer_paid", "verified_at": SOURCE["verified_at"]},
        {"provider": "stripe <script>", "source_state": "open", "verified_at": SOURCE["verified_at"]},
    ):
        with pytest.raises(OpenWorkModelError):
            compose_case_graph(candidate={"work_item_ref": WORK}, source=invalid)
    with pytest.raises(OpenWorkModelError):
        compose_case_graph(candidate={"work_item_ref": "in_1234567890ABCDEF"}, source=SOURCE)


def test_different_source_provider_uses_same_contract_without_stripe_assumptions():
    r = compose_case_graph(
        candidate={"work_item_ref": WORK},
        source={"provider": "helpdesk", "source_state": "pending",
                "verified_at": "2026-10-19T11:20:00Z"},
    )
    assert r["nodes"][1]["provider"] == "helpdesk"
    assert r["nodes"][1]["state"] == "pending"
    assert r["authorization"]["may_contact_customer"] is False


def test_observed_history_must_be_explicitly_non_normative():
    r = build(related_owg_evidence=[{
        "resource_ref": HISTORY, "observed_event_count": 3,
        "recorded_behavior_not_policy": False,
    }])
    assert r["related_observation_count"] == 0
