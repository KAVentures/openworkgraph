"""Dashboard-only, default-off, revocable local experimental MCP tool catalog."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import open_work_mcp_settings as settings


def test_default_off_and_local_toggle_persists_without_provider_rights(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    monkeypatch.delenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", raising=False)
    assert settings.experimental_tools_enabled() is False
    state = settings.set_experimental_tools_enabled(True)
    assert state["enabled"] is True
    assert state["existing_source_read_opt_in_still_required"] is True
    assert state["existing_ai_access_still_required"] is True
    assert state["permits_business_actions"] is False
    assert (tmp_path / "open_work_mcp_tools.json").exists()
    if os.name == "posix":
        assert oct((tmp_path / "open_work_mcp_tools.json").stat().st_mode & 0o777) == "0o600"
    assert settings.experimental_tools_enabled() is True
    state = settings.set_experimental_tools_enabled(False)
    assert state["enabled"] is False
    assert settings.experimental_tools_enabled() is False


def test_environment_override_is_respected_and_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    settings.set_experimental_tools_enabled(True)
    monkeypatch.setenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", "0")
    assert settings.experimental_tools_enabled() is False
    assert settings.settings_status()["environment_override_active"] is True
    monkeypatch.setenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", "1")
    assert settings.experimental_tools_enabled() is True
    monkeypatch.delenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS")
    assert settings.experimental_tools_enabled() is True


def test_corrupt_or_world_readable_setting_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    monkeypatch.delenv("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", raising=False)
    p = tmp_path / "open_work_mcp_tools.json"
    p.write_text("invalid json")
    assert settings.experimental_tools_enabled() is False
    p.write_text('{"version":1,"enabled":true}')
    if os.name == "posix":
        p.chmod(0o644)
        assert settings.experimental_tools_enabled() is False


def test_symlink_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    p = tmp_path / "open_work_mcp_tools.json"
    target = tmp_path / "innocent.txt"
    target.write_text("unchanged")
    try:
        p.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported on this runner")
    with pytest.raises(OSError):
        settings.set_experimental_tools_enabled(True)
    assert target.read_text() == "unchanged"


def test_registered_tools_refuse_calls_after_switch_off(monkeypatch):
    from mcp_server import case_context_tools as case, outcome_receipt_tools as receipt

    active = {"enabled": True}
    monkeypatch.setattr(case, "experimental_tools_enabled", lambda: active["enabled"])
    monkeypatch.setattr(receipt, "experimental_tools_enabled", lambda: active["enabled"])

    class StubMCP:
        def __init__(self): self.func = None
        def tool(self, **_kwargs):
            def wrap(fn):
                self.func = fn
                return fn
            return wrap

    calls = []
    runtime = SimpleNamespace(
        authorize_tool=lambda *_args: calls.append("auth"),
        secure_get=lambda *_args: calls.append("provider") or {},
        core=SimpleNamespace(_finish=lambda _n, x: x),
    )
    first, second = StubMCP(), StubMCP()
    case.register_case_context_tools(first, runtime)
    receipt.register_outcome_receipt_tools(second, runtime)
    active["enabled"] = False
    with pytest.raises(Exception, match="switched off"):
        first.func("owg:r:" + "a" * 32)
    with pytest.raises(Exception, match="switched off"):
        second.func("owg:r:" + "a" * 32, "github_pr_merged")
    assert calls == []


def test_local_dashboard_tool_toggle_requires_session(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = """
import server.secure_app
from fastapi.testclient import TestClient
from server.secure_app import app
c = TestClient(app)
for method in ('get', 'post'):
    res = getattr(c, method)('/v1/local-open-work/mcp-tools', **(
        {'json': {'enabled': True}} if method == 'post' else {}))
    assert res.status_code in (401, 403), res.text
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    env.pop("OWG_OPEN_WORK_EXPERIMENTAL_TOOLS", None)
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True,
        timeout=75 if os.name == "nt" else 35,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_local_dashboard_toggle_is_advanced_only():
    script = (Path(__file__).resolve().parents[1] / "dashboard/basic_mode.js").read_text()
    assert "mark($('#pvOpenWorkMcpRow'), true)" in script
    assert "experimentalToggle.onclick" in script
    assert "get_case_context and verify_case_operation" in script
    assert "Reconnect your AI app" in script
