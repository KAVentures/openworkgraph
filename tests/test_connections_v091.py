from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from server import connections
from server import agent_config_writer as writer


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(home))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    for var in ("OWG_CLAUDE_SETTINGS_PATH", "OWG_CODEX_CONFIG_PATH", "OWG_CLAUDE_CODE_MCP_PATH", "CODEX_HOME"):
        monkeypatch.delenv(var, raising=False)
    connections._CACHE.update(mtime=None, data={})
    return home


def _status(client_id):
    return connections.client_status(client_id)


def test_every_client_uses_the_launcher_with_its_own_client_id(home):
    for client_id, client in connections.CLIENTS.items():
        command, args = connections.mcp_command(client_id)
        assert command == sys.executable
        assert args[0].endswith("launcher.py") and args[1:] == ["--client", client_id]
        assert client.mcp is not None


@pytest.mark.parametrize("client_id,relpath,key", [
    ("claude_code", ".claude.json", "mcpServers"),
    ("cursor", ".cursor/mcp.json", "mcpServers"),
    ("windsurf", ".codeium/windsurf/mcp_config.json", "mcpServers"),
    ("gemini_cli", ".gemini/settings.json", "mcpServers"),
])
def test_json_mcp_on_off_remove_preserves_other_servers(home, client_id, relpath, key):
    path = home / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": "x", key: {"other": {"command": "other"}}}))

    result = connections.change(client_id, "on", ("mcp",))
    data = json.loads(path.read_text())
    assert result["mcp"]["on"] is True
    assert result["changes"]["mcp"]["backup"]
    assert data["theme"] == "x" and data[key]["other"] == {"command": "other"}
    assert data[key]["openworkgraph"]["args"][-2:] == ["--client", client_id]

    connections.change(client_id, "off", ("mcp",))
    assert _status(client_id)["mcp"] == {**_status(client_id)["mcp"], "installed": True, "enabled": False, "on": False}
    assert "openworkgraph" in json.loads(path.read_text())[key]  # off keeps config: instant, no restart
    assert connections.is_enabled(client_id, "mcp") is False

    connections.change(client_id, "on", ("mcp",))
    assert connections.is_enabled(client_id, "mcp") is True

    connections.change(client_id, "remove", ("mcp",))
    data = json.loads(path.read_text())
    assert "openworkgraph" not in data[key] and data[key]["other"] == {"command": "other"}


def test_vscode_uses_servers_key_and_claude_desktop_plain_entry(home):
    connections.change("vscode", "on", ("mcp",))
    connections.change("claude_desktop", "on", ("mcp",))
    vscode = json.loads(connections.CLIENTS["vscode"].mcp.path().read_text())
    desktop = json.loads(connections.CLIENTS["claude_desktop"].mcp.path().read_text())
    assert vscode["servers"]["openworkgraph"]["type"] == "stdio"
    assert set(desktop["mcpServers"]["openworkgraph"]) == {"command", "args"}


def test_codex_mcp_and_observe_blocks_coexist_and_remove_independently(home):
    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "gpt-5"\n')

    connections.change("codex", "on")
    parsed = tomllib.loads(codex.read_text())
    assert parsed["model"] == "gpt-5"
    assert parsed["mcp_servers"]["openworkgraph"]["args"][-2:] == ["--client", "codex"]
    assert "trace_exporter" in parsed["otel"]

    connections.change("codex", "remove", ("mcp",))
    parsed = tomllib.loads(codex.read_text())
    assert "mcp_servers" not in parsed and "otel" in parsed
    connections.change("codex", "remove", ("observe",))
    assert codex.read_text() == 'model = "gpt-5"\n'


def test_codex_mcp_refuses_hand_written_entry(home):
    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('[mcp_servers.openworkgraph]\ncommand = "mine"\n')
    with pytest.raises(writer.ConfigConflict):
        connections.change("codex", "on", ("mcp",))
    assert codex.read_text() == '[mcp_servers.openworkgraph]\ncommand = "mine"\n'


def test_claude_code_both_kinds_and_observe_only_where_supported(home):
    result = connections.change("claude_code", "on")
    assert result["mcp"]["on"] and result["observe"]["on"]
    assert (home / ".claude" / "settings.json").exists()
    assert _status("cursor")["observe"]["supported"] is False
    with pytest.raises(ValueError):
        connections.change("cursor", "on", ("observe",))
    # "both" on a context-only client just does context
    assert connections.change("cursor", "on")["mcp"]["on"] is True


def test_invalid_json_is_never_touched(home):
    path = home / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{oops")
    with pytest.raises(writer.ConfigConflict):
        connections.change("cursor", "on", ("mcp",))
    assert path.read_text() == "{oops"
    assert "error" in _status("cursor")["mcp"] or _status("cursor")["mcp"]["installed"] is False


def test_unknown_or_untouched_client_defaults_to_enabled(home):
    assert connections.is_enabled(None, "mcp") is True
    assert connections.is_enabled("some_future_client", "mcp") is True


def test_cli_works_from_any_directory_and_prints_json(tmp_path):
    env = os.environ.copy()
    env.update({"OWG_CONNECTIONS_HOME": str(tmp_path / "home"), "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data")})
    (tmp_path / "home").mkdir()
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "owg_connect.py"), *a], cwd=tmp_path,
                                    env=env, capture_output=True, text=True, timeout=60)
    listed = run("list")
    assert listed.returncode == 0, listed.stderr
    assert {c["id"] for c in json.loads(listed.stdout)["clients"]} >= {"claude_code", "cursor", "codex", "vscode"}
    on = run("on", "cursor", "--mcp")
    assert on.returncode == 0 and json.loads(on.stdout)["mcp"]["on"] is True
    off = run("off", "cursor", "--mcp")
    assert json.loads(off.stdout)["mcp"]["enabled"] is False
    (tmp_path / "home" / ".gemini").mkdir()
    (tmp_path / "home" / ".gemini" / "settings.json").write_text("{bad")
    bad = run("on", "gemini_cli")
    assert bad.returncode == 2 and json.loads(bad.stdout)["manual_setup_required"] is True


def test_launcher_consumes_client_flag(monkeypatch, tmp_path):
    import types
    from mcp_server import launcher

    seen = {}

    class FakeMcp:
        def run(self, *, transport):
            seen["client"] = os.environ.get("OWG_MCP_CLIENT")
            seen["argv"] = list(sys.argv)

    monkeypatch.setitem(sys.modules, "mcp_server.compact_stdio", types.SimpleNamespace(mcp=FakeMcp()))
    monkeypatch.setattr(sys, "argv", ["launcher.py", "--client", "cursor"])
    monkeypatch.delenv("OWG_MCP_CLIENT", raising=False)
    monkeypatch.chdir(tmp_path)
    launcher.main()
    assert seen == {"client": "cursor", "argv": ["launcher.py"]}


def test_routes_gate_mcp_and_observation_per_client(tmp_path):
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "OWG_CONNECTIONS_HOME": str(tmp_path / "home"),
    })
    (tmp_path / "home").mkdir()
    code = r'''
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
from server.agent_auth import ensure_agent_ingest_token
import server.agent_dashboard_control_plane
from server.secure_app import app

auth={'Authorization':f'Bearer {ensure_api_token()}'}
agent={'Authorization':f'Bearer {ensure_agent_ingest_token()}'}
with TestClient(app) as client:
    assert client.get('/v1/connections').status_code==401
    assert client.post('/v1/connections',json={'client':'cursor','kind':'mcp','action':'on'}).status_code==401
    assert client.post('/v1/ai-access',headers=auth,json={'enabled':True}).json()['enabled'] is True

    r=client.post('/v1/connections',headers=auth,json={'client':'cursor','kind':'mcp','action':'on'})
    assert r.status_code==200 and r.json()['mcp']['on'] is True, r.text
    assert client.get('/v1/ai-access?client=cursor',headers=auth).json()['enabled'] is True
    client.post('/v1/connections',headers=auth,json={'client':'cursor','kind':'mcp','action':'off'})
    state=client.get('/v1/ai-access?client=cursor',headers=auth).json()
    assert state['enabled'] is False and state['client_enabled'] is False and state['global_enabled'] is True
    assert client.get('/v1/ai-access?client=vscode',headers=auth).json()['enabled'] is True
    assert client.get('/v1/ai-access',headers=auth).json()['enabled'] is True

    listed=client.get('/v1/connections',headers=auth).json()
    assert 'owg_connect.py' in listed['cli']
    cursor=[c for c in listed['clients'] if c['id']=='cursor'][0]
    assert cursor['mcp']['installed'] is True and cursor['mcp']['on'] is False

    event={'events':[{'event_id':'e1','observed_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'agent_name':'Claude Code',
        'provider':'anthropic','framework':'claude-code','operation':'tool_call','status':'success',
        'observation_level':'native_trace','run_id':'r','trace_id':'r','span_id':'','tool_name':'Bash',
        'tool_category':'shell','duration_seconds':0.0}]}
    client.post('/v1/connections',headers=auth,json={'client':'claude_code','kind':'observe','action':'off'})
    dropped=client.post('/agent-ingest/v1/events',headers=agent,json=event)
    assert dropped.status_code==200 and dropped.json()['status']=='observation_off', dropped.text
    client.post('/v1/connections',headers=auth,json={'client':'claude_code','kind':'observe','action':'on'})
    kept=client.post('/agent-ingest/v1/events',headers=agent,json=event)
    assert kept.status_code==200 and kept.json()['status']=='ok', kept.text

    assert client.post('/v1/connections',headers=auth,json={'client':'nope','action':'on'}).status_code==400
    assert client.get('/connections.js').status_code==200
    assert '<script src="/connections.js"></script>' in client.get('/').text
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=90)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
