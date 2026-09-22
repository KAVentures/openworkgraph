from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_enterprise_controls_extend_the_established_secure_app():
    start = _read("start.py")
    runner = _read("server/enterprise_runner.py")
    enterprise = _read("server/enterprise_app.py")

    assert 'SECURE_API_APP = "server.secure_app:app"' in start
    assert '"-m", "server.enterprise_runner"' in start
    assert 'SECURE_APP = "server.secure_app:app"' in runner
    assert "import server.enterprise_app" in runner
    assert "uvicorn.run(SECURE_APP" in runner
    assert "from .secure_app import app" in enterprise


def test_gateway_dashboard_routes_inherit_local_capability_auth(monkeypatch):
    # Demo mode prevents any optional Gateway worker from starting while this
    # test exercises the shared local capability guard.
    monkeypatch.setenv("WORKFLOW_OBSERVER_MODE", "demo")
    from server.enterprise_app import app

    with TestClient(app) as client:
        response = client.get("/v1/gateway-status")
        assert response.status_code == 401
        assert response.json()["detail"] == "authentication required"

        response2 = client.post(
            "/v1/gateway-sharing",
            json={"enabled": True},
        )
        assert response2.status_code == 401
        assert response2.json()["detail"] == "authentication required"
