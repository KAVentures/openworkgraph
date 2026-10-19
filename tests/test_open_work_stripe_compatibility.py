"""Pinned Stripe read schema and stable opaque IDs across credential rotation."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from server import open_work_preview as preview
from server import stripe_source_evidence as source

ACCOUNT = "acct_1234567890ABCDEF"
INVOICE = "in_1234567890ABCDEF"
SECRET = b"stable-local-owg-key-32-bytes-minimum-123456"
NOW = datetime.fromtimestamp(1800000000, tz=timezone.utc)


def _account(url, key):
    assert url == "https://api.stripe.com/v1/account"
    return {"object": "account", "id": ACCOUNT}


def _invoice_list(url, key):
    return {"object": "list", "data": [
        {"id": INVOICE, "object": "invoice", "status": "open",
         "paid": False, "due_date": 1799999999},
    ], "has_more": False}


def _scan(key: str, reference_key=SECRET):
    return preview.stripe_open_work_preview(
        explicit_scan_authorized=True,
        environment={
            "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
            "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
            "OWG_STRIPE_RESTRICTED_READ_KEY": key,
        },
        account_reader=_account,
        list_reader=_invoice_list,
        reference_key=reference_key,
        now=NOW,
    )


def test_rotation_of_stripe_restricted_key_preserves_opaque_work_item_id():
    before = _scan("rk_test_first1234567890")
    after = _scan("rk_test_rotated1234567890")
    assert before["availability"] == after["availability"] == "source_reported"
    first = before["candidates"][0]["work_item_ref"]
    assert first == after["candidates"][0]["work_item_ref"]
    assert first.startswith("owg:w:")
    assert INVOICE not in str(before)
    assert ACCOUNT not in str(after)
    assert "rk_test" not in str(after)


def test_different_installation_secret_is_not_linkable():
    before = _scan("rk_test_first1234567890")
    next_install = _scan("rk_test_first1234567890",
                         reference_key=b"another-private-owg-secret-key-987654321")
    assert before["candidates"][0]["work_item_ref"] != next_install["candidates"][0]["work_item_ref"]


def test_fail_closed_when_installation_secret_invalid():
    result = _scan("rk_test_first1234567890", reference_key=b"short")
    assert result["availability"] == "unknown"
    assert result["candidates"] == []


def test_current_pinned_stripe_schema_exposes_boolean_paid_attribute():
    assert source.STRIPE_READ_API_VERSION == "2024-10-28.acacia"
    assert preview.STRIPE_READ_API_VERSION == source.STRIPE_READ_API_VERSION


def test_invoice_inventory_https_request_sends_exact_pinned_version(monkeypatch):
    class Reply:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self):
            yield json.dumps(_invoice_list("", "")).encode()
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["headers"]["Stripe-Version"] == source.STRIPE_READ_API_VERSION
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url):
            assert method == "GET" and url == "https://api.stripe.com/v1/invoices?status=open&limit=100"
            return Reply()
    monkeypatch.setattr(preview.httpx, "Client", Client)
    result = preview._read_invoice_list("https://api.stripe.com/v1/invoices?status=open&limit=100", "rk_test_sample")
    assert len(result["data"]) == 1


def test_stripe_account_and_specific_record_get_pin_same_version(monkeypatch):
    responses = {
        "https://api.stripe.com/v1/account": {"object": "account", "id": ACCOUNT},
        "https://api.stripe.com/v1/invoices/" + INVOICE:
            {"object": "invoice", "id": INVOICE, "status": "open", "paid": False},
    }
    class Reply:
        status_code = 200
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self): yield json.dumps(responses[self.url]).encode()
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["headers"]["Stripe-Version"] == source.STRIPE_READ_API_VERSION
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url):
            assert method == "GET"
            return Reply(url)
    monkeypatch.setattr(source.httpx, "Client", Client)
    assert source._get("https://api.stripe.com/v1/account", "rk_test_sample")["id"] == ACCOUNT
    assert source._get("https://api.stripe.com/v1/invoices/" + INVOICE, "rk_test_sample")["paid"] is False
