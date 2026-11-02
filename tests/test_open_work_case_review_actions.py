"""Real authenticated FastAPI + private SQLite action-plan integration.

Provider responses are synthetic; no real Stripe or customer outcome certified.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session
from server.secure_app import app
from server.open_work_case_declarations import CaseDeclarationError
from server.open_work_case_review_actions import compose_outcome_review_action_plan

A, B = ("owg:w:" + char * 32 for char in "ab")
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", lambda: policy)
    monkeypatch.setattr(reviews, "read_policy", lambda: policy)
    account = {"id": "acct_PRIVATE"}
    monkeypatch.setattr(
        routes, "_read_metadata",
        lambda _path: {"account": account["id"]} if account["id"] else None,
    )
    monkeypatch.setattr(
        routes, "_read_or_create_secret",
        lambda _name: "review-actions-test-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _setup(client, headers):
    saved = {}
    for ref, deps, expected in (
        (A, [], "stripe_invoice_paid_reported"),
        (B, [A], "operation_receipt_reviewed"),
    ):
        response = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": ref,
            "goal_code": "verify_operation", "expected_outcome_code": expected,
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert response.status_code == 200, response.text
        saved[ref] = response.json()
    return saved


def _cas(saved, ref=B):
    return {
        "work_item_ref": ref,
        "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
    }


def _triage(client, headers, saved, *, max_cases=2):
    return client.post(BASE + "/human-outcomes/evidence-triage", headers=headers, json={
        **_cas(saved), "confirm_historical_review": True, "max_cases": max_cases,
    })


def _paid_scan(ref, **_kwargs):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref, "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open", "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_PRIVATE",
        },
        "related_owg_evidence": [{
            "resource_ref": R, "observed_event_count": 2,
            "observed_session_count": 1, "observed_actor_count": 1,
            "timeline": [{"customer": "PRIVATE@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
    }


def _paid_receipt(ref, operation):
    assert (ref, operation) == (R, "stripe_invoice_paid")
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": R, "expected_operation": operation,
        "availability": "source_reported", "operation_verified": True,
        "provider": "stripe", "provider_record_kind": "invoice",
        "provider_reported_state": "paid",
        "verification_scope": "authenticated_source_provider_operational_state",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "business_objective_verified": False, "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "read_only": True,
        "native_id": "in_PRIVATE",
    }


def test_action_plan_prerequisite_first_and_no_provider_reads(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        def forbid(*_a, **_k):
            pytest.fail("historical action planning must never contact Stripe")
        monkeypatch.setattr(routes, "stripe_open_work_case_context", forbid)
        monkeypatch.setattr(routes, "source_operation_receipt", forbid)
        response = _triage(client, headers, saved)
        assert response.status_code == 200, response.text
        plan = response.json()["review_action_plan"]
        assert plan["format"] == "openworkgraph.local-reviewed-outcome-action-plan.v1"
        assert [row["work_item_ref"] for row in plan["cases"]] == [A, B]
        a, b = plan["cases"]
        assert a["operator_action_codes"] == [
            "record_human_assessment", "obtain_signed_invoice_link",
            "fresh_declared_payment_check",
        ]
        assert b["prerequisite_refs"] == [A]
        assert b["prerequisites_with_unresolved_review"] == [A]
        assert b["prerequisite_review_pending"] is True
        assert b["operator_action_codes"] == [
            "record_human_assessment", "review_historical_provider_evidence",
        ]
        assert all("payment" not in action for action in b["operator_action_codes"])
        assert plan["cases_needing_human_review"] == 2
        assert plan["customer_outcome_verified"] is False
        assert plan["agent_execution_authorized"] is False
        assert plan["source_read_performed"] is False
        assert response.headers["cache-control"] == "no-store"
        assert all(x not in response.text for x in (
            R, "in_PRIVATE", "PRIVATE@example.invalid", "acct_PRIVATE",
            "graph_binding", "record_binding",
        ))
        partial = _triage(client, headers, saved, max_cases=1)
        assert partial.status_code == 200, partial.text
        p = partial.json()["review_action_plan"]
        assert p["unscanned_prerequisite_count"] == 1
        assert p["cases"][0]["prerequisite_review_pending"] is True
        assert p["cases"][0]["operator_action_codes"][0] == "expand_dependency_review"


def test_paid_historical_and_human_disagreement_remain_unverified(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        assess = client.post(BASE + "/human-outcomes/assess", headers=headers, json={
            **_cas(saved, A), "confirm_human_assessment": True,
            "human_status": "met", "reason_code": "owner_review",
        })
        assert assess.status_code == 200, assess.text
        before = _triage(client, headers, saved).json()["review_action_plan"]["cases"][0]
        assert before["human_provider_disagreement_requires_review"] is True
        monkeypatch.setattr(routes, "stripe_open_work_case_context", _paid_scan)
        link = client.post(BASE + "/operation-link", headers=headers, json={
            **_cas(saved, A), "confirm_source_scan": True,
            "max_pages": 2, "resource_ref": R,
        })
        assert link.status_code == 200, link.text
        monkeypatch.setattr(routes, "source_operation_receipt", _paid_receipt)
        checked = client.post(BASE + "/source-goals/verify-payment", headers=headers, json={
            **_cas(saved, A), "confirm_source_verification": True,
            "record_link": link.json()["record_link"],
        })
        assert checked.status_code == 200, checked.text
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("historical plan must not reread source"),
        )
        after = _triage(client, headers, saved).json()["review_action_plan"]["cases"][0]
        assert after["human_provider_disagreement_requires_review"] is False
        assert after["review_state"] == "historical_evidence_present"
        assert after["operator_action_codes"] == ["fresh_declared_payment_check"]
        assert after["current_source_state_verified"] is False
        assert after["customer_outcome_verified"] is False


def test_action_plan_requires_session_and_current_source(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        payload = {**_cas(saved), "confirm_historical_review": True, "max_cases": 2}
        assert client.post(BASE + "/human-outcomes/evidence-triage", json=payload).status_code == 401  # no credentials
        local["id"] = None
        assert client.post(BASE + "/human-outcomes/evidence-triage",
                           headers=headers, json=payload).status_code == 403


def test_composer_refuses_conflicting_edges_and_unsafe_trust_flags():
    ref = "owg:w:" + "a" * 32
    sample = {
        "format": "openworkgraph.local-reviewed-outcome-evidence-triage.v1",
        "work_item_ref": ref, "included_case_count": 1,
        "unscanned_prerequisite_count": 0, "selected_dependency_edges": [],
        "customer_outcome_verified": False, "source_read_performed": False,
        "agent_execution_authorized": False,
        "case_reviews": [{
            "work_item_ref": ref, "role": "root",
            "owner_declared_expected_outcome_code": "human_review_recorded",
            "owner_declaration_revision": 1,
            "latest_human_assessed_status": "unknown",
            "historical_last_provider_state": "unknown",
            "human_assessment_count": 0, "provider_check_count": 0,
            "disputed_check_count": 0,
        }],
    }
    assert compose_outcome_review_action_plan(sample)["cases"][0][
        "operator_action_codes"] == ["record_human_assessment"]
    sample["customer_outcome_verified"] = True
    with pytest.raises(CaseDeclarationError):
        compose_outcome_review_action_plan(sample)
    sample["customer_outcome_verified"] = False
    sample["selected_dependency_edges"] = [{
        "prerequisite_ref": ref, "dependent_ref": ref,
        "edge_type": "owner_declared_dependency", "source_verified": False,
    }]
    with pytest.raises(CaseDeclarationError):
        compose_outcome_review_action_plan(sample)


def test_transitive_review_blocker_and_no_false_human_only_provider_duty():
    refs = ["owg:w:" + ch * 32 for ch in "abc"]
    rows = []
    for i, ref in enumerate(refs):
        rows.append({
            "work_item_ref": ref, "role": "root" if i == 2 else "prerequisite",
            "owner_declared_expected_outcome_code": "human_review_recorded",
            "owner_declaration_revision": 1,
            "latest_human_assessed_status": "unknown" if i == 0 else "met",
            "historical_last_provider_state": "unknown",
            "human_assessment_count": 0 if i == 0 else 1,
            "provider_check_count": 0, "disputed_check_count": 0,
        })
    triage = {
        "format": "openworkgraph.local-reviewed-outcome-evidence-triage.v1",
        "work_item_ref": refs[-1], "included_case_count": 3,
        "unscanned_prerequisite_count": 0,
        "selected_dependency_edges": [
            {"prerequisite_ref": refs[0], "dependent_ref": refs[1],
             "edge_type": "owner_declared_dependency", "source_verified": False},
            {"prerequisite_ref": refs[1], "dependent_ref": refs[2],
             "edge_type": "owner_declared_dependency", "source_verified": False},
        ],
        "customer_outcome_verified": False, "source_read_performed": False,
        "agent_execution_authorized": False, "case_reviews": rows,
    }
    plan = compose_outcome_review_action_plan(triage)
    assert plan["cases"][0]["operator_action_codes"] == ["record_human_assessment"]
    assert plan["cases"][1]["operator_action_codes"] == []
    assert plan["cases"][1]["prerequisites_with_unresolved_review"] == [refs[0]]
    assert plan["cases"][2]["prerequisites_with_unresolved_review"] == [refs[1]]
    assert plan["cases"][2]["prerequisite_review_pending"] is True
    assert plan["cases"][2]["review_state"] == "historical_evidence_present"
    assert plan["cases_needing_human_review"] == 1
    assert plan["operator_action_count"] == 1
