from __future__ import annotations

import json

import pytest

from server import agent_observe_presets as presets
from server.agent_config_writer import ConfigConflict


def _clean_env(monkeypatch):
    for key in (
        "COPILOT_OTEL_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "COPILOT_OTEL_ENABLED",
        "COPILOT_OTEL_CAPTURE_CONTENT",
        "COPILOT_OTEL_PROTOCOL",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_copilot_refuses_visible_environment_overrides_before_writing(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"editor.fontSize": 14}), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    monkeypatch.setenv("COPILOT_OTEL_ENDPOINT", "http://other-collector:4318")
    with pytest.raises(ConfigConflict, match="environment endpoint override"):
        presets.copilot_connect(path)
    assert path.read_text(encoding="utf-8") == before

    monkeypatch.delenv("COPILOT_OTEL_ENDPOINT")
    monkeypatch.setenv("COPILOT_OTEL_CAPTURE_CONTENT", "true")
    with pytest.raises(ConfigConflict, match="transmit prompt/response content"):
        presets.copilot_connect(path)
    assert path.read_text(encoding="utf-8") == before

    monkeypatch.delenv("COPILOT_OTEL_CAPTURE_CONTENT")
    monkeypatch.setenv("COPILOT_OTEL_PROTOCOL", "grpc")
    with pytest.raises(ConfigConflict, match="forced to gRPC"):
        presets.copilot_connect(path)
    assert path.read_text(encoding="utf-8") == before


def test_copilot_status_does_not_claim_configured_when_visible_override_wins(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    presets.copilot_connect(path)
    assert presets.copilot_status(path)["configured"] is True

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://enterprise-collector:4318")
    with pytest.raises(ConfigConflict, match="takes precedence"):
        presets.copilot_status(path)
