"""Stripe billing ground truth: account isolation, consent and unknown states."""
from server.stripe_source_evidence import stripe_record_receipt
from server.record_graph import record_graph

REF = "owg:r:" + "c" * 32
ID = "in_1234567890ABCDEF"
REFUND = "re_1234567890ABCDEF"
ACCOUNT = "acct_1234567890ABCDEF"
INFO = {"provider": "stripe", "resource_kind": "invoice",
        "host": "dashboard.stripe.com", "resolver_locator": "invoice:" + ID}
BASE = {"OWG_STRIPE_SOURCE_READ_ENABLED": "true",
        "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
        "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_synthetic_sample_only"}


def reader(state="paid", paid=True, wrong_account=False, wrong_record=False):
    urls = []
    def get(url, key):
        urls.append(url)
        assert key == BASE["OWG_STRIPE_RESTRICTED_READ_KEY"]
        if url.endswith("/v1/account"):
            return {"object": "account", "id": "acct_OTHER123456" if wrong_account else ACCOUNT,
                    "email": "VERY_PRIVATE_ACCOUNT_EMAIL"}
        if "/invoices/" in url:
            return {"id": "in_DIFFERENT123456" if wrong_record else ID,
                    "object": "invoice", "status": state, "paid": paid,
                    "customer_email": "VERY_PRIVATE_PATIENT_EMAIL", "total": 987654321}
        return {"id": REFUND, "object": "refund", "status": state,
                "description": "VERY_PRIVATE_CUSTOMER_NAME"}
    return get, urls


def test_default_off_and_missing_credential_do_not_resolve_or_read():
    calls = []
    r = stripe_record_receipt(REF, environment=BASE | {"OWG_STRIPE_SOURCE_READ_ENABLED": "false"},
                              resolver=lambda *_: calls.append("resolve"),
                              reader=lambda *_: calls.append("read"))
    assert r["availability"] == "unknown"
    assert calls == []
    r = stripe_record_receipt(REF, environment=BASE | {"OWG_STRIPE_RESTRICTED_READ_KEY": "sk_live_fake"},
                              resolver=lambda *_: calls.append("resolve"))
    assert r["availability"] == "unknown"
    assert calls == []


def test_account_is_checked_before_fetching_invoice_and_no_private_fields_returned():
    f, urls = reader()
    out = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: INFO, reader=f)
    assert out["source_state"] == "paid"
    assert out["verified_business_outcome"] is True
    assert out["verified_operational_outcome"]["kind"] == "stripe_invoice_paid"
    assert len(urls) == 2
    assert urls[0] == "https://api.stripe.com/v1/account"
    assert urls[1].endswith("/v1/invoices/" + ID)
    for forbidden in (ID, ACCOUNT, "VERY_PRIVATE", "987654321",
                      "rk_test_synthetic_sample_only"):
        assert forbidden not in str(out)
    assert out["not_a_claim_of_customer_bank_settlement"] is True


def test_wrong_account_does_not_probe_other_account_invoice():
    f, urls = reader(wrong_account=True)
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: INFO, reader=f)
    assert r["reason"] == "stripe_account_identity_mismatch"
    assert urls == ["https://api.stripe.com/v1/account"]


def test_paid_flag_disagreement_never_claims_paid():
    f, _ = reader(state="paid", paid=False)
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: INFO, reader=f)
    assert r["availability"] == "unknown"


def test_open_invoice_not_marked_paid_and_wrong_id_unknown():
    f, _ = reader(state="open", paid=False)
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: INFO, reader=f)
    assert r["verified_business_outcome"] is False
    f, _ = reader(wrong_record=True)
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: INFO, reader=f)
    assert r["reason"] == "stripe_record_identity_mismatch"


def test_succeeded_refund_is_processor_state_not_bank_settlement():
    f, _ = reader(state="succeeded")
    info = INFO | {"resource_kind": "refund", "resolver_locator": "refund:" + REFUND}
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: info, reader=f)
    assert r["verified_operational_outcome"]["kind"] == "stripe_refund_processor_succeeded"
    assert r["verified_business_outcome"] is True
    assert r["not_a_claim_of_customer_bank_settlement"] is True
    f, _ = reader(state="pending")
    r = stripe_record_receipt(REF, environment=BASE, resolver=lambda *_: info, reader=f)
    assert r["verified_business_outcome"] is False


def test_cross_record_conflict_prevents_stripe_source_probe():
    called = []
    def ev(n, kind):
        return {"event_id": str(n), "metadata": {"resource_reference": {
            "resource_ref": REF, "provider": "stripe", "resource_kind": kind,
            "host": "dashboard.stripe.com"}}, "event_type": "click"}
    g = record_graph(REF, _raw_events=[ev(1, "invoice"), ev(2, "refund")],
                     include_source_status=True,
                     source_reader=lambda ref: called.append(ref))
    assert g["record_observed"] is False
    assert called == []


def test_stripe_resolver_rejects_cross_provider_wrong_host_and_guessed_ids():
    calls = []
    for val in [
        INFO | {"host": "attacker.example"},
        INFO | {"resolver_locator": "invoice:../../other"},
        INFO | {"provider": "jira"},
    ]:
        r = stripe_record_receipt(REF, environment=BASE,
                                  resolver=lambda *_: val,
                                  reader=lambda *_: calls.append("network"))
        assert r["availability"] == "unknown"
    assert calls == []


def test_python_dashboard_reference_parsing_excludes_hosted_invoice_tokens():
    from shared.core.resource_references import resource_reference_from_url
    from unittest.mock import patch
    with patch("server.local_auth.ensure_browser_secret", return_value="a" * 60), patch(
        "server.local_auth.ensure_api_token", return_value="b" * 60,
    ):
        valid = resource_reference_from_url("https://dashboard.stripe.com/invoices/"+ID)
        assert valid["provider"] == "stripe"
        assert valid["resource_kind"] == "invoice"
        assert valid["resource_ref"].startswith("owg:r:")
        assert resource_reference_from_url("https://invoice.stripe.com/i/acct_foo/live_secret") is None
        assert resource_reference_from_url("https://dashboard.stripe.com/invoices/"+ID+"?secret=x") is None
