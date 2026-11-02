"""Actual local FastAPI/SQLite acceptance with a synthetic Stripe receipt reader.

These tests exercise the production REST route and private declaration ledger,
not a live Stripe account or a verified customer/business outcome.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session
from server.secure_app import app

REFS = ["owg:w:" + c * 32 for c in "abcde"]
RECORDS = ["owg:r:" + c * 32 for c in "abcde"]
BASE = "/v1/open-work/declarations"
LINK = BASE + "/operation-link"
VERIFY = BASE + "/dependency-operations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_private_test_123"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "dependency-operations-private-test-" + "x" * 40)
    for ref in REFS:
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cases(client, headers, count=3):
    saved = {}
    for index in range(count):
        ref = REFS[index]
        response = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": ref,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "depends_on_work_item_refs": [REFS[index - 1]] if index else [],
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert response.status_code == 200, response.text
        saved[ref] = response.json()
    return saved


def _scan(ref):
    index = REFS.index(ref)
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_PRIVATE",
        },
        "related_owg_evidence": [{
            "resource_ref": RECORDS[index],
            "observed_event_count": 2,
            "observed_session_count": 1,
            "observed_actor_count": 1,
            "timeline": [{"customer": "PRIVATE@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
        "candidate": {"amount": 999999, "customer": "PRIVATE"},
    }


def _paid(ref, operation):
    assert operation == "stripe_invoice_paid"
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": ref, "expected_operation": operation,
        "availability": "source_reported", "operation_verified": True,
        "provider": "stripe", "provider_record_kind": "invoice",
        "provider_reported_state": "paid",
        "verification_scope": "authenticated_source_provider_operational_state",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "read_only": True,
        "native_id": "in_PRIVATE",
    }


def _links(client, headers, saved, monkeypatch):
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **_opts: _scan(ref))
    links = []
    for ref, row in saved.items():
        result = client.post(LINK, headers=headers, json={
            "work_item_ref": ref, "expected_revision": row["revision"],
            "expected_edit_token": row["edit_token"],
            "confirm_source_scan": True, "max_pages": 2,
            "resource_ref": RECORDS[REFS.index(ref)],
        })
        assert result.status_code == 200, result.text
        links.append(result.json()["record_link"])
    return links


def _verify(client, headers, saved, links, root=None, **overrides):
    ref = root or list(saved)[-1]
    row = saved[ref]
    return client.post(VERIFY, headers=headers, json={
        "work_item_ref": ref, "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
        "confirm_source_verification": True, "record_links": links,
        **overrides,
    })


def test_full_dependency_chain_paid_in_prerequisite_order(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    reads = []
    def receipt(ref, op):
        reads.append(ref)
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", receipt)
    result = _verify(client, headers, saved, list(reversed(links)))
    assert result.status_code == 200, result.text
    body = result.json()
    assert reads == RECORDS[:3]
    assert [row["work_item_ref"] for row in body["cases"]] == REFS[:3]
    assert [row["provider_operation_state"] for row in body["cases"]] == ["paid"] * 3
    assert body["all_declared_case_provider_operations_verified"] is True
    assert body["all_declared_cases_checked"] is True
    assert body["source_checked_count"] == body["provider_paid_count"] == 3
    assert body["source_join_missing_count"] == body["provider_unknown_count"] == 0
    for field in ("expected_outcome_verified", "customer_outcome_verified",
                  "business_obligation_verified", "agent_caused_operation_verified",
                  "agent_execution_authorized", "source_write_performed"):
        assert body[field] is False
    assert result.headers["cache-control"] == "no-store"
    for private in ("in_PRIVATE", "PRIVATE@example.invalid", "999999",
                    "acct_private_test_123", "edit_token", "scope_ref", "record_link"):
        assert private not in result.text


def test_partial_links_never_count_unchecked_prerequisite_as_paid(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    reads = []
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda ref, op: (reads.append(ref), _paid(ref, op))[1])
    result = _verify(client, headers, saved, [links[2], links[0]])
    assert result.status_code == 200, result.text
    body = result.json()
    assert reads == [RECORDS[0], RECORDS[2]]
    assert [r["provider_operation_state"] for r in body["cases"]] == [
        "paid", "not_checked", "paid",
    ]
    assert body["source_join_missing_count"] == 1
    assert body["all_declared_case_provider_operations_verified"] is False


def test_no_links_is_nonreading_explicit_gap_report(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("zero links must not read source"))
    result = _verify(client, headers, saved, [])
    assert result.status_code == 200, result.text
    assert result.json()["source_read_performed"] is False
    assert result.json()["source_join_missing_count"] == 3
    assert all(r["provider_operation_state"] == "not_checked"
               for r in result.json()["cases"])


def test_provider_outage_or_contradiction_is_unknown_not_paid(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    def receipt(ref, op):
        if ref == RECORDS[1]:
            raise OSError("PRIVATE source error")
        result = _paid(ref, op)
        if ref == RECORDS[2]:
            result["customer_outcome_verified"] = True
        return result
    monkeypatch.setattr(routes, "source_operation_receipt", receipt)
    result = _verify(client, headers, saved, links)
    assert result.status_code == 200, result.text
    assert [r["provider_operation_state"] for r in result.json()["cases"]] == [
        "paid", "unknown", "unknown",
    ]
    assert result.json()["all_declared_case_provider_operations_verified"] is False
    assert result.json()["provider_unknown_count"] == 2
    assert "PRIVATE source error" not in result.text


@pytest.mark.parametrize("alter", [
    lambda p: p[0].update(seal="0" * 64),
    lambda p: p[1].update(source_binding="0" * 64),
    lambda p: p.append(deepcopy(p[0])),
    lambda p: p.append({"work_item_ref": REFS[3]}),
    lambda p: p[2].update(work_item_ref=REFS[3]),
    lambda p: p[0].update(expected_operation="github_pr_merged"),
])
def test_invalid_links_rejected_before_any_provider_io(local, monkeypatch, alter):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = deepcopy(_links(client, headers, saved, monkeypatch))
    alter(links)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("invalid links must refuse before source IO"))
    result = _verify(client, headers, saved, links)
    assert result.status_code in (409, 422), result.text


def test_review_revocation_after_first_read_aborts_remaining_reads(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    reads = []
    def receipt(ref, op):
        reads.append(ref)
        reviews.remove_review_intention(REFS[0])
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", receipt)
    result = _verify(client, headers, saved, links)
    assert result.status_code in (403, 409, 422)
    assert reads == [RECORDS[0]]
    assert "provider_paid_count" not in result.text


def test_account_switch_after_first_read_aborts_remaining_reads(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    reads = []
    def receipt(ref, op):
        reads.append(ref)
        local["id"] = "acct_changed"
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", receipt)
    result = _verify(client, headers, saved, links)
    assert result.status_code in (403, 409, 422)
    assert reads == [RECORDS[0]]


def test_stale_root_or_prerequisite_reapproval_refuses_before_io(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    links = _links(client, headers, saved, monkeypatch)
    update = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": REFS[0],
        "goal_code": "verify_operation",
        "expected_outcome_code": "operation_receipt_reviewed",
        "depends_on_work_item_refs": [],
        "expected_revision": saved[REFS[0]]["revision"],
        "expected_edit_token": saved[REFS[0]]["edit_token"],
    })
    assert update.status_code == 200, update.text
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("stale DAG must refuse before provider IO"))
    assert _verify(client, headers, saved, links).status_code in (403, 409, 422)


def test_five_case_dependency_graph_refuses_bounded_scan(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers, count=5)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("oversized DAG must refuse before provider IO"))
    result = _verify(client, headers, saved, [])
    assert result.status_code == 422, result.text


def test_explicit_confirmation_and_dashboard_session_required(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("unauthorized provider read"))
    assert _verify(client, headers, saved, [], confirm_source_verification=False).status_code == 403
    assert _verify(client, {}, saved, []).status_code == 401  # no credentials: global auth refuses first


def test_non_operation_goal_cannot_be_verified_as_invoice_paid(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers, count=1)
    changed = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": REFS[0],
        "goal_code": "assess_declared_policy",
        "expected_outcome_code": "human_review_recorded",
        "depends_on_work_item_refs": [],
        "expected_revision": saved[REFS[0]]["revision"],
        "expected_edit_token": saved[REFS[0]]["edit_token"],
    })
    assert changed.status_code == 200, changed.text
    saved[REFS[0]] = changed.json()
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("wrong goal must refuse before source IO"))
    assert _verify(client, headers, saved, []).status_code == 422
