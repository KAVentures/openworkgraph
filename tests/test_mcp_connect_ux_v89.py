from __future__ import annotations

import io
import os
import sys
import types
import zipfile

from mcp_server import launcher
from server.mcp_bundle import claude_mcpb_bytes
from server.mcp_connection import stdio_connection_config


def test_connection_config_points_to_openworkgraph_launcher_without_secrets():
    cfg = stdio_connection_config()
    assert cfg["transport"] == "stdio"
    assert cfg["command"] == sys.executable
    assert len(cfg["args"]) == 1
    assert cfg["args"][0].endswith("mcp_server/launcher.py") or cfg["args"][0].endswith("mcp_server\\launcher.py")
    assert cfg["env"] == {}
    encoded = repr(cfg)
    assert "Authorization" not in encoded
    assert "Bearer " not in encoded


def test_launcher_is_independent_of_callers_working_directory(tmp_path, monkeypatch):
    calls = []

    class FakeMcp:
        def run(self, *, transport):
            calls.append((transport, os.getcwd(), os.environ.get("WORKFLOW_OBSERVER_API"), os.environ.get("WORKFLOW_OBSERVER_AUTH_DIR")))

    fake = types.SimpleNamespace(mcp=FakeMcp())
    monkeypatch.setitem(sys.modules, "mcp_server.compact_stdio", fake)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WORKFLOW_OBSERVER_API", raising=False)
    monkeypatch.delenv("WORKFLOW_OBSERVER_AUTH_DIR", raising=False)

    launcher.main()

    assert calls
    transport, cwd, api, auth = calls[0]
    assert transport == "stdio"
    assert cwd == str(launcher.ROOT)
    assert api == "http://127.0.0.1:8787"
    assert auth == str(launcher.ROOT / "data" / "auth")


def test_local_claude_bundle_contains_manifest_and_wrapper():
    payload = claude_mcpb_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert {"manifest.json", "server/index.js"} <= set(zf.namelist())
        wrapper = zf.read("server/index.js").decode("utf-8")
    assert "OpenWorkGraph" in wrapper
