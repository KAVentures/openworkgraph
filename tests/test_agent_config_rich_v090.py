from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from adapters.claude_code_hook import settings_fragment
from adapters.claude_code_otel import env_settings
from adapters.codex_config import config_snippet
from server import agent_config_writer as writer


@pytest.fixture
def claude_file(tmp_path, monkeypatch):
    path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setenv("OWG_CLAUDE_SETTINGS_PATH", str(path))
    return path


@pytest.fixture
def codex_file(tmp_path, monkeypatch):
    path = tmp_path / ".codex" / "config.toml"
    monkeypatch.setenv("OWG_CODEX_CONFIG_PATH", str(path))
    return path


def _managed_env() -> dict[str, str]:
    return env_settings(token="write-only-token", base_url="http://127.0.0.1:8787")


def test_rich_claude_connect_adds_hooks_and_logs_only_otel_preserving_unrelated_env(claude_file):
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({
        "model": "opus",
        "env": {"MY_COMPANY_SETTING": "keep-me"},
    }))
    managed = _managed_env()
    result = writer.claude_connect(settings_fragment, managed)
    data = json.loads(claude_file.read_text())

    assert result["configured"] is True
    assert result["hooks_configured"] is True
    assert result["telemetry_configured"] is True
    assert data["env"]["MY_COMPANY_SETTING"] == "keep-me"
    for key, value in managed.items():
        assert data["env"][key] == value
    assert data["env"]["OTEL_LOGS_EXPORTER"] == "otlp"
    assert data["env"]["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] == "http/json"
    assert data["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"].endswith("/agent-ingest/v1/claude-otel")
    assert data["env"]["OTEL_LOG_USER_PROMPTS"] == "0"
    assert data["env"]["OTEL_LOG_ASSISTANT_RESPONSES"] == "0"
    assert data["env"]["OTEL_LOG_TOOL_DETAILS"] == "0"
    assert data["env"]["OTEL_LOG_TOOL_CONTENT"] == "0"
    assert data["env"]["OTEL_LOG_RAW_API_BODIES"] == "0"
    status = writer.claude_status(managed)
    assert status["configured"] is True
    assert status["hooks_configured"] is True
    assert status["telemetry_configured"] is True


def test_rich_claude_connect_refuses_to_overwrite_foreign_telemetry(claude_file):
    original = {
        "env": {
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://company-collector.example/v1/logs",
            "COMPANY": "keep",
        }
    }
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps(original))

    with pytest.raises(writer.ConfigConflict):
        writer.claude_connect(settings_fragment, _managed_env())

    assert json.loads(claude_file.read_text()) == original
    assert not list(claude_file.parent.glob("settings.json.owg-backup-*"))


def test_rich_claude_disconnect_removes_only_values_still_owned_by_owg(claude_file):
    managed = _managed_env()
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({"env": {"COMPANY": "keep"}}))
    writer.claude_connect(settings_fragment, managed)

    # The user changes one previously OWG-managed setting after setup. Disconnect
    # must not delete or rewrite that value because OWG no longer owns it.
    data = json.loads(claude_file.read_text())
    data["env"]["OTEL_LOGS_EXPORTER"] = "console"
    claude_file.write_text(json.dumps(data))

    result = writer.claude_disconnect(managed)
    after = json.loads(claude_file.read_text())
    assert result["configured"] is False
    assert after["env"] == {"COMPANY": "keep", "OTEL_LOGS_EXPORTER": "console"}
    assert "hooks" not in after


def test_claude_manual_fragment_contains_no_content_collection_opt_in():
    managed = _managed_env()
    forbidden_truthy = [key for key in managed if key.startswith("OTEL_LOG_") and managed[key] not in {"0", "false", "False"}]
    assert forbidden_truthy == []
    assert "OTEL_TRACES_EXPORTER" not in managed  # beta detailed traces are not required for v0.90


def test_codex_fragment_exports_structural_logs_and_traces_but_no_content_opt_ins():
    snippet = config_snippet(token="write-only-token", base_url="http://127.0.0.1:8787")
    parsed = tomllib.loads(snippet)["otel"]
    assert "otlp-http" in parsed["exporter"]
    assert "otlp-http" in parsed["trace_exporter"]
    assert parsed["exporter"]["otlp-http"]["endpoint"].endswith("/agent-ingest/v1/codex-otel")
    assert parsed["trace_exporter"]["otlp-http"]["endpoint"].endswith("/agent-ingest/v1/codex-otel")
    assert parsed["log_user_prompt"] is False
    assert parsed["log_agent_responses"] is False
    assert parsed["log_guardian_assessments"] is False


def test_codex_trace_only_owg_block_is_detected_as_partial_and_upgraded(codex_file):
    codex_file.parent.mkdir(parents=True)
    endpoint = "http://127.0.0.1:8787/agent-ingest/v1/codex-otel"
    old_block = "\n".join([
        writer.CODEX_BLOCK_START,
        "[otel]",
        "log_user_prompt = false",
        "log_agent_responses = false",
        "log_guardian_assessments = false",
        f'trace_exporter = {{ otlp-http = {{ endpoint = "{endpoint}", headers = {{ Authorization = "Bearer tok" }}, protocol = "json" }} }}',
        writer.CODEX_BLOCK_END,
        "",
    ])
    codex_file.write_text(old_block)

    before = writer.codex_status()
    assert before["configured"] is False
    assert before["partial_configured"] is True

    writer.codex_connect(config_snippet(token="tok", base_url="http://127.0.0.1:8787"))
    after = writer.codex_status()
    assert after["configured"] is True
    assert after["partial_configured"] is False
    parsed = tomllib.loads(codex_file.read_text())["otel"]
    assert "exporter" in parsed and "trace_exporter" in parsed
