from __future__ import annotations

import json

import pytest

from adapters import _agent_client


class _Response:
    def __init__(self, body: bytes = b'{"status":"ok"}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit: int):
        return self.body


def test_adapter_client_defaults_to_loopback_and_write_only_namespace(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.delenv("WORKFLOW_OBSERVER_API", raising=False)
    monkeypatch.setenv("OWG_AGENT_INGEST_TOKEN", "write-only-test-token")
    monkeypatch.setattr(_agent_client, "urlopen", fake_urlopen)
    result = _agent_client.post_agent_events([{"event_id": "x"}])
    assert result["status"] == "ok"
    assert captured["url"] == "http://127.0.0.1:8787/agent-ingest/v1/events"
    assert captured["auth"] == "Bearer write-only-test-token"
    assert captured["body"] == {"events": [{"event_id": "x"}]}
    assert captured["timeout"] <= 0.75

    with pytest.raises(ValueError, match="agent-ingest"):
        _agent_client.post_json("/v1/events", {})


def test_adapter_client_rejects_remote_endpoint_by_default(monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_API", "https://collector.example.com")
    monkeypatch.delenv("OWG_AGENT_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("OWG_AGENT_INGEST_TOKEN", "test-token")
    with pytest.raises(ValueError, match="OWG_AGENT_ALLOW_REMOTE=1"):
        _agent_client.post_json("/agent-ingest/v1/events", {"events": []})


def test_adapter_client_allows_explicit_remote_opt_in(monkeypatch):
    called = {}

    def fake_urlopen(request, timeout):
        called["url"] = request.full_url
        return _Response()

    monkeypatch.setenv("WORKFLOW_OBSERVER_API", "https://collector.example.com")
    monkeypatch.setenv("OWG_AGENT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("OWG_AGENT_INGEST_TOKEN", "test-token")
    monkeypatch.setattr(_agent_client, "urlopen", fake_urlopen)
    assert _agent_client.post_json("/agent-ingest/v1/events", {"events": []})["status"] == "ok"
    assert called["url"].startswith("https://collector.example.com/agent-ingest/")
