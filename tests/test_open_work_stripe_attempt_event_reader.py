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
    return {"object": "event", "id": EVENT, "type": OPERATION, "livemode": False,
            "created": 1700000000, "request": {"idempotency_key": CORRELATION},
            "data": {"object": {"id": OBJECT}}}


def _read(**overrides):
    args = dict(account=ACCOUNT, object_id=OBJECT, operation=OPERATION,
                correlation_id=CORRELATION, expected_event_id=EVENT,
                credentials=lambda: CREDS, reader=_reader,
                recheck_authorization=lambda: True)
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
        read_events=lambda: snapshot, recheck_authorization=lambda: True)
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


def test_revocation_between_account_and_event_prevents_second_network_call():
    calls = []
    def reader(path, key):
        calls.append(path)
        return _reader(path, key)
    checks = iter([True, True, False])
    snapshot = _read(reader=reader, recheck_authorization=lambda: next(checks))
    assert snapshot["complete"] is False
    assert snapshot["revoked"] is True
    assert calls == ["/v1/account"]


def test_revocation_after_event_read_discards_provider_evidence():
    calls = []
    def reader(path, key):
        calls.append(path)
        return _reader(path, key)
    checks = iter([True, True, True, False])
    snapshot = _read(reader=reader, recheck_authorization=lambda: next(checks))
    assert snapshot["complete"] is False
    assert snapshot["revoked"] is True
    assert calls == ["/v1/account", "/v1/events/" + EVENT]


def test_authorization_guard_error_fails_closed():
    def denied():
        raise OSError("source disconnected")
    snapshot = _read(recheck_authorization=denied,
                     reader=lambda *args: (_ for _ in ()).throw(AssertionError("network")))
    assert snapshot["complete"] is False


def test_credential_rotation_between_provider_calls_prevents_event_get():
    calls = []
    credentials = iter([CREDS, {**CREDS, "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_test_rotated"}])
    def reader(path, key):
        calls.append(path)
        return _reader(path, key)
    snapshot = _read(credentials=lambda: next(credentials), reader=reader)
    assert snapshot["complete"] is False
    assert snapshot["revoked"] is True
    assert calls == ["/v1/account"]


def test_source_disabled_during_event_read_discards_evidence():
    calls = []
    credentials = iter([CREDS, CREDS, {**CREDS, "OWG_STRIPE_SOURCE_READ_ENABLED": "false"}])
    def reader(path, key):
        calls.append(path)
        return _reader(path, key)
    snapshot = _read(credentials=lambda: next(credentials), reader=reader)
    assert snapshot["complete"] is False
    assert snapshot["revoked"] is True
    assert calls == ["/v1/account", "/v1/events/" + EVENT]


def test_event_environment_must_match_restricted_key_mode():
    # A test-mode key must not authenticate a live event, even with exact
    # event, account, object and idempotency identifiers.
    def live_event(path, key):
        result = _reader(path, CREDS["OWG_STRIPE_RESTRICTED_READ_KEY"])
        return {**result, "livemode": True} if path.startswith("/v1/events/") else result
    assert _read(reader=live_event)["complete"] is False

    def missing_mode(path, key):
        result = _reader(path, CREDS["OWG_STRIPE_RESTRICTED_READ_KEY"])
        if path.startswith("/v1/events/"):
            result.pop("livemode")
        return result
    assert _read(reader=missing_mode)["complete"] is False

    live_creds = {**CREDS, "OWG_STRIPE_RESTRICTED_READ_KEY": "rk_live_dummy"}
    def live_reader(path, key):
        result = _reader(path, CREDS["OWG_STRIPE_RESTRICTED_READ_KEY"])
        return {**result, "livemode": True} if path.startswith("/v1/events/") else result
    assert _read(credentials=lambda: live_creds, reader=live_reader)["complete"] is True


def test_connected_account_event_cannot_cross_source_boundary():
    def connected_event(path, key):
        value = _reader(path, key)
        return {**value, "account": "acct_other123"} if path.startswith("/v1/events/") else value
    assert _read(reader=connected_event)["complete"] is False

    def same_account_event(path, key):
        value = _reader(path, key)
        return {**value, "account": ACCOUNT} if path.startswith("/v1/events/") else value
    assert _read(reader=same_account_event)["complete"] is True


def test_future_event_timestamp_is_not_historical_evidence():
    future = int(datetime.now(timezone.utc).timestamp()) + 3600
    def future_event(path, key):
        value = _reader(path, key)
        return {**value, "created": future} if path.startswith("/v1/events/") else value
    assert _read(reader=future_event)["complete"] is False


def test_malformed_provider_json_shapes_fail_closed_without_event_disclosure():
    """A valid HTTP 200 can still contain a non-object JSON response."""
    for malformed in (None, [], "unexpected", 42):
        calls = []
        def bad_account(path, key):
            calls.append(path)
            return malformed
        snapshot = _read(reader=bad_account)
        assert snapshot["complete"] is False
        assert calls == ["/v1/account"]

        def bad_event(path, key):
            calls.append(path)
            return malformed if path.startswith("/v1/events/") else _reader(path, key)
        calls.clear()
        snapshot = _read(reader=bad_event)
        assert snapshot["complete"] is False
        assert snapshot["authenticated"] is False
        assert calls == ["/v1/account", "/v1/events/" + EVENT]


def test_direct_adapter_requires_explicit_authorization_guard_before_network():
    calls = []
    def forbidden(path, key):
        calls.append(path)
        raise AssertionError("provider read without grant")
    for missing in (None, False, "allow"):
        result = _read(reader=forbidden, recheck_authorization=missing)
        assert result == {"complete": False, "authenticated": False, "revoked": True}
    assert calls == []


def test_revoked_grant_prevents_even_local_credential_access():
    calls = []
    def forbidden_credentials():
        calls.append("credentials")
        raise AssertionError("revoked actor must not access provider credentials")
    def forbidden_provider(*args):
        calls.append("provider")
        raise AssertionError("revoked actor must not read provider")
    for guard in (lambda: False, lambda: None):
        result = _read(credentials=forbidden_credentials,
                       reader=forbidden_provider, recheck_authorization=guard)
        assert result == {"complete": False, "authenticated": False, "revoked": True}
    assert calls == []


def test_authorization_guard_exception_prevents_credential_access():
    calls = []
    def broken_guard():
        raise OSError("authorization service unavailable")
    def forbidden_credentials():
        calls.append("credentials")
        return CREDS
    result = _read(credentials=forbidden_credentials,
                   recheck_authorization=broken_guard)
    assert result["complete"] is False
    assert calls == []
