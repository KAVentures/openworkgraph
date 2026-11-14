"""R9 analysis grants: atomic, session-bound and fail-closed primitives."""
from concurrent.futures import ThreadPoolExecutor

from server import local_auth


def test_analysis_grant_is_single_use_and_bound_to_session_log_lineage():
    session = local_auth.create_dashboard_session()
    other = local_auth.create_dashboard_session()
    grant = local_auth.issue_analysis_grant(session, "log-a", "digest-a")["ticket"]
    assert not local_auth.consume_analysis_grant(grant, other, "log-a", "digest-a")
    assert not local_auth.consume_analysis_grant(grant, session, "log-a", "digest-a")
    for wrong_log, wrong_digest in (("log-b", "digest-a"), ("log-a", "digest-b")):
        token = local_auth.issue_analysis_grant(session, "log-a", "digest-a")["ticket"]
        assert not local_auth.consume_analysis_grant(token, session, wrong_log, wrong_digest)
        assert not local_auth.consume_analysis_grant(token, session, "log-a", "digest-a")


def test_analysis_grant_concurrent_replay_only_one_succeeds():
    session = local_auth.create_dashboard_session()
    token = local_auth.issue_analysis_grant(session, "log", "digest")["ticket"]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: local_auth.consume_analysis_grant(
            token, session, "log", "digest"), range(24)))
    assert results.count(True) == 1
    assert results.count(False) == 23


def test_analysis_grant_rejects_invalid_session():
    import pytest
    with pytest.raises(ValueError):
        local_auth.issue_analysis_grant("invalid-session", "log", "digest")


def test_analysis_grant_expires_and_cannot_be_replayed(monkeypatch):
    # Time is controlled so this is deterministic on slow CI and all OSes.
    clock = [1000.0]
    monkeypatch.setattr(local_auth.time, "time", lambda: clock[0])
    session = local_auth.create_dashboard_session()
    ticket = local_auth.issue_analysis_grant(session, "log", "digest", ttl_seconds=1)["ticket"]
    clock[0] += 2.0
    assert not local_auth.consume_analysis_grant(ticket, session, "log", "digest")
    assert not local_auth.consume_analysis_grant(ticket, session, "log", "digest")


def test_analysis_grant_denies_revoked_session_and_burns_ticket():
    session = local_auth.create_dashboard_session()
    ticket = local_auth.issue_analysis_grant(session, "log", "digest")["ticket"]
    # Simulate the same process-local session revocation used by shutdown.
    with local_auth._LOCK:
        local_auth._DASHBOARD_SESSIONS.pop(session, None)
    assert not local_auth.consume_analysis_grant(ticket, session, "log", "digest")
    with local_auth._LOCK:
        local_auth._DASHBOARD_SESSIONS[session] = None
    assert not local_auth.consume_analysis_grant(ticket, session, "log", "digest")
