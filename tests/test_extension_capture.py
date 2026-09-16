from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_browser_sensor_is_event_driven_and_frame_aware():
    manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text())
    cs = manifest["content_scripts"][0]
    assert cs["all_frames"] is True
    assert cs["match_about_blank"] is True
    content = (ROOT / "browser_extension" / "content.js").read_text()
    assert 'addEventListener("pointerdown"' in content
    assert 'addEventListener("focusin"' in content
    assert "composedPath" in content
    assert "target.value" not in content


def test_browser_sensor_registers_navigation_without_user_interaction():
    manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text())
    assert "webNavigation" in manifest["permissions"]
    background = (ROOT / "browser_extension" / "background.js").read_text()
    content = (ROOT / "browser_extension" / "content.js").read_text()
    assert "webNavigation.onBeforeNavigate" in background
    assert "webNavigation.onCommitted" in background
    assert "webNavigation.onCompleted" in background
    assert 'sendNavigation("document_start")' in content


def test_navigation_uses_destination_url_directly_for_brief_address_bar_visits():
    background = (ROOT / "browser_extension" / "background.js").read_text()
    assert "changeInfo.url || (changeInfo.status === \"loading\" ? tab?.url : null)" in background
    assert "details.url" in background
    assert '"navigation_requested"' in background
    assert '"navigation_committed"' in background
    assert '"navigation_completed"' in background


def test_browser_delivery_is_durable_and_idempotent():
    manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text())
    assert "storage" in manifest["permissions"]
    assert "alarms" in manifest["permissions"]
    background = (ROOT / "browser_extension" / "background.js").read_text()
    assert "openworkgraph_pending_browser_events" in background
    assert "event_id: body.event_id || uuid()" in background
    assert "flushBrowserQueue" in background
    assert "sensor_version" in background
    assert manifest["version"] == "1.7.0"


def test_queued_browser_event_keeps_capture_time_work_identity():
    background = (ROOT / "browser_extension" / "background.js").read_text()
    server = (ROOT / "server" / "main.py").read_text()
    assert 'getJson("/v1/browser-context")' in background
    assert "work_session_id: body.work_session_id ?? work.work_session_id" in background
    assert '@app.get("/v1/browser-context")' in server
    assert "event.work_session_id or COLLECTOR_STATUS.get(\"session_id\")" in server
