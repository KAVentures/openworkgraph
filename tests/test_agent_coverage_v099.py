from __future__ import annotations

"""Copilot, Gemini CLI and Cursor observation: endpoints, presets and hooks."""

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import agent_observe_presets as presets
from server import agent_telemetry_diagnostics as diagnostics
from server.agent_config_writer import ConfigConflict
from shared.cursor_hook_adapter import SUPPORTED_EVENTS, cursor_hook_to_agent_events

ROOT = Path(__file__).resolve().parents[1]
SECRET = "TOP-SECRET-CONTENT"


def _kv(key, value):
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def _span(name_op, span_id, attrs, start=1_790_000_000_000_000_000, dur=1_000_000_000, parent=""):
    return {
        "traceId": "a" * 32, "spanId": span_id, "parentSpanId": parent, "name": f"{name_op} {SECRET}",
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + dur),
        "attributes": [_kv("gen_ai.operation.name", name_op), *[_kv(k, v) for k, v in attrs.items()]],
        "status": {"code": 1},
    }


def copilot_traces() -> dict:
    return {"resourceSpans": [{"resource": {"attributes": [_kv("service.name", "copilot-chat")]}, "scopeSpans": [{
        "scope": {"name": "copilot-chat"},
        "spans": [
            _span("invoke_agent", "1" * 16, {"gen_ai.agent.name": "GitHub Copilot", "gen_ai.conversation.id": "conv-1",
                                             "gen_ai.request.model": "gpt-5"}, dur=5_000_000_000),
            _span("chat", "2" * 16, {"gen_ai.request.model": "gpt-5", "gen_ai.usage.input_tokens": 120,
                                     "gen_ai.usage.output_tokens": 30, "gen_ai.input.messages": SECRET}, parent="1" * 16),
            _span("execute_tool", "3" * 16, {"gen_ai.tool.name": "run_in_terminal", "gen_ai.tool.call.arguments": SECRET,
                                             "gen_ai.tool.call.result": SECRET}, parent="1" * 16),
        ],
    }]}]}


def gemini_logs() -> dict:
    def rec(**attrs):
        return {"timeUnixNano": "1790000000000000000", "attributes": [_kv(k, v) for k, v in attrs.items()]}
    return {"resourceLogs": [{"resource": {"attributes": [_kv("session.id", "g-sess")]}, "scopeLogs": [{"logRecords": [
        rec(**{"event.name": "gemini_cli.user_prompt", "event.timestamp": "2026-09-28T10:00:00.000Z", "prompt_id": "gp1", "prompt": SECRET}),
        rec(**{"event.name": "gemini_cli.api_response", "event.timestamp": "2026-09-28T10:00:01.000Z", "prompt_id": "gp1",
               "model": "gemini-2.5-pro", "duration_ms": 900, "status_code": 200, "input_token_count": 10, "response_text": SECRET}),
        rec(**{"event.name": "gemini_cli.tool_call", "event.timestamp": "2026-09-28T10:00:02.000Z", "prompt_id": "gp1",
               "function_name": "run_shell_command", "function_args": SECRET, "success": True, "decision": "auto_accept"}),
    ]}]}]}


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(tmp_path / "home"))
    from server.agent_routes import router

    app = FastAPI()
    app.include_router(router)
    diagnostics.reset_for_tests()
    with TestClient(app) as client:
        yield client


def _url(source: str, token: str | None = None, signal: str = "traces") -> str:
    from server.agent_auth import ensure_agent_otlp_path_token

    return f"/agent-ingest/otlp/{source}/{token or ensure_agent_otlp_path_token()}/v1/{signal}"


def _stored(framework: str) -> list[dict]:
    from server.db import rows

    return [r for r in rows("SELECT * FROM events WHERE source = 'agent'") if r["metadata"]["agent"].get("framework") == framework]


# --- the OTLP endpoint for settings-file clients ------------------------------------------------

def test_copilot_traces_become_structural_runs_without_content(app_client):
    from server.db import init_db

    init_db()
    response = app_client.post(_url("copilot"), json=copilot_traces())
    assert response.status_code == 200 and response.json() == {}
    stored = _stored("github-copilot")
    ops = sorted(r["metadata"]["operation"] for r in stored)
    assert ops == ["model_call", "run_finished", "run_started", "tool_call"]
    assert any(r["metadata"]["tool"]["name"] == "run_in_terminal" for r in stored)
    assert SECRET not in json.dumps(stored)
    ch = diagnostics.snapshot()["channels"]["copilot_otel"]
    assert ch["requests"] == 1 and ch["events_stored"] == 4


def test_gemini_logs_and_gzip_are_accepted(app_client):
    from server.db import init_db

    init_db()
    body = gzip.compress(json.dumps(gemini_logs()).encode())
    response = app_client.post(_url("gemini", signal="logs"), content=body,
                               headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})
    assert response.status_code == 200
    stored = _stored("gemini-cli")
    assert sorted(r["metadata"]["operation"] for r in stored) == ["model_call", "run_started", "tool_call"]
    assert SECRET not in json.dumps(stored)  # prompt, response text and arguments never copied
    # auto_accept is policy, not a person: no approval event
    assert not any(r["metadata"]["operation"] == "human_approval_received" for r in stored)


def test_endpoint_rejects_wrong_token_protobuf_and_unknown_sources(app_client):
    assert app_client.post(_url("copilot", token="wrong"), json={}).status_code == 401
    assert app_client.post(_url("copilot"), content=b"\x0a\x01", headers={"Content-Type": "application/x-protobuf"}).status_code == 415
    assert app_client.post(_url("nosuch"), json={}).status_code == 404
    assert app_client.post(_url("copilot", signal="profiles"), json={}).status_code == 404
    ch = diagnostics.snapshot()["channels"]["copilot_otel"]
    assert ch["rejected"] == {"auth": 1, "protobuf_unsupported": 1}


def test_metrics_are_accepted_counted_and_not_stored(app_client):
    from server.db import init_db

    init_db()
    before = len(_stored("github-copilot"))
    body = {"resourceMetrics": [{"scopeMetrics": [{"metrics": [{"name": "copilot_chat.session.count"}, {"name": "x"}]}]}]}
    assert app_client.post(_url("copilot", signal="metrics"), json=body).status_code == 200
    assert len(_stored("github-copilot")) == before
    ch = diagnostics.snapshot()["channels"]["copilot_otel"]
    assert ch["records_seen"] == 2 and ch["records_ignored"] == 2 and ch["events_stored"] == 0


def test_observe_switch_off_stores_nothing(app_client, monkeypatch):
    import server.agent_routes as routes
    from server.db import init_db

    init_db()
    original = routes.is_enabled
    monkeypatch.setattr(routes, "is_enabled", lambda client, kind: False if client == "vscode" else original(client, kind))
    before = len(_stored("github-copilot"))
    assert app_client.post(_url("copilot"), json=copilot_traces()).status_code == 200
    assert len(_stored("github-copilot")) == before
    assert diagnostics.snapshot()["channels"]["copilot_otel"]["observation_off"] == 1


def test_access_log_never_shows_the_path_token():
    from server.log_redaction import redact

    line = '127.0.0.1:1 - "POST /agent-ingest/otlp/gemini/abcDEF123_-xyz/v1/logs HTTP/1.1" 200'
    assert "abcDEF123" not in redact(line) and "/agent-ingest/otlp/gemini/[redacted]/v1/logs" in redact(line)


# --- presets ---------------------------------------------------------------------------------

def test_copilot_preset_merges_refuses_and_removes_only_its_keys(tmp_path):
    path = tmp_path / "Code" / "User" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"editor.fontSize": 14}))
    presets.copilot_connect(path)
    data = json.loads(path.read_text())
    assert data["editor.fontSize"] == 14 and data["github.copilot.chat.otel.captureContent"] is False
    assert data["github.copilot.chat.otel.enabled"] is True
    assert "/agent-ingest/otlp/copilot/" in data["github.copilot.chat.otel.otlpEndpoint"]
    assert presets.copilot_status(path)["configured"] is True
    presets.copilot_disconnect(path)
    assert json.loads(path.read_text()) == {"editor.fontSize": 14}

    path.write_text(json.dumps({"github.copilot.chat.otel.enabled": True, "github.copilot.chat.otel.otlpEndpoint": "http://my-collector:4318"}))
    with pytest.raises(ConfigConflict, match="another endpoint"):
        presets.copilot_connect(path)
    path.write_text(json.dumps({"github.copilot.chat.otel.captureContent": True}))
    with pytest.raises(ConfigConflict, match="content capture"):
        presets.copilot_connect(path)
    jsonc = '{\n  // my settings\n  "editor.fontSize": 14,\n}\n'
    path.write_text(jsonc)
    with pytest.raises(ConfigConflict, match="not plain JSON"):
        presets.copilot_connect(path)
    assert path.read_text() == jsonc


def test_gemini_preset_forces_prompt_logging_off_and_respects_own_telemetry(tmp_path):
    path = tmp_path / ".gemini" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"x": {}}}))
    presets.gemini_connect(path)
    data = json.loads(path.read_text())
    assert data["mcpServers"] == {"x": {}}
    assert data["telemetry"]["logPrompts"] is False and data["telemetry"]["otlpProtocol"] == "http"
    assert data["telemetry"]["target"] == "local" and data["telemetry"]["enabled"] is True
    presets.gemini_disconnect(path)
    assert "telemetry" not in json.loads(path.read_text())

    path.write_text(json.dumps({"telemetry": {"enabled": False}}))  # switched off: fine to take over
    presets.gemini_connect(path)
    path.write_text(json.dumps({"telemetry": {"enabled": True, "target": "gcp"}}))
    with pytest.raises(ConfigConflict, match="own telemetry"):
        presets.gemini_connect(path)
    path.write_text(json.dumps({"telemetry": {"logPrompts": True}}))
    with pytest.raises(ConfigConflict):
        presets.gemini_connect(path)


def test_cursor_preset_keeps_user_hooks_and_never_uses_permission_hooks(tmp_path):
    path = tmp_path / ".cursor" / "hooks.json"
    path.parent.mkdir()
    mine = {"command": "./audit.sh"}
    path.write_text(json.dumps({"version": 1, "hooks": {"stop": [mine], "beforeShellExecution": [mine]}}))
    presets.cursor_connect(path)
    presets.cursor_connect(path)  # idempotent: no duplicates
    data = json.loads(path.read_text())
    assert data["hooks"]["stop"][0] == mine and len(data["hooks"]["stop"]) == 2
    assert data["hooks"]["beforeShellExecution"] == [mine]
    for permission_hook in ("preToolUse", "beforeShellExecution", "beforeMCPExecution", "beforeReadFile", "subagentStart"):
        assert permission_hook not in SUPPORTED_EVENTS
        assert not any("adapters.cursor_hook" in h["command"] for h in data["hooks"].get(permission_hook, []))
    assert presets.cursor_status(path)["configured"] is True
    presets.cursor_disconnect(path)
    assert json.loads(path.read_text()) == {"version": 1, "hooks": {"stop": [mine], "beforeShellExecution": [mine]}}


# --- Cursor hooks --------------------------------------------------------------------------------

def test_cursor_mapping_turns_sessions_tools_and_no_content():
    common = {"conversation_id": "c1", "generation_id": "g1", "model": "claude-4.5-sonnet", "user_email": SECRET,
              "workspace_roots": [SECRET], "transcript_path": SECRET}
    events = []
    for hook, extra in (
        ("sessionStart", {"session_id": "c1"}),
        ("beforeSubmitPrompt", {"prompt": SECRET, "attachments": [SECRET]}),
        ("postToolUse", {"tool_name": "Shell", "tool_input": {"command": SECRET}, "tool_output": SECRET, "tool_use_id": "t1", "duration": 1500}),
        ("postToolUseFailure", {"tool_name": "Edit", "tool_use_id": "t2", "error_message": SECRET, "failure_type": "permission_denied"}),
        ("subagentStop", {"subagent_type": "explore", "status": "completed", "task": SECRET, "summary": SECRET}),
        ("stop", {"status": "aborted"}),
        ("sessionEnd", {"final_status": "completed", "error_message": SECRET}),
    ):
        events += cursor_hook_to_agent_events({**common, "hook_event_name": hook, **extra}, observed_at="2026-09-28T10:00:00Z")
    summary = [(e["operation"], e["status"], e["run_id"], e["tool_name"]) for e in events]
    assert summary == [
        ("run_started", "running", "c1", ""),
        ("run_started", "running", "g1", ""),
        ("tool_call", "success", "g1", "Shell"),
        ("tool_call", "denied", "g1", "Edit"),
        ("handoff", "success", "g1", "subagent:explore"),
        ("run_finished", "cancelled", "g1", ""),
        ("run_finished", "success", "c1", ""),
    ]
    assert events[2]["duration_seconds"] == 1.5 and events[2]["tool_category"] == "shell"
    assert SECRET not in json.dumps(events)
    # A turn hook without a generation never falls back to the conversation.
    assert cursor_hook_to_agent_events({"hook_event_name": "stop", "conversation_id": "c1", "status": "completed"}) == []
    from shared.agent_evidence import agent_event_to_evidence

    for event in events:
        agent_event_to_evidence(event)  # every event is valid evidence


@pytest.mark.parametrize("hook,expected", [("beforeSubmitPrompt", {"continue": True}), ("postToolUse", {}), ("stop", {})])
def test_cursor_hook_process_answers_immediately_and_never_blocks(tmp_path, hook, expected):
    env = {**os.environ, "WORKFLOW_OBSERVER_API": "http://127.0.0.1:9", "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path)}
    payload = json.dumps({"hook_event_name": hook, "conversation_id": "c", "generation_id": "g", "prompt": SECRET, "tool_name": "Shell"})
    done = subprocess.run([sys.executable, "-m", "adapters.cursor_hook"], input=payload, capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
    assert done.returncode == 0 and json.loads(done.stdout) == expected and SECRET not in done.stdout + done.stderr
    broken = subprocess.run([sys.executable, "-m", "adapters.cursor_hook"], input="{not json", capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
    assert broken.returncode == 0 and json.loads(broken.stdout) == {}


def test_manual_setup_material_matches_the_switches():
    material = presets.manual_setup_material()
    source = (ROOT / "server" / "agent_dashboard_control_plane.py").read_text()
    assert "**manual_setup_material()," in source
    assert set(material) == {"vscode", "gemini_cli", "cursor"}
    assert material["vscode"]["settings"]["github.copilot.chat.otel.captureContent"] is False
    assert material["gemini_cli"]["settings"]["telemetry"]["logPrompts"] is False
    assert set(material["cursor"]["settings"]["hooks"]) == set(SUPPORTED_EVENTS)
    assert all(item["content_logging_enabled"] is False for item in material.values())


def test_ephemeral_history_keeps_cursor_and_copilot_sessions_across_turns(app_client):
    """Turn finishes (Cursor stop, Copilot invoke_agent) must not purge the session."""
    from server.agent_ingest import ingest_agent_payloads
    from server.db import init_db, rows
    from shared.history_policy import update_retention

    init_db()
    update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)

    def cursor(hook, **extra):
        return cursor_hook_to_agent_events({"hook_event_name": hook, "conversation_id": "eph-cur", **extra})

    def count(session):
        return len([r for r in rows("SELECT session_id FROM events WHERE source = 'agent'") if r["session_id"] == session])

    ingest_agent_payloads(cursor("sessionStart") + cursor("beforeSubmitPrompt", generation_id="g1") + cursor("stop", generation_id="g1", status="completed"))
    ingest_agent_payloads(cursor("beforeSubmitPrompt", generation_id="g2"))
    assert count("eph-cur") == 4
    ingest_agent_payloads(cursor("sessionEnd", final_status="completed"))
    assert count("eph-cur") == 0

    app_client.post(_url("copilot"), json=copilot_traces())
    second = copilot_traces()
    for span in second["resourceSpans"][0]["scopeSpans"][0]["spans"]:
        span["traceId"] = "b" * 32
    app_client.post(_url("copilot"), json=second)
    # Two agent invocations in one conversation: both kept in full (the root span
    # carries the conversation; its model/tool spans are grouped by trace).
    assert count("conv-1") == 4 and count("a" * 32) == 2 and count("b" * 32) == 2
