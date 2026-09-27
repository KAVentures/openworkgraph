from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from adapters.claude_code_hook import settings_fragment
from adapters.codex_config import config_snippet
from server import agent_config_writer as writer


ROOT = Path(__file__).resolve().parents[1]
SNIPPET = config_snippet(token="tok", base_url="http://127.0.0.1:8787")


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


def _owg_handlers(data):
    return [
        h for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]
        if writer._is_owg_handler(h)
    ]


def test_claude_connect_preserves_user_settings_and_hooks(claude_file):
    user_hook = {"type": "command", "command": "echo mine"}
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [user_hook]}]},
    }))

    result = writer.claude_connect(settings_fragment)
    data = json.loads(claude_file.read_text())

    assert result["configured"] is True
    assert result["backup"] and Path(result["backup"]).exists()
    assert data["model"] == "opus"
    assert data["hooks"]["PostToolUse"][0] == {"matcher": "Bash", "hooks": [user_hook]}
    assert len(_owg_handlers(data)) == len(settings_fragment()["hooks"])
    assert writer.claude_status()["configured"] is True


def test_claude_connect_is_idempotent_and_replaces_legacy_args_form(claude_file):
    legacy = {"type": "command", "command": "/py", "args": ["-m", "adapters.claude_code_hook"]}
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [legacy]}]}}))
    writer.claude_connect(settings_fragment)
    writer.claude_connect(settings_fragment)
    data = json.loads(claude_file.read_text())

    assert len(_owg_handlers(data)) == len(settings_fragment()["hooks"])
    assert all("args" not in h for h in _owg_handlers(data))


def test_claude_disconnect_removes_only_openworkgraph(claude_file):
    user_hook = {"type": "command", "command": "echo mine"}
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(json.dumps({"theme": "dark", "hooks": {"PostToolUse": [{"hooks": [user_hook]}]}}))
    writer.claude_connect(settings_fragment)

    result = writer.claude_disconnect()
    data = json.loads(claude_file.read_text())

    assert result["configured"] is False
    assert data == {"theme": "dark", "hooks": {"PostToolUse": [{"hooks": [user_hook]}]}}
    assert writer.claude_disconnect()["backup"] is None  # nothing left to remove, no write


def test_claude_connect_creates_missing_file_and_refuses_invalid_json(claude_file):
    writer.claude_connect(settings_fragment)
    assert writer.claude_status()["configured"] is True
    # POSIX permission bits are meaningful on Unix-like systems. Windows reports
    # synthesized mode bits (commonly 0666), so asserting 0600 there is invalid.
    if os.name != "nt":
        assert oct(claude_file.stat().st_mode & 0o777) == "0o600"

    claude_file.write_text("{not json")
    with pytest.raises(writer.ConfigConflict):
        writer.claude_connect(settings_fragment)
    assert claude_file.read_text() == "{not json"


def test_codex_connect_appends_managed_block_and_disconnect_restores(codex_file):
    original = 'model = "gpt-5"\n\n[profiles.fast]\nmodel = "gpt-5-mini"\n'
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text(original)

    result = writer.codex_connect(SNIPPET)
    parsed = tomllib.loads(codex_file.read_text())

    assert result["configured"] is True
    assert parsed["model"] == "gpt-5"
    assert parsed["profiles"]["fast"]["model"] == "gpt-5-mini"
    assert parsed["otel"]["log_user_prompt"] is False
    assert "otlp-http" in parsed["otel"]["trace_exporter"]
    assert writer.codex_status()["configured"] is True

    writer.codex_connect(SNIPPET)  # idempotent
    assert codex_file.read_text().count(writer.CODEX_BLOCK_START) == 1

    writer.codex_disconnect()
    assert codex_file.read_text() == original
    assert writer.codex_status()["configured"] is False


def test_codex_connect_refuses_foreign_otel_settings(codex_file):
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text('[otel]\nenvironment = "prod"\n')
    with pytest.raises(writer.ConfigConflict):
        writer.codex_connect(SNIPPET)
    assert codex_file.read_text() == '[otel]\nenvironment = "prod"\n'


def test_codex_connect_on_missing_file(codex_file):
    writer.codex_connect(SNIPPET)
    assert "trace_exporter" in tomllib.loads(codex_file.read_text())["otel"]


def test_agent_config_routes_require_auth_and_toggle(tmp_path):
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "OWG_CLAUDE_SETTINGS_PATH": str(tmp_path / "claude.json"),
        "OWG_CODEX_CONFIG_PATH": str(tmp_path / "codex.toml"),
    })
    code = r'''
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
import server.agent_dashboard_control_plane
from server.secure_app import app

auth={'Authorization':f'Bearer {ensure_api_token()}'}
with TestClient(app) as client:
    assert client.get('/v1/agent-config').status_code==401
    assert client.post('/v1/agent-config',json={'agent':'claude_code','action':'connect'}).status_code==401
    status=client.get('/v1/agent-config',headers=auth).json()['integrations']
    assert status['claude_code']['configured'] is False and status['codex']['configured'] is False
    for agent in ('claude_code','codex'):
        r=client.post('/v1/agent-config',headers=auth,json={'agent':agent,'action':'connect'})
        assert r.status_code==200 and r.json()['configured'] is True, r.text
    status=client.get('/v1/agent-config',headers=auth).json()['integrations']
    assert status['claude_code']['configured'] and status['codex']['configured']
    r=client.post('/v1/agent-config',headers=auth,json={'agent':'claude_code','action':'disconnect'})
    assert r.json()['configured'] is False
    assert client.post('/v1/agent-config',headers=auth,json={'agent':'openai_agents','action':'connect'}).status_code==400
    open(__import__('os').environ['OWG_CODEX_CONFIG_PATH'],'w').write('[otel]\nx = 1\n')
    r=client.post('/v1/agent-config',headers=auth,json={'agent':'codex','action':'connect'})
    assert r.status_code==409 and r.json()['manual_setup_required'] is True
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
