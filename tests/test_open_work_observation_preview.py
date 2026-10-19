"""Additive OWG observation states are evidence-backed, not claims of neglected work."""
from __future__ import annotations
import json

import pytest

from server import open_work_observation as observation
from server import open_work_preview as preview
from server import local_reference_lookup as refs
from server import db

REF = "owg:r:" + "b" * 32
OTHER = "owg:r:" + "c" * 32
INV = "in_1234567890ABCDEF"
ACCOUNT = "acct_1234567890ABCDEF"


def _registered(ref=REF, invoice=INV, kind="invoice"):
    return {
        "resources": {
            ref: {"provider": "stripe", "resource_kind": kind,
                  "host": "dashboard.stripe.com", "resolver_locator": "invoice:" + invoice},
        },
    }


def test_one_database_query_for_all_exact_source_references(monkeypatch):
    monkeypatch.setattr(refs, "_read", lambda: _registered())
    calls = []
    def fake_rows(sql, params):
        calls.append((sql, params))
        assert "json_valid(metadata_json)" in sql
        assert "GROUP BY" in sql
        return [{"ref": REF, "provider": "stripe", "kind": "invoice",
                 "host": "dashboard.stripe.com", "observations": 1}]
    monkeypatch.setattr(db, "rows", fake_rows)
    states = observation.stripe_invoice_observation_states([INV, "in_1234567890ABCDE0"])
    assert states[INV] == "observed"
    assert states["in_1234567890ABCDE0"] == "not_in_retained_owg"
    assert len(calls) == 1
    assert calls[0][1] == (REF,)


def test_conflicting_source_identity_downgrades_to_uncertain(monkeypatch):
    monkeypatch.setattr(refs, "_read", lambda: _registered())
    monkeypatch.setattr(db, "rows", lambda *_: [
        {"ref": REF, "provider": "stripe", "kind": "invoice", "host": "dashboard.stripe.com"},
        {"ref": REF, "provider": "stripe", "kind": "refund", "host": "dashboard.stripe.com"},
    ])
    states = observation.stripe_invoice_observation_states([INV])
    assert states[INV] == "uncertain"


def test_unretained_stale_resolver_is_not_observed(monkeypatch):
    monkeypatch.setattr(refs, "_read", lambda: _registered())
    monkeypatch.setattr(db, "rows", lambda *_: [])
    assert observation.stripe_invoice_observation_states([INV])[INV] == "not_in_retained_owg"


def test_invalid_or_inaccessible_history_returns_uncertain(monkeypatch):
    monkeypatch.setattr(refs, "_read", lambda: _registered())
    monkeypatch.setattr(db, "rows", lambda *_: (_ for _ in ()).throw(__import__("sqlite3").OperationalError("unavailable")))
    assert observation.stripe_invoice_observation_states([INV])[INV] == "uncertain"


def test_stripe_scan_calls_observation_reader_once_for_many_items():
    seen = []
    invoice_ids = ["in_" + str(i).zfill(16) for i in range(100)]
    def account_reader(url, key):
        return {"object": "account", "id": ACCOUNT}
    def list_reader(url, key):
        return {"object": "list", "data": [
            {"id": iid, "object": "invoice", "status": "open", "paid": False,
             "due_date": None} for iid in invoice_ids
        ], "has_more": False}
    def observation_reader(ids):
        seen.append(ids)
        return {iid: "observed" if iid == invoice_ids[0] else
                "uncertain" if iid == invoice_ids[1] else "not_in_retained_owg"
                for iid in ids}
    scan = preview.stripe_open_work_preview(
        explicit_scan_authorized=True,
        environment={"OWG_STRIPE_SOURCE_READ_ENABLED": "true",
                     "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
                     "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_demo1234567890"},
        reference_key=b"non-exportable-local-secret-1234567890",
        account_reader=account_reader, list_reader=list_reader,
        observation_reader=observation_reader, include_local_observation=True,
    )
    assert scan["availability"] == "source_reported"
    assert scan["returned"] == 100
    assert scan["local_observation_checked"] is True
    assert len(seen) == 1 and seen[0] == invoice_ids
    assert scan["candidates"][0]["historically_observed_in_owg"] == "observed"
    assert scan["candidates"][1]["historically_observed_in_owg"] == "uncertain"
    assert scan["candidates"][2]["historically_observed_in_owg"] == "not_in_retained_owg"
    assert scan["absence_of_observation_proves_no_work"] is False
    assert not any(iid in str(scan) for iid in invoice_ids)


def test_default_preview_does_not_query_observed_history():
    def forbidden(*_args):
        pytest.fail("No history should be read without request")
    r = preview.stripe_open_work_preview(
        explicit_scan_authorized=True,
        environment={"OWG_STRIPE_SOURCE_READ_ENABLED": "true",
                     "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
                     "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_demo1234567890"},
        account_reader=lambda *_: {"object": "account", "id": ACCOUNT},
        list_reader=lambda *_: {"object": "list", "data": [], "has_more": False},
        observation_reader=forbidden,
        reference_key=b"non-exportable-local-secret-1234567890",
    )
    assert r["local_observation_checked"] is False


def test_actual_sqlite_exact_reference_and_conflict_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "evidence.sqlite")
    monkeypatch.setattr(refs, "_read", lambda: _registered())
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT, observed_at TEXT, session_id TEXT, "
            "actor_id TEXT, event_type TEXT, source TEXT, metadata_json TEXT)"
        )
        entries = [
            ("genuine", json.dumps({"resource_reference": {
                "resource_ref": REF, "provider": "stripe",
                "resource_kind": "invoice", "host": "dashboard.stripe.com"}})),
            ("unrelated", json.dumps({"resource_reference": {
                "resource_ref": OTHER, "provider": "stripe",
                "resource_kind": "invoice", "host": "dashboard.stripe.com"}})),
            ("text-only", json.dumps({"description": "Mention of " + REF})),
            ("malformed", "{invalid json containing " + REF),
        ]
        conn.executemany(
            "INSERT INTO events(event_id, observed_at, session_id, actor_id, "
            "event_type, source, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(id, "2026-10-19T10:00:00Z", "s1", "a1", "browser_click",
              "browser_extension", metadata) for id, metadata in entries],
        )
    assert observation.stripe_invoice_observation_states([INV])[INV] == "observed"
    with db.connect() as conn:
        conn.execute("UPDATE events SET metadata_json=? WHERE event_id='genuine'",
                     (json.dumps({"resource_reference": {
                         "resource_ref": REF, "provider": "stripe",
                         "resource_kind": "refund", "host": "dashboard.stripe.com"}}),))
    assert observation.stripe_invoice_observation_states([INV])[INV] == "uncertain"
