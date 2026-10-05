from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import connections


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OWG_CONNECTIONS_HOME", str(home))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("OWG_INSTALLED_ROOT", raising=False)
    monkeypatch.delenv("OWG_INSTALLED_PYTHON", raising=False)
    connections._CACHE.update(mtime=None, data={})
    return home


def test_setup_from_checkout_never_writes_when_install_is_missing(home, monkeypatch):
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: None)

    result = connections.setup_connection("cursor")

    assert result["status"] == "needs_install"
    assert result["writes_performed"] is False
    assert not (home / ".cursor" / "mcp.json").exists()
    assert result["ai_access"]["changed"] is False


def test_setup_delegates_to_durable_runtime_without_touching_clone_config(home, tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    python = tmp_path / "durable-python"
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: (installed, python))

    result = connections.setup_connection("codex")

    assert result["status"] == "delegate"
    assert result["writes_performed"] is False
    assert result["command"] == [
        str(python),
        str(installed / "owg_connect.py"),
        "on",
        "codex",
        "--mcp",
    ]
    assert not (home / ".codex" / "config.toml").exists()


def test_source_override_is_explicit_and_configures_mcp_only(home, monkeypatch):
    monkeypatch.setattr(connections, "_setup_access_status", lambda: {
        "changed": False,
        "currently_enabled": False,
        "required": True,
    })

    result = connections.setup_connection("cursor", allow_source=True)

    assert result["status"] == "configured"
    assert result["runtime"] == "source_override"
    assert result["connection"]["mcp"]["installed"] is True
    assert result["connection"]["observe"]["installed"] is False
    assert (home / ".cursor" / "mcp.json").exists()
    assert not (home / ".cursor" / "hooks.json").exists()
    assert result["ai_access"]["changed"] is False


def test_fresh_checkout_setup_needs_no_site_packages(home, tmp_path):
    env = os.environ.copy()
    env.update({
        "OWG_CONNECTIONS_HOME": str(home),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
    })
    run = subprocess.run(
        [sys.executable, "-S", str(ROOT / "owg_connect.py"), "setup", "--client", "cursor"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "needs_install"
    assert payload["writes_performed"] is False


def test_setup_cli_accepts_flagged_client_and_refuses_observe(home, tmp_path):
    env = os.environ.copy()
    env.update({
        "OWG_CONNECTIONS_HOME": str(home),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
    })

    configured = subprocess.run(
        [sys.executable, str(ROOT / "owg_connect.py"), "setup", "--client", "cursor", "--allow-source"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert configured.returncode == 0, configured.stderr
    payload = json.loads(configured.stdout)
    assert payload["status"] == "configured"
    assert payload["observe_changed"] is False

    refused = subprocess.run(
        [sys.executable, str(ROOT / "owg_connect.py"), "setup", "--client", "cursor", "--observe", "--allow-source"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert refused.returncode == 2
    denied = json.loads(refused.stdout)
    assert denied["observe_changed"] is False
    assert "MCP context only" in denied["error"]


def test_agent_instruction_contract_is_short_safe_and_canonical():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    gemini = (ROOT / "GEMINI.md").read_text(encoding="utf-8")

    assert "owg_connect.py setup --client <client_id>" in agents
    assert "do **not** launch mcp_server.compact_stdio directly" in agents
    assert "Never silently enable Observe" in agents
    assert "Setup never enables" in agents
    assert "get_current_work_context" in agents
    assert "Do **not** call OpenWorkGraph for ordinary coding" in agents
    assert "AGENTS.md" in claude and "AGENTS.md" in gemini


def test_connected_ai_guidance_is_continuity_first_not_always_on():
    compact = (ROOT / "mcp_server" / "compact.py").read_text(encoding="utf-8")
    skill = (ROOT / "plugins" / "openworkgraph" / "skills" / "owg" / "SKILL.md").read_text(encoding="utf-8")
    server_instructions = (ROOT / "mcp_server" / "workflow_evidence_tools.py").read_text(encoding="utf-8")

    assert "start with get_current_work_context" in compact
    assert "ordinary coding or general questions" in compact
    assert "get_current_work_context" in skill
    assert "Skip it for ordinary coding" in skill
    assert "start with get_current_work_context" in server_instructions
    assert "Skip OpenWorkGraph for ordinary coding" in server_instructions


def test_connection_note_matches_persistent_ai_access_behavior():
    notes = connections.list_connections()["notes"]
    assert "remembered" in notes["mcp_master_switch"]
    assert "resets to OFF" not in notes["mcp_master_switch"]
