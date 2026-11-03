"""Opt-in full screen capture: every monitor, native resolution, optional OCR."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from collector import main as collector
from collector import screen_capture
from server import screen_capture_settings as settings

ROOT = Path(__file__).resolve().parents[1]


class _Shot:
    def __init__(self, width, height):
        self.size = (width, height)
        self.rgb = bytes([200, 30, 30]) * (width * height)


class _Grabber:
    def __init__(self, sizes):
        self.monitors = [{"left": 0, "top": 0}] + [{"size": size} for size in sizes]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, monitor):
        return _Shot(*monitor["size"])


def test_full_capture_saves_every_monitor_at_native_resolution(tmp_path):
    manifest = screen_capture.capture_all_monitors("cap-1", tmp_path, grabber=_Grabber([(64, 40), (32, 20)]))
    assert manifest["monitor_count"] == 2
    assert [(image["width"], image["height"]) for image in manifest["images"]] == [(64, 40), (32, 20)]
    from PIL import Image
    assert Image.open(manifest["images"][0]["path"]).size == (64, 40)
    assert manifest["ocr_status"] == "disabled" and manifest["text_path"] is None
    assert json.loads((tmp_path / "cap-1.json").read_text())["stored"] == "local_only"


def test_ocr_text_is_stored_locally_and_never_in_event_metadata(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract",
                        types.SimpleNamespace(image_to_string=lambda image: "Invoice 4411 overdue"))
    manifest = screen_capture.capture_all_monitors("cap-2", tmp_path, ocr=True, grabber=_Grabber([(16, 16)]))
    assert manifest["ocr_status"] == "recognized"
    assert "Invoice 4411 overdue" in Path(manifest["text_path"]).read_text()
    summary = screen_capture.event_summary(manifest)
    assert summary["screen_capture"]["text_captured"] is True
    text = json.dumps(summary)
    assert "Invoice" not in text and str(tmp_path) not in text


def test_missing_ocr_engine_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    manifest = screen_capture.capture_all_monitors("cap-3", tmp_path, ocr=True, grabber=_Grabber([(8, 8)]))
    assert manifest["ocr_status"] == "unavailable" and manifest["text_characters"] == 0


def test_capture_failure_returns_nothing(tmp_path):
    class Broken(_Grabber):
        def grab(self, monitor):
            raise OSError("no display")
    assert screen_capture.capture_all_monitors("cap-4", tmp_path, grabber=Broken([(8, 8)])) is None


def test_mode_defaults_off_and_rejects_unknown_values():
    assert collector.load_config(Path("/nonexistent/config.json"))["screen_capture_mode"] == "off"
    assert screen_capture.mode({"screen_capture_mode": "everything"}) == "off"
    assert screen_capture.interval_seconds({"screenshot_interval_seconds": 1}) == screen_capture.MIN_INTERVAL_SECONDS


def test_collector_screenshot_uses_full_capture_only_in_full_mode(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(screen_capture, "capture_all_monitors", lambda event_id, directory, ocr=False: calls.append(
        (event_id, ocr)) or {"event_id": event_id, "monitor_count": 2, "ocr_status": "disabled", "text_characters": 0,
                             "images": [{"path": str(tmp_path / "a.jpg")}]})
    path = collector._capture("ev-1", {"screen_capture_mode": "full", "screen_text_ocr_enabled": True})
    assert path == str(tmp_path / "a.jpg") and calls == [("ev-1", True)]
    assert collector.take_screen_manifest("ev-1")["monitor_count"] == 2
    assert collector.take_screen_manifest("ev-1") is None
    legacy = []
    monkeypatch.setattr(collector, "screenshot", lambda event_id: legacy.append(event_id))
    collector._capture("ev-2", {"screen_capture_mode": "off"})
    assert legacy == ["ev-2"] and len(calls) == 1


def test_focus_span_reports_capture_counts_without_paths():
    captures = [{"event_id": f"c{i}", "monitor_count": 2, "ocr_status": "disabled", "text_characters": 0}
                for i in range(3)]
    event = collector._focus_span_event(
        state={"app": "Excel", "window_title": "Q3", "excluded": False}, started_at="2026-11-03T10:00:00+00:00",
        duration_seconds=60, cfg={}, session_id="s", screen_captures=captures)
    capture = event["metadata"]["screen_capture"]
    assert capture["capture_count"] == 3 and capture["capture_ids"] == ["c0", "c1", "c2"]
    assert capture["monitor_count"] == 2 and capture["stored"] == "local_only"
    plain = collector._focus_span_event(
        state={"app": "Excel", "window_title": "Q3", "excluded": False}, started_at="2026-11-03T10:00:00+00:00",
        duration_seconds=60, cfg={}, session_id="s")
    assert "screen_capture" not in plain["metadata"]


def test_turning_full_capture_off_hot_reloads(tmp_path):
    config = tmp_path / "config.json"
    cfg = {"screen_capture_mode": "full", "screen_text_ocr_enabled": True}
    config.write_text(json.dumps({"screen_capture_mode": "off"}), encoding="utf-8")
    assert collector.refresh_live_exclusions(config, cfg, {}) is True
    assert cfg["screen_capture_mode"] == "off" and cfg["screen_text_ocr_enabled"] is False


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"device_id": "keep-me"}), encoding="utf-8")
    monkeypatch.setattr(settings, "_config_path", lambda: path)
    return path


def test_enabling_full_capture_requires_acknowledgement(config_file):
    assert settings.current()["mode"] == "off"
    with pytest.raises(PermissionError):
        settings.update({"mode": "full"})
    assert settings.current()["mode"] == "off"
    on = settings.update({"mode": "full", "acknowledge": settings.ACK_VERSION, "ocr_enabled": True,
                          "interval_seconds": 30})
    assert on["mode"] == "full" and on["ocr_enabled"] is True and on["interval_seconds"] == 30
    saved = json.loads(config_file.read_text())
    assert saved["device_id"] == "keep-me" and saved["screen_capture_mode"] == "full"
    assert settings.update({"mode": "off"})["mode"] == "off"


@pytest.mark.parametrize("payload", [{"mode": "everything"}, {"ocr_enabled": "yes"},
                                     {"interval_seconds": 1}, {"interval_seconds": True}, []])
def test_invalid_settings_are_rejected(config_file, payload):
    with pytest.raises(ValueError):
        settings.update(payload)


def test_privacy_tab_offers_screen_capture_switches():
    script = (ROOT / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")
    assert 'id="pvScreenCaptureSwitch"' in script and 'id="pvScreenOcrSwitch"' in script
    assert "/v1/screen-capture" in script
    routes = (ROOT / "server" / "basic_mode_routes.py").read_text(encoding="utf-8")
    assert '"/v1/screen-capture", get_screen_capture' in routes and '"/v1/screen-capture", put_screen_capture' in routes
