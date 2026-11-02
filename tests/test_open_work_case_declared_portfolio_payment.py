"""FastAPI/private-SQLite acceptance for selected independent portfolio checks.

Synthetic Stripe-shaped receipts exercise the actual route and durable ledger.
They do not establish live provider acceptance or real customer outcomes.
"""
from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_operation_ledger as ledger
from server.local_auth import create_dashboard_session
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
RA = "owg:r:" + "a" * 32
RB = "owg:r:" + "b" * 32
BASE = "/v1/open-work/declarations"
PORTFOLIO = BASE + "/source-goals/verify-portfolio-payments"
SINGLE = BASE + "/source-goals/verify-payment"
HISTORY = BASE + "/source-goals/payment-history"


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
        lambda _name: "selected-portfolio-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cas(row):
    return {
        "work_item_ref": row["work_item_ref"],
        "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
    }


def _declare(client, headers, ref, dependencies=(), outcome="stripe_invoice_paid_reported"):
    response = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": ref,
        "goal_code": "verify_operation",
        "expected_outcome_code": outcome,
        "depends_on_work_item_refs": list(dependencies),
        "expected_revision": 0, "expected_edit_token": None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _scan(ref, **_options):
    resource = RA if ref == A else RB
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
            "resource_ref": resource, "observed_event_count": 2,
            "observed_session_count": 1, "observed_actor_count": 1,
            "timeline": [{"private_customer": "PRIVATE@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
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


def _setup(client, headers, monkeypatch, *, dependent=False):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    b = _declare(client, headers, B)
    a = _declare(client, headers, A, (B,) if dependent else ())
    selections = []
    for row, resource in ((a, RA), (b, RB)):
        linked = client.post(BASE + "/operation-link", headers=headers, json={
            **_cas(row), "confirm_source_scan": True,
            "max_pages": 2, "resource_ref": resource,
        })
        assert linked.status_code == 200, linked.text
        selections.append({**_cas(row), "record_link": linked.json()["record_link"]})
    return a, b, selections


def _verify(client, headers, selections, *, confirm=True):
    return client.post(PORTFOLIO, headers=headers, json={
        "confirm_source_verification": confirm,
        "selections": selections,
    })


def test_independent_portfolio_checks_commit_together_with_truthful_unknown(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, selections = _setup(client, headers, monkeypatch)
        reads = []
        def source(ref, op):
            reads.append(ref)
            return _paid(ref, op) if ref == RB else None
        monkeypatch.setattr(routes, "source_operation_receipt", source)
        response = _verify(client, headers, selections)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["format"] == "openworkgraph.local-case-declared-portfolio-payment-check.v1"
        assert reads == [RA, RB]
        assert [r["work_item_ref"] for r in data["cases"]] == [A, B]
        assert [r["provider_goal_state_at_check"] for r in data["cases"]] == ["unknown", "met"]
        assert [r["provider_state_transition"] for r in data["cases"]] == ["first_check", "first_check"]
        assert data["fresh_provider_paid_count"] == 1
        assert data["fresh_provider_unknown_count"] == 1
        assert data["local_evidence_appends_are_one_atomic_transaction"] is True
        assert data["provider_reads_are_sequential_not_atomic_snapshot"] is True
        assert data["customer_outcome_verified"] is False
        assert data["agent_execution_authorized"] is False
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
            stored = str(db.execute("SELECT * FROM evidence").fetchall())
        for secret in (RA, RB, "in_PRIVATE", "acct_PRIVATE", "PRIVATE@example.invalid"):
            assert secret not in response.text
            assert secret not in stored
        assert response.headers["cache-control"] == "no-store"
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("history must not contact Stripe"))
        for row in (a, b):
            history = client.post(HISTORY, headers=headers, json=_cas(row))
            assert history.status_code == 200, history.text
            assert history.json()["provider_check_count"] == 1


def test_portfolio_second_check_reports_per_case_transitions(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", lambda *_: None)
        assert _verify(client, headers, selections).status_code == 200
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        result = _verify(client, headers, selections)
        assert result.status_code == 200, result.text
        assert [r["provider_state_transition"] for r in result.json()["cases"]] == [
            "unknown_to_paid", "unknown_to_paid",
        ]
        assert result.json()["all_declared_provider_goals_met_at_individual_checks"] is True
        assert result.json()["business_objective_verified"] is False


def test_portfolio_invalid_selection_refused_before_any_source_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("invalid portfolio must not read Stripe"))
        invalid = [
            selections[:1],
            selections + selections + selections,
            [selections[0], selections[0]],
            [selections[0], dict(selections[1], record_link=selections[0]["record_link"])],
            [selections[0], dict(selections[1], record_link={
                **selections[1]["record_link"], "seal": "0" * 64,
            })],
            [selections[0], dict(selections[1], expected_revision=999)],
            [selections[0], dict(selections[1], extra_field="not_allowed")],
        ]
        for candidate in invalid:
            response = _verify(client, headers, candidate)
            # Too many selections can exceed the body limit (413) before validation.
            assert response.status_code in (403, 409, 413, 422), response.text
        assert _verify(client, headers, selections, confirm=False).status_code == 403
        # No credentials: the global auth middleware refuses before the route.
        assert client.post(PORTFOLIO, json={
            "confirm_source_verification": True, "selections": selections,
        }).status_code == 401
        assert not ledger._path().exists()


def test_dependent_selected_roots_refuse_portfolio_use_dependency_endpoint(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch, dependent=True)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("dependent roots must not be read"))
        response = _verify(client, headers, selections)
        assert response.status_code in (403, 409, 422), response.text
        assert not ledger._path().exists()


def test_revocation_after_first_read_prevents_second_and_any_ledger_append(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        reads = []
        def revoke(ref, op):
            reads.append(ref)
            reviews.remove_review_intention(B)
            return _paid(ref, op)
        monkeypatch.setattr(routes, "source_operation_receipt", revoke)
        result = _verify(client, headers, selections)
        assert result.status_code in (403, 409, 422)
        assert reads == [RA]
        assert not ledger._path().exists()


def test_prior_hmac_tampering_on_later_case_blocks_first_provider_read(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        first = client.post(SINGLE, headers=headers, json={
            **_cas(b), "confirm_source_verification": True,
            "record_link": selections[1]["record_link"],
        })
        assert first.status_code == 200, first.text
        with sqlite3.connect(ledger._path()) as db:
            db.execute("UPDATE evidence SET provider_state='unknown' WHERE work_item_ref=?", (B,))
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("tampered later case must block all Stripe reads"))
        result = _verify(client, headers, selections)
        assert result.status_code in (403, 409, 422)


def test_concurrent_dispute_aborts_all_selected_checks(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        first = client.post(SINGLE, headers=headers, json={
            **_cas(b), "confirm_source_verification": True,
            "record_link": selections[1]["record_link"],
        })
        assert first.status_code == 200, first.text
        original = routes.append_operation_evidence_batch
        def dispute_before_append(entries, **kw):
            ledger.dispute_operation_evidence(
                entries[1]["admitted"], entries[1]["signature"],
                scope=kw["scope"], secret=kw["secret"], guard=kw["guard"],
                target_seq=1, reason="operator_dispute",
            )
            return original(entries, **kw)
        monkeypatch.setattr(routes, "append_operation_evidence_batch", dispute_before_append)
        response = _verify(client, headers, selections)
        assert response.status_code == 409, response.text
        with sqlite3.connect(ledger._path()) as db:
            rows = db.execute(
                "SELECT work_item_ref, kind FROM evidence ORDER BY work_item_ref, seq"
            ).fetchall()
        assert rows == [(B, "check"), (B, "dispute")]


def test_late_second_insert_failure_rolls_back_entire_portfolio(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        original = ledger._insert
        calls = []
        def fail_second(db, row):
            calls.append(row["work_item_ref"])
            if len(calls) == 2:
                raise sqlite3.OperationalError("synthetic second insert failure")
            return original(db, row)
        monkeypatch.setattr(ledger, "_insert", fail_second)
        response = _verify(client, headers, selections)
        assert response.status_code == 409, response.text
        assert calls == [A, B]
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_account_switch_during_first_provider_read_stops_next_and_persistence(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        reads = []
        def switch(ref, op):
            reads.append(ref)
            local["id"] = "acct_OTHER"
            return _paid(ref, op)
        monkeypatch.setattr(routes, "source_operation_receipt", switch)
        response = _verify(client, headers, selections)
        assert response.status_code in (403, 409, 422)
        assert reads == [RA]
        assert not ledger._path().exists()
