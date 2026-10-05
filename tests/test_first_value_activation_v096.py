from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "dashboard" / "first_value_activation.js"
SERVER = ROOT / "server" / "first_value_activation.py"
RUNNER = ROOT / "server" / "enterprise_runner.py"


def test_first_value_layer_is_additive_and_loaded_by_desktop_runner():
    server = SERVER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    assert 'from .secure_app import app' in server
    assert '/first-value-activation.js' in server
    assert 'server.first_value_activation' in runner
    assert 'SCRIPT_TAG + "\\n</body>"' in server


def test_activation_layer_reads_existing_surfaces_only():
    js = JS.read_text(encoding="utf-8")
    for endpoint in (
        "/v1/summary?scope=current&limit=500",
        "/v1/agent-execution-traces?limit=10&evidence_limit=3000&max_events_per_execution=20",
        "/v1/ai-access",
    ):
        assert endpoint in js
    # First-value activation must not become a second control plane. All writes
    # remain behind the established History/Connections/Organization surfaces.
    for mutation in ("method:'POST'", 'method:"POST"', "method:'PUT'", 'method:"PUT"', "method:'DELETE'", 'method:"DELETE"'):
        assert mutation not in js
    assert "fetch(url,{cache:'no-store'})" in js


def test_activation_layer_adds_no_capture_or_content_sensor():
    js = JS.read_text(encoding="utf-8").lower()
    forbidden_apis = (
        "getdisplaymedia(",
        "getusermedia(",
        "filesystemobserver",
        "clipboard.read",
        "clipboard.readtext",
        "mutationobserver(",
    )
    for marker in forbidden_apis:
        assert marker not in js
    # The layer may explain these privacy boundaries in UI copy, but it must not
    # define content-bearing telemetry fields or write them to a new endpoint.
    assert "/agent-ingest/" not in js
    assert "/v1/events" not in js
    assert "/v1/context-events" not in js


def test_activation_never_auto_enables_ai_or_retention():
    js = JS.read_text(encoding="utf-8")
    assert "/v1/history-policy" not in js
    assert "/v1/history/ai-access" not in js
    assert "set_ai_access" not in js
    assert "Connect AI" in js
    assert "Connecting an AI is optional and does not change capture or retention" in js


def test_existing_mcp_and_browser_permissions_are_untouched_by_activation_layer():
    # These files are read, not modified, by this feature. The assertions make
    # the intended v0.96 boundary explicit so a future activation edit cannot
    # quietly grow browser privileges or replace MCP with an onboarding API.
    manifest_text = (ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["permissions"] == ["tabs", "webNavigation", "storage", "alarms"]
    assert "first_value_activation" not in manifest_text
    compact = (ROOT / "mcp_server" / "compact_stdio.py").read_text(encoding="utf-8")
    legacy = (ROOT / "mcp_server" / "secure_stdio.py").read_text(encoding="utf-8")
    assert "first_value_activation" not in compact
    assert "first_value_activation" not in legacy


def test_timeline_and_workflow_cards_are_never_hidden_by_the_first_value_layer():
    # v0.117: hiding the timeline card before the timeline loaded hid it for
    # good. The cards now show their own loading and empty states instead.
    js = JS.read_text(encoding="utf-8")
    assert "style.display='none'" not in js
    assert "Timeline lanes will activate" not in js
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Timeline lanes will activate" not in html
    assert 'id="timelineCard"' in html and "Loading your timeline" in html
    for tab in ("overview", "evidence", "connect", "export", "settings", "organization"):
        assert f'data-tab="{tab}"' in html
