from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_normal_launcher_starts_http_mcp_without_replacing_observer():
    source = _read("start.py")
    assert 'MCP_PORT = 8788' in source
    assert '"-m", "mcp_server.main"' in source
    assert 'mcp_env["MCP_TRANSPORT"] = "streamable-http"' in source
    assert 'stop_process(mcp_process)' in source
    # The existing observer processes remain present.
    assert '"-m", "uvicorn", "server.main:app"' in source
    assert '"-m", "collector.main"' in source


def test_mcp_is_rich_evidence_first_and_keeps_security_boundary():
    source = _read("mcp_server/main.py")
    assert '"/v1/events"' in source
    assert '"/v1/semantic-activity"' in source
    assert 'f"/v1/sessions/{session_id}"' in source
    assert '"raw_local_evidence"' in source
    assert '"data_layer": "rich_ai_context"' in source
    assert 'return _return_observed(' in source
    assert 'raw evidence is not exposed through MCP by default' not in source.lower()


def test_dashboard_keeps_existing_observer_sections_and_adds_top_actions():
    html = _read("dashboard/index.html")
    # Existing data/functionality remains available.
    for element_id in (
        "events", "engaged", "idle", "keys", "interactions", "browserActions",
        "taskCandidates", "apps", "appsTable", "transitionsTable", "taskTable",
        "patternTable", "browserTable", "seqTable", "interactionTable",
        "includeRaw", "privacyResetStatus",
    ):
        assert f'id="{element_id}"' in html
    assert "Reset learned person aliases" in html
    assert "Export captured session" in html

    # New onboarding and top-level controls.
    assert 'id="includeRawTop"' in html
    assert "Connect your AI" in html
    assert "Quick help" in html
    assert "Add to Cursor" in html
    assert "Connect Claude" in html
    assert "Set up ChatGPT" in html
    assert "Other MCP app" in html


def test_cursor_deeplink_and_guided_cloud_paths_are_explicit():
    html = _read("dashboard/index.html")
    assert "cursor://anysphere.cursor-deeplink/mcp/install" in html
    assert "http://127.0.0.1:8788/mcp" in html
    assert "mcp_server.main" in html
    assert "Secure MCP Tunnel" in html
    assert "help.openai.com/en/articles/12584461" in html
    assert "support.claude.com/en/articles/10949351" in html


def test_raw_export_toggle_is_synchronized_top_and_bottom():
    html = _read("dashboard/index.html")
    assert "function syncRawToggle(source)" in html
    assert "function rawEnabled()" in html
    assert "include_raw=${raw}" in html
