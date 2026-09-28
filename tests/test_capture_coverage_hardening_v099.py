from __future__ import annotations

import gzip
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import agent_observe_presets as presets
from server.agent_config_writer import ConfigConflict
from shared.gemini_otel_adapter import gemini_otel_to_agent_events


def test_copilot_preserves_foreign_endpoint_even_when_disabled(tmp_path):
    path = tmp_path / "settings.json"
    original = {
        "github.copilot.chat.otel.enabled": False,
        "github.copilot.chat.otel.otlpEndpoint": "http://my-collector:4318",
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ConfigConflict, match="another endpoint"):
        presets.copilot_connect(path)

    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_copilot_stale_owg_token_is_repaired_and_removable(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    old = "http://127.0.0.1:8787/agent-ingest/otlp/copilot/old-token"
    new = "http://127.0.0.1:8787/agent-ingest/otlp/copilot/new-token"
    path.write_text(json.dumps({
        "editor.fontSize": 14,
        "github.copilot.chat.otel.enabled": True,
        "github.copilot.chat.otel.exporterType": "otlp-http",
        "github.copilot.chat.otel.otlpEndpoint": old,
        "github.copilot.chat.otel.captureContent": False,
    }), encoding="utf-8")
    monkeypatch.setattr(presets, "otlp_base_url", lambda source: new if source == "copilot" else f"http://127.0.0.1:8787/agent-ingest/otlp/{source}/new-token")

    result = presets.copilot_connect(path)
    assert result["configured"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["github.copilot.chat.otel.otlpEndpoint"] == new

    presets.copilot_disconnect(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"editor.fontSize": 14}


def test_gemini_stale_owg_token_is_repaired_and_removable(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    old = "http://127.0.0.1:8787/agent-ingest/otlp/gemini/old-token"
    new = "http://127.0.0.1:8787/agent-ingest/otlp/gemini/new-token"
    path.write_text(json.dumps({
        "theme": "system",
        "telemetry": {
            "enabled": True,
            "target": "local",
            "otlpEndpoint": old,
            "otlpProtocol": "http",
            "logPrompts": False,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(presets, "otlp_base_url", lambda source: new if source == "gemini" else f"http://127.0.0.1:8787/agent-ingest/otlp/{source}/new-token")

    result = presets.gemini_connect(path)
    assert result["configured"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["telemetry"]["otlpEndpoint"] == new

    presets.gemini_disconnect(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "system"}


def test_gemini_missing_tool_success_is_unknown_not_error():
    payload = {
        "resourceLogs": [{
            "resource": {"attributes": [{"key": "session.id", "value": {"stringValue": "s1"}}]},
            "scopeLogs": [{"logRecords": [{
                "timeUnixNano": "1790000000000000000",
                "attributes": [
                    {"key": "event.name", "value": {"stringValue": "gemini_cli.tool_call"}},
                    {"key": "prompt_id", "value": {"stringValue": "p1"}},
                    {"key": "function_name", "value": {"stringValue": "run_shell_command"}},
                ],
            }]}],
        }],
    }
    events, stats = gemini_otel_to_agent_events(payload)
    assert stats["records_seen"] == 1
    assert len(events) == 1
    assert events[0]["operation"] == "tool_call"
    assert events[0]["status"] == "unknown"


def test_otlp_gzip_requires_one_complete_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(tmp_path / "home"))

    from server.agent_auth import ensure_agent_otlp_path_token
    from server.agent_routes import router

    app = FastAPI()
    app.include_router(router)
    token = ensure_agent_otlp_path_token()
    url = f"/agent-ingest/otlp/copilot/{token}/v1/traces"
    headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}

    good = gzip.compress(b"{}")
    with TestClient(app) as client:
        assert client.post(url, content=good[:-4], headers=headers).status_code == 400
        assert client.post(url, content=good + gzip.compress(b"{}"), headers=headers).status_code == 400
