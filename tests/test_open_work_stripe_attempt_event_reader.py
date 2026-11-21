"""Contract tests for exact, independently authenticated Stripe event reads."""
from datetime import datetime, timezone

from server.open_work_provider_attempt_verification import verify_provider_attempt
from server.open_work_stripe_attempt_event_reader import read_exact_stripe_attempt_event

ACCOUNT = "acct_12345678"
EVENT = "evt_12345678"
OBJECT = "in_1234567890"
CORRELATION = "owg-run-123"
OPERATION = "invoice.finalized"
CREDS = {"OWG_STRIPE_SOURCE_READ_ENABLED": "true",
         "OWG_STRIPE_ACCOUNT_ID": ACCOUNT,
         "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_dummy"}


def _reader(path, key):
    assert key == CREDS["OWG_STRIPE_RESTRICTED_READ_KEY"]
    if path == "/v1/account":
        return {"object": "account", "id": ACCOUNT}
    assert path == "/v1/events/" + EVENT
    return {"object": "event", "id": EVENT, "type": OPERATION,
            "created": 1700000000, "request": {"idempotency_key": CORRELATION},
            "data": {"object": {"id": OBJECT}}}


def _read(**overrides):
    args = dict(account=ACCOUNT, object_id=OBJECT, operation=OPERATION,
                correlation_id=CORRELATION, expected_event_id=EVENT,
                credentials=lambda: CREDS, reader=_reader)
    args.update(overrides)
    return read_exact_stripe_attempt_event(**args)


def test_exact_provider_event_is_only_observed_not_success():
    snapshot = _read()
    assert snapshot["authenticated"] is True
    assert snapshot["complete"] is True
    assert snapshot["events"][0]["status"] == "observed"
    verdict = verify_provider_attempt(
        account=ACCOUNT, object_id=OBJECT, operation=OPERATION,
        correlation_id=CORRELATION, expected_event_id=EVENT,
        read_events=lambda: snapshot)
    assert verdict["status"] == "verified_provider_event"
    assert verdict["provider_operation"] == "unknown"
    assert verdict["customer_outcome"] == "unknown"


def test_missing_consent_or_account_mismatch_never_reads_event():
    def forbidden(*args):
        raise AssertionError("must not read provider")
    assert _read(credentials=lambda: None, reader=forbidden)["complete"] is False
    assert _read(credentials=lambda: {**CREDS, "OWG_STRIPE_ACCOUNT_ID": "acct_other123"},
                 reader=forbidden)["complete"] is False


def test_wrong_event_correlation_and_object_fail_closed():
    for changed in ({"request": {"idempotency_key": "other"}},
                    {"data": {"object": {"id": "in_wrong"}}},
                    {"type": "invoice.paid"}, {"id": "evt_wrong"}):
        def reader(path, key):
            value = _reader(path, key)
            return {**value, **changed} if path.startswith("/v1/events/") else value
        assert _read(reader=reader)["complete"] is False


def test_account_mismatch_and_provider_failure_fail_closed():
    assert _read(reader=lambda path, key: {"object": "account", "id": "acct_wrong"})["complete"] is False
    def outage(path, key):
        raise OSError("provider outage")
    assert _read(reader=outage)["complete"] is False


def test_reject_untrusted_identity_without_network():
    def forbidden(*args):
        raise AssertionError("unexpected I/O")
    assert _read(expected_event_id="../../secret", reader=forbidden)["complete"] is False
    assert _read(operation="invoice.finalized?foo", reader=forbidden)["complete"] is False
