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
    assert 'composedPath' in content
    assert 'target.value' not in content


def test_browser_sensor_registers_navigation_without_user_interaction():
    manifest = json.loads((ROOT / "browser_extension" / "manifest.json").read_text())
    assert "webNavigation" in manifest["permissions"]
    background = (ROOT / "browser_extension" / "background.js").read_text()
    content = (ROOT / "browser_extension" / "content.js").read_text()
    assert "webNavigation.onCommitted" in background
    assert 'sendNavigation("document_start")' in content


def test_navigation_uses_destination_url_directly_for_brief_address_bar_visits():
    background = (ROOT / "browser_extension" / "background.js").read_text()
    assert "webNavigation.onBeforeNavigate" in background
    assert 'emitTab(tab, "navigation_started", {trigger: "tabs.onUpdated"}, changeInfo.url)' in background
    assert '}, details.url);' in background
