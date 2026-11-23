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
        expected_event_id="evt_1", read_events=reader, now=NOW,\n        recheck_authorization=kwargs.pop("recheck_authorization", lambda: True), **kwargs)


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


def test_invalid_verifier_inputs_do_not_call_provider():
    calls = []
    def reader():
        calls.append(True)
        return payload()
    for age in (True, False, 0, -1, 3601, 1.5, "300"):
        result = check(reader, max_age_seconds=age)
        assert result["reason"] == "invalid_freshness"
        assert result["customer_outcome"] == "unknown"
    for clock in (False, 0, "2026-11-06T11:00:00Z",
                  datetime(2026, 11, 6, 11, 0)):
        result = verify_provider_attempt(account="acct_1", object_id="inv_1",
            operation="invoice.update", correlation_id="attempt_1",
            expected_event_id="evt_1", read_events=reader, now=clock)
        assert result["reason"] == "invalid_clock"
    assert calls == []
    assert check(None)["reason"] == "invalid_provider_reader"


def test_valid_explicit_utc_clock_and_age_still_verify_provider_only():
    result = check(lambda: payload(), max_age_seconds=300)
    assert result["status"] == "verified_provider_operation"
    assert result["customer_outcome"] == "unknown"


def test_future_dated_snapshot_is_not_a_verified_provider_recheck():
    # A provider or intermediary with a wrong clock must not confer
    # verification authority using a snapshot from the future.
    future = (NOW + timedelta(minutes=2)).isoformat()
    assert check(lambda: payload(checked_at=future))["reason"] == "stale_source"
    # Small ordinary clock skew remains supported.
    near = (NOW + timedelta(seconds=20)).isoformat()
    assert check(lambda: payload(checked_at=near))["status"] == "verified_provider_operation"


def test_event_time_must_be_consistent_with_independent_provider_read():
    base = payload()["events"][0]
    before = (NOW - timedelta(minutes=2)).isoformat()
    assert check(lambda: payload(events=[dict(base, occurred_at=before)]))["status"] == "verified_provider_operation"
    for timestamp in ("not-a-time", "2026-11-06T11:00:00",
                      (NOW + timedelta(seconds=1)).isoformat(),
                      (NOW + timedelta(days=1)).isoformat()):
        result = check(lambda timestamp=timestamp: payload(
            events=[dict(base, occurred_at=timestamp)]))
        assert result["reason"] == "invalid_event_time"
        assert result["customer_outcome"] == "unknown"


def test_grant_rechecked_after_provider_read():
    checks = []
    def guard():
        checks.append(True)
        return len(checks) == 1
    result = check(lambda: payload(), recheck_authorization=guard)
    assert result["reason"] == "authorization_revoked"
    assert result["customer_outcome"] == "unknown"
    assert len(checks) == 2


def test_invalid_or_unavailable_grant_blocks_provider_read():
    calls = []
    def reader():
        calls.append(True)
        return payload()
    assert verify_provider_attempt(account="acct_1", object_id="inv_1",\n        operation="invoice.update", correlation_id="attempt_1",\n        expected_event_id="evt_1", read_events=reader, now=NOW)["reason"] == "authorization_required"\n    assert check(reader, recheck_authorization=lambda: False)["reason"] == "authorization_revoked"
    def unavailable():
        raise OSError("grant store unavailable")
    assert check(reader, recheck_authorization=unavailable)["reason"] == "authorization_unavailable"
    assert check(reader, recheck_authorization=True)["reason"] == "invalid_authorization_guard"
    assert calls == []
    assert check(reader, recheck_authorization=lambda: True)["status"] == "verified_provider_operation"
    assert len(calls) == 1
