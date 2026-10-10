from __future__ import annotations

from fastapi.testclient import TestClient

# Routes are registered by the installed enterprise runner, not by importing
# secure_app alone. Exercise the real ASGI guard and router together.
from server.secure_app import app
import server.browser_signal_routes  # noqa: F401
from server import local_auth, work_text_capture as wt


def test_ai_bearer_cannot_enable_capture_or_master_ai_access(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(wt, "_POLICY", tmp_path / "work_text_policy.json")
    monkeypatch.setattr(wt, "_DB", tmp_path / "work_text.db")
    api_token = local_auth.ensure_api_token()
    bearer = {"Authorization": f"Bearer {api_token}"}
    dashboard_session = local_auth.create_dashboard_session()
    dashboard = {"Authorization": f"OWG-Session {dashboard_session}"}

    with TestClient(app) as client:
        # A missing AI-identification header must never become an authorization.
        for headers in (bearer, {**bearer, "X-OpenWorkGraph-Context": "ai"}):
            captured = client.post(
                "/v1/work-text/policy",
                json={"capture_enabled": True, "ai_read_enabled": True},
                headers=headers,
            )
            assert captured.status_code == 403, captured.text
            enabled = client.post("/v1/ai-access", json={"enabled": True}, headers=headers)
            assert enabled.status_code == 403, enabled.text
        assert wt.get_policy()["capture_enabled"] is False
        assert wt.get_policy()["ai_read_enabled"] is False

        # An MCP/API bearer may no longer mint a privileged dashboard session.
        assert client.post("/v1/dashboard-session/reopen", headers=bearer).status_code == 401

        anonymous = client.post("/v1/work-text/policy", json={"capture_enabled": True})
        assert anonymous.status_code == 401

        # The real, process-bound dashboard session can persist explicit consent.
        accepted = client.post(
            "/v1/work-text/policy", json={"capture_enabled": True}, headers=dashboard
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["capture_enabled"] is True
        assert accepted.json()["ai_read_enabled"] is False
        accepted = client.post(
            "/v1/work-text/policy", json={"ai_read_enabled": True}, headers=dashboard
        )
        assert accepted.status_code == 200 and accepted.json()["ai_read_enabled"] is True
        disabled = client.post(
            "/v1/work-text/policy", json={"capture_enabled": False}, headers=dashboard
        )
        assert disabled.status_code == 200
        assert wt.get_policy() == {"capture_enabled": False, "ai_read_enabled": False, "retention_days": 7}


def test_native_reopen_secret_is_independent_from_mcp_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    api = local_auth.ensure_api_token()
    mcp = local_auth.ensure_mcp_token()
    native = local_auth.ensure_dashboard_reopen_token()
    assert len({api, mcp, native}) == 3

    with TestClient(app) as client:
        for token in (api, mcp):
            assert client.post(
                "/v1/dashboard-session/reopen",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code == 401
        session = client.post(
            "/v1/dashboard-session/reopen",
            headers={"Authorization": f"Bearer {native}"},
        )
        assert session.status_code == 200 and local_auth.dashboard_session_valid(session.json()["session"])
