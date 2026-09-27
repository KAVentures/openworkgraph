from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import db as server_db
from server.agent_auth import ensure_agent_ingest_token
from server.agent_routes import AGENT_CLAUDE_OTEL_PATH, router as agent_router
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
        "event.name": "claude_code.api_request",
        "session.id": "claude-route-session",
        "prompt.id": "claude-route-prompt",
        "model": "claude-sonnet-5",
        "input_tokens": 25,
        "output_tokens": 5,
        "duration_ms": 500,
        "request_id": "native-private-request-id",
        "prompt": "SUPERSECRET prompt",
        "response": "patient@example.com SUPERSECRET response",
    }
    return {
        "resourceLogs": [{
            "scopeLogs": [{"logRecords": [{
                "body": {"stringValue": "SUPERSECRET log body"},
                "attributes": [_attr(k, v) for k, v in attrs.items()],
            }]}],
        }],
    }


def _app() -> FastAPI:
    server_db.init_db()
    app = FastAPI()
    app.include_router(agent_router)
    return app


def test_claude_otel_route_requires_write_only_token_and_persists_only_structure():
    agent_headers = {"Authorization": f"Bearer {ensure_agent_ingest_token()}"}
    api_headers = {"Authorization": f"Bearer {ensure_api_token()}"}

    with TestClient(_app()) as client:
        assert client.post(AGENT_CLAUDE_OTEL_PATH, json=_payload()).status_code == 401
        assert client.post(AGENT_CLAUDE_OTEL_PATH, json=_payload(), headers=api_headers).status_code == 401
        response = client.post(AGENT_CLAUDE_OTEL_PATH, json=_payload(), headers=agent_headers)
        assert response.status_code == 202, response.text
        assert response.content == b""

    rows = server_db.rows("SELECT * FROM events WHERE session_id = ?", ("claude-route-session",))
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "agent_model_call"
    assert row["sensor_id"] == "agent:claude-code-otel"
    assert row["metadata"]["agent"]["framework"] == "claude-code"
    assert row["metadata"]["agent"]["model"] == "claude-sonnet-5"
    assert row["metadata"]["usage"]["total_tokens"] == 30
    serialized = json.dumps(row, ensure_ascii=False)
    for forbidden in ("SUPERSECRET", "patient@example.com", "native-private-request-id"):
        assert forbidden not in serialized
