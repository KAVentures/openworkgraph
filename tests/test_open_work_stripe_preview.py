"""Open Work pilot: unobserved source candidates, no new capture/authority."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit
import inspect

import pytest

from server.open_work_preview import stripe_open_work_preview, _read_invoice_list

ACCOUNT = "acct_1234567890ABCDEF"
KEY = "rk_test_synthetic_read_key_1234567"
ENV = {
    "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
    "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
    "OWG_STRIPE_RESTRICTED_READ_KEY": KEY,
}
NOW = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)


def account_reader(url, key):
    assert url == "https://api.stripe.com/v1/account"
    assert key == KEY
    return {"object": "account", "id": ACCOUNT,
            "email": "VERY_PRIVATE_OWNER@example.test"}


def invoice(i, due=1_799_999_900):
    return {
        "object": "invoice", "id": "in_" + str(i).zfill(16),
        "status": "open", "paid": False, "due_date": due,
        "customer_email": "VERY_PRIVATE_CUSTOMER@example.test",
        "amount_due": 987654,
    }


def test_requires_separate_explicit_scan_and_existing_source_consent():
    calls = []
    def reader(url, key):
        calls.append(url)
        return {"object": "account", "id": ACCOUNT}
    denied = stripe_open_work_preview(
        environment=ENV, account_reader=reader, list_reader=reader,
        now=NOW,
    )
    assert denied["reason"] == "explicit_inventory_scan_not_authorized"
    assert calls == []
    denied = stripe_open_work_preview(
        explicit_scan_authorized=True,
        environment={**ENV, "OWG_STRIPE_SOURCE_READ_ENABLED": "false"},
        account_reader=reader, list_reader=reader, now=NOW,
    )
    assert denied["reason"] == "stripe_source_read_opt_in_off"
    assert calls == []


def test_source_backed_candidates_without_any_observed_owg_work():
    calls = []
    def list_reader(url, key):
        calls.append(url)
        return {"object": "list", "data": [invoice(1), invoice(2, None)],
                "has_more": False}
    result = stripe_open_work_preview(
        environment=ENV, explicit_scan_authorized=True,
        account_reader=account_reader, list_reader=list_reader, now=NOW,
    )
    assert calls == ["https://api.stripe.com/v1/invoices?status=open&limit=100"]
    assert result["availability"] == "source_reported"
    assert result["source_coverage_complete"] is True
    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["past_due_date"] is True
    assert result["candidates"][1]["past_due_date"] is False
    assert result["candidates"][0]["historically_observed_in_owg"] == "not_checked"
    assert result["not_a_verified_business_obligation"] is True
    assert result["read_only"] is True and result["persisted"] is False
    assert result["automatic_actions"] is False
    for private in ("in_0000000000000001", ACCOUNT, KEY,
                    "VERY_PRIVATE", "987654"):
        assert private not in str(result)
    assert result["candidates"][0]["work_item_ref"].startswith("owg:w:")


def test_partial_pagination_does_not_claim_inventory_complete():
    urls = []
    def reader(url, _key):
        urls.append(url)
        if len(urls) == 1:
            return {"object": "list", "data": [invoice(1)], "has_more": True}
        return {"object": "list", "data": [invoice(2)], "has_more": False}
    limited = stripe_open_work_preview(
        environment=ENV, explicit_scan_authorized=True, now=NOW,
        account_reader=account_reader, list_reader=reader, max_pages=1,
    )
    assert limited["source_coverage_complete"] is False
    assert limited["source_limit_reached"] is True
    urls.clear()
    complete = stripe_open_work_preview(
        environment=ENV, explicit_scan_authorized=True, now=NOW,
        account_reader=account_reader, list_reader=reader, max_pages=2,
    )
    assert complete["source_coverage_complete"] is True
    assert len(complete["candidates"]) == 2
    assert urls[1].endswith("starting_after=in_0000000000000001")


def test_wrong_account_aborts_before_inventory():
    calls = []
    result = stripe_open_work_preview(
        environment=ENV, explicit_scan_authorized=True,
        account_reader=lambda url, key: {"object": "account", "id": "acct_other123456"},
        list_reader=lambda url, key: calls.append(url),
        now=NOW,
    )
    assert result["reason"] == "stripe_account_identity_mismatch"
    assert calls == []


@pytest.mark.parametrize("broken", [
    {"object": "list", "data": [invoice(1) | {"status": "paid"}], "has_more": False},
    {"object": "list", "data": [invoice(1), invoice(1)], "has_more": False},
    {"object": "list", "data": [invoice(1)], "has_more": "no"},
    {"object": "list", "data": [invoice(1) | {"id": "bad;evil"}], "has_more": False},
])
def test_untrusted_inconsistent_provider_results_fail_closed(broken):
    result = stripe_open_work_preview(
        environment=ENV, explicit_scan_authorized=True, now=NOW,
        account_reader=account_reader, list_reader=lambda *_: broken,
    )
    assert result["availability"] == "unknown"
    assert result["candidates"] == []


def test_source_read_only_allowlist_rejects_unapproved_resources():
    for url in (
        "https://evil.example/v1/invoices?status=open&limit=100",
        "https://api.stripe.com/v1/refunds?status=open&limit=100",
        "https://api.stripe.com/v1/invoices?status=paid&limit=100",
        "https://api.stripe.com/v1/invoices?status=open&limit=100&starting_after=../hack",
        "https://api.stripe.com/v1/invoices?status=open&limit=100&status=paid",
        "https://api.stripe.com/v1/invoices?status=open&limit=100&expand[]=customer",
    ):
        with pytest.raises(ValueError):
            _read_invoice_list(url, KEY)


def test_existing_workflow_mapping_not_imported_or_replaced():
    # Dependency isolation: inventory discovery does not call task grouping,
    # procedural memory, workflow candidate ranking or canonical event writes.
    source = inspect.getsource(stripe_open_work_preview)
    for name in ("insert_events(", "record_graph(", "find_repeated_workflows(",
                 "get_workflow_evidence(", "send_email(", "submit_payment("):
        assert name not in source


def test_dashboard_route_requires_existing_session_and_confirmed_scan(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = """
import server.secure_app
from fastapi.testclient import TestClient
from server.secure_app import app
client = TestClient(app)
r = client.get('/v1/open-work/stripe-preview', params={'confirm_scan': 'true'})
assert r.status_code in (401, 403), r.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env, text=True, capture_output=True,
        timeout=75 if os.name == "nt" else 35,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
