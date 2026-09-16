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
