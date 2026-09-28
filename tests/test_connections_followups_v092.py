from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from server import connections


ROOT = Path(__file__).resolve().parents[1]
CHATGPT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(home))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    for var in ("OWG_CLAUDE_SETTINGS_PATH", "OWG_CODEX_CONFIG_PATH", "OWG_CLAUDE_CODE_MCP_PATH", "CODEX_HOME", "COPILOT_HOME"):
        monkeypatch.delenv(var, raising=False)
    connections._CACHE.update(mtime=None, data={})
    connections._PROCESS_CACHE.update(at=0.0, procs=[])
    return home


def _fake_processes(monkeypatch, procs):
    monkeypatch.setattr(connections, "_processes", lambda: procs)


@pytest.mark.parametrize("client_id,relpath,entry_type", [
    ("copilot_cli", ".copilot/mcp-config.json", "local"),
    ("kiro", ".kiro/settings/mcp.json", None),
    ("amazon_q", ".aws/amazonq/mcp.json", None),
])
def test_new_mcp_clients_write_their_native_format(home, client_id, relpath, entry_type):
    connections.change(client_id, "on", ("mcp",))
    entry = json.loads((home / relpath).read_text())["mcpServers"]["openworkgraph"]
    assert entry["args"][-2:] == ["--client", client_id]
    assert entry.get("type") == entry_type
    if client_id == "copilot_cli":
        assert entry["tools"] == ["*"] and entry["env"] == {}


def test_codex_observe_reports_restart_until_engine_restarts(home, monkeypatch):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "x"\n')
    _fake_processes(monkeypatch, [(CHATGPT_CODEX, [CHATGPT_CODEX, "app-server"], time.time() - 86400)])

    status = connections.change("codex", "on")
    assert status["observe"]["restart_needed"]["app"] == "the ChatGPT app"
    # Context is reloaded per chat by Codex, so it never asks for a restart.
    assert "restart_needed" not in status["mcp"]

    _fake_processes(monkeypatch, [(CHATGPT_CODEX, [CHATGPT_CODEX, "app-server"], time.time() + 5)])
    assert "restart_needed" not in connections.client_status("codex")["observe"]

    _fake_processes(monkeypatch, [])
    assert "restart_needed" not in connections.client_status("codex")["observe"]


def test_restart_uses_backup_time_for_installs_made_before_tracking(home, monkeypatch):
    # Installs from older versions have no recorded time; the newest backup
    # (written right before every managed change) stands in for it.
    config = home / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "x"\n')
    connections.change("codex", "on", ("observe",))
    data = json.loads((home.parent / "data" / "connections.json").read_text())
    data.pop("installed_at")
    (home.parent / "data" / "connections.json").write_text(json.dumps(data))
    connections._CACHE.update(mtime=None, data={})
    _fake_processes(monkeypatch, [("", [CHATGPT_CODEX], time.time() - 3600)])
    assert list(config.parent.glob("config.toml.owg-backup-*"))
    assert connections.client_status("codex")["observe"]["restart_needed"]


def test_claude_desktop_matcher_ignores_helpers_and_claude_code():
    main = "/Applications/Claude.app/Contents/MacOS/Claude"
    helper = "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper"
    assert connections._claude_desktop_process(main, [main]) == "Claude Desktop"
    assert connections._claude_desktop_process(helper, [helper]) == ""
    assert connections._claude_desktop_process("/usr/local/bin/claude", ["claude"]) == ""
    assert connections._codex_process("", ["/usr/local/bin/codex", "exec"]) == "Codex"
    assert connections._codex_process("", ["/Applications/ChatGPT.app/Contents/Resources/codex-code-mode-host"]) == ""


def test_hidden_frameworks_follow_observe_switch(home):
    everything = {"claude-code", "codex", "github-copilot", "gemini-cli", "cursor"}
    assert connections.hidden_frameworks() == everything  # nothing installed
    connections.change("claude_code", "on", ("observe",))
    assert connections.hidden_frameworks() == everything - {"claude-code"}
    connections.change("claude_code", "off", ("observe",))
    assert "claude-code" in connections.hidden_frameworks()


def test_agent_traces_hide_disconnected_history_only_when_asked(tmp_path):
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "OWG_CONNECTIONS_HOME": str(tmp_path / "home"),
    })
    (tmp_path / "home").mkdir()
    code = r'''
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
from server.agent_auth import ensure_agent_ingest_token
import server.agent_dashboard_control_plane
from server.secure_app import app

auth={'Authorization':f'Bearer {ensure_api_token()}'}
agent={'Authorization':f'Bearer {ensure_agent_ingest_token()}'}
now=datetime.now(timezone.utc).isoformat()
def event(eid, framework, name):
    return {'event_id':eid,'observed_at':now,'agent_name':name,'provider':'x','framework':framework,
            'operation':'tool_call','status':'success','observation_level':'native_trace','run_id':eid,
            'trace_id':eid,'span_id':'','tool_name':'Bash','tool_category':'shell','duration_seconds':0.0}
with TestClient(app) as client:
    client.post('/v1/connections',headers=auth,json={'client':'claude_code','kind':'observe','action':'on'})
    r=client.post('/agent-ingest/v1/events',headers=agent,json={'events':[event('c1','claude-code','Claude Code'),event('x1','custom','My Agent')]})
    assert r.status_code==200, r.text
    client.post('/v1/connections',headers=auth,json={'client':'claude_code','kind':'observe','action':'off'})
    url='/v1/agent-execution-traces?limit=50&evidence_limit=25000&max_events_per_execution=5'
    names=lambda p:sorted({(x.get('agent') or {}).get('name') for x in p['executions']})
    everything=client.get(url,headers=auth).json()
    assert names(everything)==['Claude Code','My Agent'], names(everything)
    filtered=client.get(url+'&hide_disconnected=true',headers=auth).json()
    assert names(filtered)==['My Agent'], names(filtered)
    assert 'claude-code' in filtered['hidden_frameworks']
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=90)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
