from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import db as server_db
from server.agent_auth import ensure_agent_ingest_token
from server.agent_routes import AGENT_CODEX_OTEL_PATH, router as agent_router
from server.local_auth import ensure_api_token


def _attr(key: str, value):
    if isinstance(value, bool):
        wrapped = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def _payload() -> dict:
    attrs = {
        "event.name": "codex.tool_result",
        "event.timestamp": "2026-09-25T01:00:00.000Z",
        "conversation.id": "codex-route-conv",
        "tool_result_seq": 1,
        "tool_name": "exec_command",
        "tool_namespace": "functions",
        "call_id": "codex-route-call",
        "duration_ms": 42,
        "success": True,
        "arguments": "SUPERSECRET argument",
        "output": "patient@example.com SUPERSECRET result",
        "user.email": "patient@example.com",
    }
    return {
        "resourceLogs": [{
            "scopeLogs": [{"logRecords": [{
                "body": {"stringValue": "SUPERSECRET body"},
                "attributes": [_attr(k, v) for k, v in attrs.items()],
            }]}],
        }],
    }


def _app() -> FastAPI:
    server_db.init_db()
    app = FastAPI()
    app.include_router(agent_router)
    return app


def test_codex_route_requires_write_only_agent_token_and_persists_safe_evidence():
    agent_headers = {"Authorization": f"Bearer {ensure_agent_ingest_token()}"}
    api_headers = {"Authorization": f"Bearer {ensure_api_token()}"}
    payload = _payload()

    with TestClient(_app()) as client:
        assert client.post(AGENT_CODEX_OTEL_PATH, json=payload).status_code == 401
        assert client.post(AGENT_CODEX_OTEL_PATH, json=payload, headers=api_headers).status_code == 401
        accepted = client.post(AGENT_CODEX_OTEL_PATH, json=payload, headers=agent_headers)
        assert accepted.status_code == 202, accepted.text
        assert accepted.content == b""

        # The write-only telemetry credential cannot use the workflow read API.
        assert client.get("/v1/agent-workflows", headers=agent_headers).status_code == 401
        assert client.get("/v1/agent-workflows", headers=api_headers).status_code == 200

    rows = server_db.rows("SELECT * FROM events WHERE session_id = ?", ("codex-route-conv",))
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "agent"
    assert row["event_type"] == "agent_tool_call"
    assert row["metadata"]["agent"]["framework"] == "codex"
    assert row["metadata"]["tool"] == {"name": "exec_command", "category": "shell"}
    serialized = json.dumps(row, ensure_ascii=False)
    assert "SUPERSECRET" not in serialized
    assert "patient@example.com" not in serialized


def test_codex_route_ignores_unsupported_sensitive_records_instead_of_storing_them():
    payload = {
        "resourceLogs": [{
            "scopeLogs": [{"logRecords": [{
                "body": {"stringValue": "SUPERSECRET body"},
                "attributes": [
                    _attr("event.name", "codex.user_prompt"),
                    _attr("event.timestamp", "2026-09-25T01:01:00.000Z"),
                    _attr("conversation.id", "codex-secret-prompt"),
                    _attr("prompt", "SUPERSECRET prompt"),
                    _attr("user.email", "patient@example.com"),
                ],
            }]}],
        }],
    }
    headers = {"Authorization": f"Bearer {ensure_agent_ingest_token()}"}
    with TestClient(_app()) as client:
        response = client.post(AGENT_CODEX_OTEL_PATH, json=payload, headers=headers)
        assert response.status_code == 202
        assert response.content == b""
    assert server_db.rows("SELECT * FROM events WHERE session_id = ?", ("codex-secret-prompt",)) == []
