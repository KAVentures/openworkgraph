from types import SimpleNamespace

from collector.interactions import RawClipboardAction, classify_clipboard_shortcut
from collector import main as collector_main


def test_clipboard_shortcuts_are_platform_specific_and_minimal():
    assert classify_clipboard_shortcut(
        "c", command_down=True, control_down=False, platform_name="Darwin"
    ) == "copy"
    assert classify_clipboard_shortcut(
        "x", command_down=True, control_down=False, platform_name="Darwin"
    ) == "cut"
    assert classify_clipboard_shortcut(
        "v", command_down=False, control_down=True, platform_name="Windows"
    ) == "paste"

    assert classify_clipboard_shortcut(
        "c", command_down=False, control_down=True, platform_name="Darwin"
    ) is None
    assert classify_clipboard_shortcut(
        "a", command_down=True, control_down=True, platform_name="Darwin"
    ) is None
    assert classify_clipboard_shortcut(
        None, command_down=True, control_down=True, platform_name="Windows"
    ) is None


def test_clipboard_event_contains_behavior_not_contents(monkeypatch):
    monkeypatch.setattr(
        collector_main,
        "active_window",
        lambda: SimpleNamespace(app="Google Chrome", title="OpenWorkGraph - Google Chrome"),
    )
    cfg = {
        "device_id": "device-1",
        "sensor_id": "sensor-1",
        "organization_id": "",
        "actor_id": "",
        "excluded_apps": [],
        "excluded_title_patterns": [],
        "window_title_mode": "full",
    }
    raw = RawClipboardAction(kind="copy", occurred_mono=10.0, clipboard_change_token=42)
    event = collector_main._clipboard_event(
        raw=raw,
        cfg=cfg,
        session_id="session-1",
        event_id="event-1",
        transfer_id="transfer-1",
    )

    assert event["event_type"] == "clipboard_copy"
    assert event["app"] == "Google Chrome"
    assert event["metadata"]["action"] == "copy"
    assert event["metadata"]["clipboard_transfer_id"] == "transfer-1"
    assert event["metadata"]["clipboard_change_token"] == 42
    assert event["metadata"]["clipboard_contents_captured"] is False
    assert event["metadata"]["privacy"]["clipboard_contents"] is False
    assert "clipboard_contents" not in event
    assert "text" not in event["metadata"]
