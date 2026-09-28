from __future__ import annotations

"""Setup checks turn delivery counts and configuration into specific next steps."""

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.setup_checks import checks

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
CONFIGURED = {"hook_events": ["SessionStart", "Stop"], "missing_hook_events": [], "telemetry_enabled": True,
              "otel_logs_endpoint_is_openworkgraph": True}


def _snapshot(**channels):
    return {"channels": {name: {"label": name, **value} for name, value in channels.items()}}


def _ids(result):
    return [c["id"] for c in result]


def test_healthy_setup_has_no_checks():
    snap = _snapshot(claude_code_hooks={"requests": 5, "last_received_at": (NOW - timedelta(minutes=1)).isoformat()},
                     claude_code_otel_logs={"requests": 3})
    assert checks(snap, {"claude_code": CONFIGURED}, now=NOW) == []


def test_recent_hooks_without_telemetry_are_reported_without_inventing_the_cause():
    snap = _snapshot(claude_code_hooks={"requests": 5, "last_received_at": (NOW - timedelta(minutes=2)).isoformat()},
                     claude_code_otel_logs={"requests": 0})
    [check] = checks(snap, {"claude_code": CONFIGURED}, now=NOW)
    assert check["id"] == "claude_telemetry_missing" and check["client"] == "claude_code"
    assert "cannot prove the cause" in check["message"] and "new Claude Code session" in check["action"]
    # Old hook traffic alone (nothing recent) is not evidence of a current delivery problem.
    stale = _snapshot(claude_code_hooks={"requests": 5, "last_received_at": (NOW - timedelta(hours=3)).isoformat()})
    assert checks(stale, {"claude_code": CONFIGURED}, now=NOW) == []


def test_exactly_current_hook_timestamp_is_recent_not_falsy():
    snap = _snapshot(claude_code_hooks={"requests": 1, "last_received_at": NOW.isoformat()},
                     claude_code_otel_logs={"requests": 0})
    assert _ids(checks(snap, {"claude_code": CONFIGURED}, now=NOW)) == ["claude_telemetry_missing"]


def test_outdated_hooks_and_missing_telemetry():
    config = {"claude_code": {**CONFIGURED, "missing_hook_events": ["Stop", "UserPromptSubmit"], "telemetry_enabled": False}}
    assert _ids(checks(_snapshot(), config, now=NOW)) == ["claude_hooks_outdated", "claude_telemetry_off"]


def test_rejections_codex_spool_and_new_features():
    snap = _snapshot(codex_otel={"requests": 2, "rejected": {"auth": 2}}, copilot_otel={"requests": 1, "rejected": {"protobuf_unsupported": 1}})
    config = {"codex": {"configured": True}, "agent_spool": {"pending_files": 3, "lease_active": False}}
    extras = {"outcome_tracking": {"enabled": True, "gh_found": True, "gh_logged_in": False},
              "agent_brief": {"frameworks": {"claude-code": {"enabled": True, "hook_installed": False}}}}
    result = checks(snap, config, extras=extras, now=NOW)
    assert _ids(result) == ["token_rejected", "otlp_protobuf", "spool_waiting", "gh_logged_out", "brief_hook_missing"]
    assert {c["client"] for c in result} == {"codex", "vscode", "claude_code", "outcomes"}
    assert _ids(checks(_snapshot(), {"codex": {"configured": True}}, now=NOW)) == ["codex_nothing_yet"]
    assert _ids(checks(_snapshot(), {}, extras={"outcome_tracking": {"enabled": True, "gh_found": False}}, now=NOW)) == ["gh_missing"]


def test_diagnostics_endpoint_includes_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OWG_CLAUDE_SETTINGS_PATH", str(tmp_path / "home" / ".claude" / "settings.json"))
    from server.agent_routes import router
    from server.local_auth import ensure_api_token

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        body = client.get("/v1/agent-telemetry/diagnostics", headers={"Authorization": f"Bearer {ensure_api_token()}"}).json()
    assert isinstance(body["checks"], list) and "channels" in body and "configuration" in body
