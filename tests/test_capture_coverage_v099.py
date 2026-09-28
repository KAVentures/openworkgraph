from __future__ import annotations

"""Clipboard writes observed from the OS change counter (contents never read)."""

import json

import collector.main as cm
from collector.interactions import RawClipboardAction
from tests.test_capture_correctness_v098 import drive, seconds, spans


def _keyboard(monkeypatch):
    holder: dict = {}

    class FakeKeyboard:
        def __init__(self, callback, *, clipboard_callback=None, capture_clipboard_shortcuts=False):
            holder["clipboard"] = clipboard_callback

        def start(self):
            return True

        def stop(self):
            pass

    monkeypatch.setattr(cm, "KeyboardActivitySensor", FakeKeyboard)
    return holder


def test_menu_copy_is_a_write_shortcut_copy_is_not_counted_twice_and_pastes_link(tmp_path, monkeypatch):
    holder = _keyboard(monkeypatch)
    now = {"t": 0.0}
    fired: set[float] = set()

    def token():
        t = now["t"]
        return 1 if t < 10 else 2 if t < 22 else 3 if t < 40 else 4

    def shortcut(t, kind):
        if t not in fired:
            fired.add(t)
            holder["clipboard"](RawClipboardAction(kind=kind, occurred_mono=cm.time.monotonic()))

    def world(t):
        now["t"] = t
        if t == 20:
            shortcut(t, "copy")   # Cmd+C; the app updates the clipboard at t=22
        if t == 30:
            shortcut(t, "paste")
        if t == 44:
            shortcut(t, "paste")
        return ("Mail", "Inbox", 0.0, False)

    monkeypatch.setattr(cm, "clipboard_change_token", token)
    events, _ = drive(tmp_path, monkeypatch, world, end=60, keyboard=True)
    clip = sorted(spans(events), key=seconds)
    clip = [e for e in clip if e["event_type"].startswith("clipboard_")]
    assert [(e["event_type"], seconds(e)) for e in clip] == [
        ("clipboard_write", 10), ("clipboard_copy", 20), ("clipboard_paste", 30), ("clipboard_write", 40), ("clipboard_paste", 44),
    ]
    write1, copy, paste1, write2, paste2 = clip
    assert write1["metadata"]["evidence_channel"] == "os_clipboard_sequence"
    assert copy["metadata"]["evidence_channel"] == "keyboard_shortcut"
    # Each paste links to the most recent clipboard source, whichever way it was written.
    assert paste1["metadata"]["linked_copy_event_id"] == copy["event_id"]
    assert paste2["metadata"]["linked_copy_event_id"] == write2["event_id"]
    assert all(e["metadata"]["clipboard_contents_captured"] is False for e in clip)
    assert "paste" not in {e["event_type"] for e in clip if e["metadata"]["evidence_channel"] == "os_clipboard_sequence"}


def test_no_writes_are_attributed_while_away_or_when_disabled(tmp_path, monkeypatch):
    _keyboard(monkeypatch)
    now = {"t": 0.0}
    monkeypatch.setattr(cm, "clipboard_change_token", lambda: 1 if now["t"] < 20 else 2)

    def world(t):
        now["t"] = t
        return ("Mail", "Inbox", 400.0 if t < 40 else 0.0, False)  # away until t=40

    events, _ = drive(tmp_path, monkeypatch, world, end=60, keyboard=True)
    assert not [e for e in events if e["event_type"] == "clipboard_write"]

    now["t"] = 0.0
    monkeypatch.setattr(cm, "clipboard_change_token", lambda: 1 if now["t"] < 20 else 2)

    def present(t):
        now["t"] = t
        return ("Mail", "Inbox", 0.0, False)

    events, _ = drive(tmp_path / "off", monkeypatch, present, end=40, keyboard=True, config={"clipboard_write_detection_enabled": False})
    assert not [e for e in events if e["event_type"] == "clipboard_write"]
    assert "Inbox" not in json.dumps([e.get("metadata") for e in events if e["event_type"].startswith("clipboard")])
