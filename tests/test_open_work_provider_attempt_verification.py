from datetime import datetime, timezone, timedelta

from server.open_work_provider_attempt_verification import verify_provider_attempt

NOW = datetime(2026, 11, 6, 11, 0, tzinfo=timezone.utc)


def payload(**overrides):
    data = dict(authenticated=True, complete=True, account="acct_1",
                object_id="inv_1", checked_at=NOW.isoformat(),
                events=[dict(event_id="evt_1", correlation_id="attempt_1",
                             operation="invoice.update", object_id="inv_1",
                             status="succeeded")])
    data.update(overrides)
    return data


def check(reader, **kwargs):
    return verify_provider_attempt(account="acct_1", object_id="inv_1",
        operation="invoice.update", correlation_id="attempt_1",
        expected_event_id="evt_1", read_events=reader, now=NOW, **kwargs)


def test_exact_authenticated_provider_operation_is_not_customer_success():
    result = check(lambda: payload())
    assert result["status"] == "verified_provider_operation"
    assert result["provider_operation"] == "succeeded"
    assert result["customer_outcome"] == "unknown"


def test_missing_and_conflicting_events_fail_closed():
    assert check(lambda: payload(events=[]))["status"] == "unknown"
    assert check(lambda: payload(events=payload()["events"] * 2))["reason"] == "missing_or_conflicting_event"
    bad = dict(payload()["events"][0], correlation_id="another_attempt")
    assert check(lambda: payload(events=[bad]))["reason"] == "identity_conflict"


def test_scope_and_freshness_and_authorization_fail_closed():
    assert check(lambda: payload(account="acct_2"))["reason"] == "source_scope_mismatch"
    assert check(lambda: payload(authenticated=False))["reason"] == "incomplete_or_unauthenticated"
    assert check(lambda: payload(complete=False))["reason"] == "incomplete_or_unauthenticated"
    assert check(lambda: payload(revoked=True))["reason"] == "source_revoked_or_invalid"
    old = (NOW - timedelta(minutes=20)).isoformat()
    assert check(lambda: payload(checked_at=old))["reason"] == "stale_source"


def test_provider_failure_and_unknown_status():
    assert check(lambda: (_ for _ in ()).throw(OSError("offline")))["reason"] == "provider_unavailable"
    assert check(lambda: payload(events=[dict(payload()["events"][0], status="pending")]))["reason"] == "unconfirmed_provider_status"
    assert check(lambda: payload(events=[dict(payload()["events"][0], status="failed")]))["provider_operation"] == "failed"
