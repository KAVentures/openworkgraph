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
