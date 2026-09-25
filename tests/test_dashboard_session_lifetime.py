from __future__ import annotations

from server import local_auth
from server.secure_app import _bootstrap_script


def _reset_dashboard_auth_state() -> None:
    local_auth._DASHBOARD_SESSIONS.clear()
    local_auth._CONSUMED_DASHBOARD_BOOTSTRAPS.clear()


def test_dashboard_session_survives_wall_clock_advance(monkeypatch):
    _reset_dashboard_auth_state()
    bootstrap = "process-lifetime-bootstrap"
    monkeypatch.setenv("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", bootstrap)

    now = 1_800_000_000.0
    monkeypatch.setattr(local_auth.time, "time", lambda: now)
    session = local_auth.exchange_dashboard_bootstrap(bootstrap)
    assert session
    assert local_auth.dashboard_session_valid(session)

    # The dashboard capability is tied to the running server process, not a
    # 12-hour timer. Advancing wall time by a month must not invalidate it.
    monkeypatch.setattr(local_auth.time, "time", lambda: now + 31 * 24 * 60 * 60)
    assert local_auth.dashboard_session_valid(session)


def test_dashboard_session_is_revoked_when_process_memory_is_gone(monkeypatch):
    _reset_dashboard_auth_state()
    bootstrap = "process-restart-bootstrap"
    monkeypatch.setenv("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", bootstrap)

    session = local_auth.exchange_dashboard_bootstrap(bootstrap)
    assert session and local_auth.dashboard_session_valid(session)

    # A real OpenWorkGraph restart creates a fresh Python process. Clearing the
    # in-memory registry models that boundary: stale tabs must no longer work.
    local_auth._DASHBOARD_SESSIONS.clear()
    assert not local_auth.dashboard_session_valid(session)


def test_stale_dashboard_401_is_not_reported_as_observer_unreachable():
    script = _bootstrap_script()
    assert "window.__owgAuthLost = true" in script
    assert "OpenWorkGraph was restarted. Use the dashboard opened by the current launcher." in script
    assert "Could not reach the local observer. Is the launcher still running?" in script
