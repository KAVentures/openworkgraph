from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_normal_launcher_starts_http_mcp_without_replacing_observer():
    source = _read("start.py")
    assert 'MCP_PORT = 8788' in source
    assert '"mcp_server.http_app:app"' in source
    assert 'stop_process(mcp_process)' in source
    # The same observer remains present underneath authenticated wrappers.
    assert '"server.secure_app:app"' in source
    assert '"-m", "collector.secure_main"' in source
    assert 'write_browser_pairing_bundle' in source


def test_streamable_http_mcp_process_really_starts(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_API": "http://127.0.0.1:65534",
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mcp_server.http_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = False
    try:
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    ready = True
                    break
            except OSError:
                time.sleep(0.1)
        if not ready:
            stdout, stderr = process.communicate(timeout=2) if process.poll() is not None else ("", "")
            raise AssertionError(f"MCP HTTP server did not start on port {port}. stdout={stdout!r} stderr={stderr!r}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)


def test_mcp_is_rich_evidence_first_and_keeps_security_boundary():
    source = _read("mcp_server/main.py")
    assert '"/v1/events"' in source
    assert '"/v1/semantic-activity"' in source
    assert 'f"/v1/sessions/{session_id}"' in source
    assert '"raw_local_evidence"' in source
    assert '"data_layer": "rich_ai_context"' in source
    assert 'return _return_observed(' in source
    assert 'raw evidence is not exposed through MCP by default' not in source.lower()
    assert "mcp_bearer_matches" in _read("mcp_server/http_app.py")
    assert "ensure_api_token" in _read("mcp_server/secure_runtime.py")


def test_dashboard_keeps_existing_observer_sections_and_adds_top_actions():
    html = _read("dashboard/index.html")
    for element_id in (
        "events", "engaged", "idle", "keys", "interactions", "browserActions",
        "taskCandidates", "apps", "appsTable", "transitionsTable", "taskTable",
        "patternTable", "browserTable", "seqTable", "interactionTable",
        "includeRaw", "privacyResetStatus",
    ):
        assert f'id="{element_id}"' in html
    assert "Reset learned person aliases" in html
    assert "Export captured session" in html
    assert 'id="includeRawTop"' in html
    assert "Connect your AI" in html
    assert "Quick help" in html
    assert "Add to Cursor" in html
    assert "Connect Claude" in html
    assert "Set up ChatGPT" in html
    assert "Other MCP app" in html


def test_cursor_and_claude_connections_are_security_wrapped():
    html = _read("dashboard/index.html")
    secure = _read("server/secure_app.py")
    assert "cursor://anysphere.cursor-deeplink/mcp/install" in secure
    assert "Authorization:`Bearer ${c.token}`" in secure
    assert "mcp_server.secure_stdio" in secure
    assert "Secure MCP Tunnel" in secure
    assert "help.openai.com/en/articles/12584461" in secure
    assert "support.claude.com/en/articles/10949351" in html


def test_raw_export_toggle_is_synchronized_top_and_bottom():
    html = _read("dashboard/index.html")
    assert "function syncRawToggle(source)" in html
    assert "function rawEnabled()" in html
    assert "include_raw=${raw}" in html
