"""Exercise realistic Stripe inventory sizes with an isolated fake HTTPS stream.

The preview still exposes no native invoice/customer/amount fields and never
accepts oversized source responses.
"""
from __future__ import annotations

import json

import pytest

from server import open_work_preview


class FakeStream:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        for index in range(0, len(self.body), 16_384):
            yield self.body[index:index+16_384]


class FakeClient:
    def __init__(self, body, **kwargs):
        self.body = body
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] <= 8.0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url):
        assert method == "GET"
        assert url == "https://api.stripe.com/v1/invoices?status=open&limit=100"
        return FakeStream(self.body)


def _monkeypatch_http(monkeypatch, document):
    data = json.dumps(document).encode("utf-8")
    monkeypatch.setattr(open_work_preview.httpx, "Client",
                        lambda **kwargs: FakeClient(data, **kwargs))
    return len(data)


def test_realistic_100_invoice_page_larger_than_old_256k_still_loads(monkeypatch):
    invoices = [{
        "id": "in_" + str(i).zfill(16),
        "object": "invoice", "paid": False, "status": "open",
        "due_date": 1_799_999_000,
        "line_items_and_metadata": "PRIVATE_CUSTOMER_INFORMATION" * 260,
    } for i in range(100)]
    size = _monkeypatch_http(
        monkeypatch, {"object": "list", "data": invoices, "has_more": False})
    assert 256_000 < size < open_work_preview._MAX_BODY_BYTES

    def account_reader(url, key):
        return {"object": "account", "id": "acct_1234567890ABCDEF"}

    result = open_work_preview.stripe_open_work_preview(
        explicit_scan_authorized=True,
        environment={
            "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
            "OWG_STRIPE_ACCOUNT_ID": "acct_1234567890ABCDEF",
            "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_pseudonymous_read_key_1234567",
        },
        account_reader=account_reader,
    )
    assert result["availability"] == "source_reported"
    assert len(result["candidates"]) == 100
    assert result["source_coverage_complete"] is True
    assert "PRIVATE_CUSTOMER_INFORMATION" not in str(result)
    assert "in_0000000000000001" not in str(result)


def test_oversized_provider_response_still_aborts_before_parsing(monkeypatch):
    body = {"object": "list", "data": [{"large": "x" * (open_work_preview._MAX_BODY_BYTES + 1024)}],
            "has_more": False}
    size = _monkeypatch_http(monkeypatch, body)
    assert size > open_work_preview._MAX_BODY_BYTES
    with pytest.raises(ValueError, match="oversize_invoice_inventory"):
        open_work_preview._read_invoice_list(
            "https://api.stripe.com/v1/invoices?status=open&limit=100",
            "rk_test_key",
        )
