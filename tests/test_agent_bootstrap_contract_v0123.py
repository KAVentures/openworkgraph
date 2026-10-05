from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import agent_bootstrap, connections


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


def _stub_bootstrap_runtime(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    python = root / ("python.exe" if os.name == "nt" else "python")
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(
        agent_bootstrap,
        "_install_if_needed",
        lambda: {"ok": True, "performed": False, "runtime": (root, python)},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_start_if_needed",
        lambda *_: {"ok": True, "performed": False, "health": {"ok": True, "payload": {"status": "ok"}}},
    )
    return root, python


def test_autonomous_known_client_reaches_ready_without_manual_machine_steps(home, tmp_path, monkeypatch):
    root, python = _stub_bootstrap_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_bootstrap,
        "_connect_known",
        lambda *_: {"ok": True, "result": {"mcp": {"installed": True, "enabled": True, "on": True}}},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_permission_state",
        lambda *_: {"ok": True, "permissions": {"accessibility": True, "input_monitoring": True}, "missing": []},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_access_and_context_probe",
        lambda *_: {"enabled": True, "resets_on_restart": False, "probe": {"ok": True, "returned": 0, "detail_level": "redacted"}},
    )

    result = agent_bootstrap.run(local=True, client_id="codex")

    assert result["status"] == "ready"
    assert result["installed_root"] == str(root)
    assert result["install_performed"] is False
    assert result["health"]["ok"] is True
    assert result["connection"]["ok"] is True
    assert result["context_probe"]["ok"] is True
    assert result["agent_actions"] == []
    assert result["user_actions"] == []


def test_new_known_client_config_requires_reload_before_ready(home, tmp_path, monkeypatch):
    _stub_bootstrap_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_bootstrap,
        "_connect_known",
        lambda *_: {
            "ok": True,
            "result": {
                "id": "codex",
                "label": "Codex",
                "changes": {"mcp": {"takes_effect": "in new Codex chats"}},
                "mcp": {"installed": True, "enabled": True, "on": True},
            },
        },
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_permission_state",
        lambda *_: {"ok": True, "permissions": {"accessibility": True, "input_monitoring": True}, "missing": []},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_access_and_context_probe",
        lambda *_: {"enabled": True, "resets_on_restart": False, "probe": {"ok": True, "returned": 0}},
    )

    result = agent_bootstrap.run(local=True, client_id="codex")

    assert result["status"] == "needs_user_action"
    assert result["user_actions"][0]["kind"] == "reload_ai_client"
    assert "new Codex chats" in result["user_actions"][0]["takes_effect"]


def test_autonomous_bootstrap_stops_only_for_real_human_consent(home, tmp_path, monkeypatch):
    _stub_bootstrap_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_bootstrap,
        "_connect_known",
        lambda *_: {"ok": True, "result": {"mcp": {"installed": True, "enabled": True, "on": True}}},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_permission_state",
        lambda *_: {"ok": True, "permissions": {"accessibility": False, "input_monitoring": False}, "missing": ["accessibility", "input_monitoring"]},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_access_and_context_probe",
        lambda *_: {"enabled": False, "resets_on_restart": False, "probe": {"ok": False, "reason": "ai_access_off"}},
    )

    result = agent_bootstrap.run(local=True, client_id="codex")

    assert result["status"] == "needs_user_action"
    kinds = {item["kind"] for item in result["user_actions"]}
    assert kinds == {"macos_privacy_permissions", "ai_access"}
    assert result["agent_actions"] == []


def test_remote_autonomous_bootstrap_never_calls_local_installer(home, monkeypatch):
    def fail_install():
        raise AssertionError("remote bootstrap must never install locally")

    monkeypatch.setattr(agent_bootstrap, "_install_if_needed", fail_install)

    result = agent_bootstrap.run(remote=True, self_route=True, name="grok")

    assert result["status"] == "needs_gateway"
    assert result["do_not_install_here"] is True
    assert result["writes_performed"] is False


def test_unknown_local_agent_gets_agent_action_not_user_config_work(home, tmp_path, monkeypatch):
    root, python = _stub_bootstrap_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        connections,
        "_generic_local_descriptor",
        lambda name, installed_root, runtime_python: {
            "connection": {
                "name": "openworkgraph",
                "transport": "stdio",
                "command": str(runtime_python),
                "args": [str(installed_root / "mcp_server" / "launcher.py"), "--client", "external_grok"],
            }
        },
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_permission_state",
        lambda *_: {"ok": True, "permissions": {}, "missing": []},
    )
    monkeypatch.setattr(
        agent_bootstrap,
        "_access_and_context_probe",
        lambda *_: {"enabled": True, "resets_on_restart": False, "probe": {"ok": True, "returned": 0}},
    )

    result = agent_bootstrap.run(local=True, self_route=True, name="grok")

    assert result["status"] == "needs_agent_action"
    assert result["user_actions"] == []
    assert result["agent_actions"][0]["kind"] == "register_mcp_descriptor"
    assert result["agent_actions"][0]["descriptor"]["transport"] == "stdio"
    assert result["agent_actions"][0]["instruction"].startswith("Register")
    assert result["installed_root"] == str(root)


def test_incomplete_runtime_reenters_official_installer(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    calls = iter([(root, None), (root, root / "python")])
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: next(calls))
    monkeypatch.setattr(
        connections,
        "_install_instruction",
        lambda: {"platform": "macos", "command": ["/bin/echo", "official-installer"], "human_action": ""},
    )

    class FakeProcess:
        pid = 4567
        def poll(self):
            return None

    monkeypatch.setattr(
        agent_bootstrap,
        "_launch_background",
        lambda command, cwd=None: {"pid": 4567, "log": "/tmp/owg.log", "_process": FakeProcess()},
    )

    result = agent_bootstrap._install_if_needed()

    assert result["ok"] is True
    assert result["performed"] is True
    assert result["runtime"][0] == root


def test_install_helper_launches_official_installer_automatically(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    python = root / "python"
    root.mkdir()
    python.write_text("", encoding="utf-8")
    calls = iter([None, (root, python)])

    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: next(calls))
    monkeypatch.setattr(
        connections,
        "_install_instruction",
        lambda: {"platform": "macos", "command": ["/bin/echo", "official-installer"], "human_action": ""},
    )

    class FakeProcess:
        pid = 1234
        def poll(self):
            return None

    monkeypatch.setattr(
        agent_bootstrap,
        "_launch_background",
        lambda command, cwd=None: {"pid": 1234, "log": "/tmp/owg.log", "_process": FakeProcess()},
    )

    result = agent_bootstrap._install_if_needed()

    assert result["ok"] is True
    assert result["performed"] is True
    assert result["runtime"] == (root, python)
    assert result["installer_pid"] == 1234


def test_self_diagnostic_is_read_only_and_refuses_to_guess_environment(home, monkeypatch):
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: None)

    result = connections.setup_self(name="grok")

    assert result["status"] == "environment_required"
    assert result["writes_performed"] is False
    assert result["installed_runtime_found"] is False
    assert result["detected_known_clients"] == []
    assert "--local" in result["instruction"]
    assert "--remote" in result["instruction"]


def test_remote_self_route_never_installs_and_points_to_gateway_mcp(home):
    result = connections.setup_self(remote=True, name="grok")

    assert result["status"] == "needs_gateway"
    assert result["environment"] == "remote"
    assert result["writes_performed"] is False
    assert result["do_not_install_here"] is True
    assert result["remote_mcp"]["transport"] == "streamable_http"
    assert result["remote_mcp"]["endpoint"].endswith("/mcp")
    assert result["remote_mcp"]["authentication"] == "delegated OIDC"
    assert result["repo_alone_is_enough"] is False
    assert "do not install" in result["instruction"].lower()
    assert "client id" in result["instruction"].lower()


def test_unknown_local_agent_gets_generic_descriptor_not_another_clients_config(home, tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    python = tmp_path / "runtime" / "python"
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: (installed, python))
    monkeypatch.setattr(connections, "_setup_access_status", lambda: {
        "changed": False,
        "currently_enabled": False,
        "required": True,
    })

    result = connections.setup_self(local=True, name="Grok Cloud Desktop")

    assert result["status"] == "descriptor"
    assert result["environment"] == "local"
    assert result["integration"] == "generic_local_mcp"
    assert result["client_id"] == "external_grok_cloud_desktop"
    assert result["writes_performed"] is False
    assert result["connection"] == {
        "name": "openworkgraph",
        "transport": "stdio",
        "command": str(python),
        "args": [
            str(installed / "mcp_server" / "launcher.py"),
            "--client",
            "external_grok_cloud_desktop",
        ],
    }
    assert result["observe_changed"] is False
    assert result["ai_access"]["changed"] is False
    assert not (home / ".cursor" / "mcp.json").exists()
    assert not (home / ".codex" / "config.toml").exists()


def test_explicit_local_linux_does_not_offer_mac_or_windows_installer(home, monkeypatch):
    monkeypatch.setattr(connections, "_find_installed_runtime", lambda: None)
    monkeypatch.setattr(connections.sys, "platform", "linux")

    result = connections.setup_self(local=True, name="custom-agent")

    assert result["status"] == "unsupported_local_platform"
    assert result["environment"] == "local"
    assert result["writes_performed"] is False
    assert result["platform"] == "linux"
    assert "macOS and Windows" in result["reason"]


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


def test_fresh_checkout_remote_self_route_needs_no_site_packages(home, tmp_path):
    env = os.environ.copy()
    env.update({
        "OWG_CONNECTIONS_HOME": str(home),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
    })
    run = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "owg_connect.py"),
            "setup",
            "--self",
            "--remote",
            "--name",
            "grok",
        ],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "needs_gateway"
    assert payload["do_not_install_here"] is True
    assert payload["writes_performed"] is False


def test_fresh_checkout_autonomous_remote_bootstrap_needs_no_site_packages(home, tmp_path):
    env = os.environ.copy()
    env.update({
        "OWG_CONNECTIONS_HOME": str(home),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
    })
    run = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "owg_connect.py"),
            "bootstrap",
            "--self",
            "--remote",
            "--name",
            "grok",
        ],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "needs_gateway"
    assert payload["do_not_install_here"] is True
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

    assert "owg_connect.py bootstrap --local --client <client_id>" in agents
    assert "/bin/bash owg_bootstrap.sh --client <client_id>" in agents
    assert "owg_bootstrap.ps1 --client <client_id>" in agents
    assert "owg_connect.py bootstrap --self --local --name <your-agent-name>" in agents
    assert "owg_connect.py bootstrap --self --remote --name <your-agent-name>" in agents
    assert "perform every safe machine step yourself" in agents
    assert "Do not ask the person to clone, download, run terminal commands" in agents
    assert "would observe the wrong machine" in agents
    assert "must **not** ask the person to choose a fake local client id" in agents
    assert "https://owg.your-company.example/mcp" in agents
    assert "Never silently enable Observe" in agents
    assert "get_current_work_context" in agents
    assert "Do **not** call OpenWorkGraph for ordinary coding" in agents
    assert "AGENTS.md" in claude and "AGENTS.md" in gemini


def test_native_bootstrap_wrappers_are_present_and_parseable():
    mac = ROOT / "owg_bootstrap.sh"
    win = ROOT / "owg_bootstrap.ps1"
    assert mac.exists() and win.exists()

    mac_text = mac.read_text(encoding="utf-8")
    win_text = win.read_text(encoding="utf-8")
    assert "install.sh" in mac_text
    assert 'bootstrap --local "$@"' in mac_text
    assert "install.ps1" in win_text
    assert "bootstrap --local @BootstrapArgs" in win_text

    if os.name != "nt":
        checked = subprocess.run(
            ["/bin/bash", "-n", str(mac)],
            capture_output=True, text=True, timeout=30,
        )
        assert checked.returncode == 0, checked.stderr
    else:
        escaped_win = str(win).replace("'", "''")
        command = (
            "$tokens=$null;$errors=$null;"
            f"[System.Management.Automation.Language.Parser]::ParseFile('{escaped_win}',[ref]$tokens,[ref]$errors)|Out-Null;"
            "if($errors.Count){$errors|ForEach-Object{$_.ToString()};exit 1}"
        )
        checked = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
        assert checked.returncode == 0, checked.stderr + checked.stdout


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
