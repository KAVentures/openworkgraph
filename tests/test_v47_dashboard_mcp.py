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


def test_normal_launcher_is_stdio_first_without_replacing_observer():
    source = _read("start.py")
    assert '"server.secure_app:app"' in source
    assert '"-m", "collector.secure_main"' in source
    assert "write_browser_pairing_bundle" in source
    assert "MCP_PORT = 8788" not in source
    assert '"mcp_server.http_app:app"' not in source
    assert "start_local_mcp" not in source
    assert "HTTP MCP is OFF by default" in source
    optional = _read("server/mcp_http_control.py")
    assert '"mcp_server.http_app:app"' in optional
    assert 'f"http://{HOST}:{port}/mcp"' in optional


def test_streamable_http_mcp_process_still_available_for_on_demand_clients(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_API": "http://127.0.0.1:65534",
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mcp_server.http_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ready = False
    try:
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    ready = True; break
            except OSError:
                time.sleep(0.1)
        if not ready:
            stdout, stderr = process.communicate(timeout=2) if process.poll() is not None else ("", "")
            raise AssertionError(f"MCP HTTP server did not start on port {port}. stdout={stdout!r} stderr={stderr!r}")
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=4)


def test_mcp_is_compact_rich_evidence_first_and_keeps_security_boundary():
    source = _read("mcp_server/main.py")
    assert "def get_workflow_trace(" in source
    assert '"/v1/workflow-trace"' in source
    assert "openworkgraph://ai-guide" in source
    assert "AI_DATA_DICTIONARY_MD" in source
    assert "rich_ai_context_compact" in source
    assert "raw_local_evidence" not in source
    assert "protect_observed_payload" in source
    assert "mcp_bearer_matches" in _read("mcp_server/http_app.py")
    secure = _read("mcp_server/secure_runtime.py")
    assert "ensure_api_token" in secure
    assert 'secure_get("/v1/ai-access")' in secure
    assert '"status": "denied"' in secure


def test_dashboard_data_first_layout_keeps_required_observer_controls():
    html = _read("dashboard/index.html")
    for element_id in (
        "engaged", "idle", "keys", "surfaceCount", "completedRuns",
        "evidenceTable", "transitionsTable", "seqTable", "patternList",
        "includeRaw", "privacyResetStatus", "aiAccessPanel", "gatewayPanel",
    ):
        assert f'id="{element_id}"' in html
    for panel in ("overview", "evidence", "connect", "organization", "export"):
        assert f'data-panel="{panel}"' in html
    assert "Reset learned person aliases" in html
    assert "Export this run" in html
    assert 'id="includeRawTop"' not in html
    assert "Connect AI" in html
    assert "Add to Cursor" in html
    assert "Connect Claude" in html
    assert "Set up ChatGPT" in html
    assert "Other MCP app" in html
    assert "See how work actually happens" not in html


def test_cursor_and_claude_connections_use_stdio_and_chatgpt_http_is_on_demand():
    html = _read("dashboard/index.html")
    secure = _read("server/secure_app.py")
    assert "cursor://anysphere.cursor-deeplink/mcp/install" in secure
    assert '"transport": "stdio"' in secure
    assert '"-m", "mcp_server.secure_stdio"' in secure
    assert "Authorization:`Bearer ${c.token}`" not in secure.split("window.connectCursor", 1)[1].split("window.showBrowserPairingCode", 1)[0]
    assert "httpMcp('start')" in secure
    assert "Secure MCP Tunnel" in secure
    assert "help.openai.com/en/articles/12584461" in secure
    assert "support.claude.com/en/articles/10949351" in secure
    assert "AI access" in secure
    assert "Recent AI activity" in secure
    assert "Connect Claude" in html


def test_raw_export_toggle_is_single_source_in_export_tab():
    html = _read("dashboard/index.html")
    assert "function syncRawToggle(source)" in html
    assert "function rawEnabled()" in html
    assert "include_raw=${raw}" in html
    assert html.count('id="includeRaw"') == 1
    assert 'id="includeRawTop"' not in html
