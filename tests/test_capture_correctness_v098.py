from __future__ import annotations

"""Capture correctness: one collector, away time, document boundaries, honest health.

The collector loop runs for real against a scripted clock, foreground window and
OS input-idle clock, so these tests check the evidence it actually emits.
"""

import json
import subprocess
import sys
import textwrap
import time as real_time
from datetime import datetime as real_datetime, timedelta, timezone
from pathlib import Path

import pytest

import collector.main as cm
from collector.boundaries import document_key, is_material_change
from collector.instance_lock import EXIT_ALREADY_RUNNING, CollectorLock
from collector.platform import ActiveWindow
from connector.policy import prepare_event_for_gateway

T0 = real_datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, end: float) -> None:
        self.t = 0.0
        self.end = end

    def monotonic(self) -> float:
        return 1000.0 + self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        if self.t >= self.end:
            cm.STOP = True


def drive(tmp_path, monkeypatch, world, *, end: float, permissions=None, config=None, keyboard=False):
    """Run the real collector loop; ``world(t)`` returns (app, title, idle_seconds, locked)."""
    clock = Clock(end)

    class FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return T0 + timedelta(seconds=clock.t)

    fake_time = type("T", (), {"monotonic": staticmethod(clock.monotonic), "sleep": staticmethod(clock.sleep), "time": staticmethod(real_time.time)})
    events: list[dict] = []
    heartbeats: list[dict] = []
    monkeypatch.setattr(cm, "LOCAL_DIR", tmp_path)
    monkeypatch.setattr(cm, "time", fake_time)
    monkeypatch.setattr(cm, "datetime", FakeDatetime)
    monkeypatch.setattr(cm, "persist_event", lambda event, _outbox: events.append(event))
    monkeypatch.setattr(cm, "post_heartbeat", lambda status, _url: heartbeats.append(status))
    monkeypatch.setattr(cm, "active_window", lambda: ActiveWindow(*world(clock.t)[:2]))
    monkeypatch.setattr(cm, "system_idle_seconds", lambda: world(clock.t)[2])
    monkeypatch.setattr(cm, "screen_locked", lambda: world(clock.t)[3])
    monkeypatch.setattr(cm, "sensor_permissions", lambda: dict(permissions or {}))
    if keyboard:
        monkeypatch.setattr(cm.KeyboardActivitySensor, "start", lambda self: True)
        monkeypatch.setattr(cm.KeyboardActivitySensor, "stop", lambda self: None)
    cfg = {
        "backend_url": "http://127.0.0.1:9",
        "poll_seconds": 2,
        "screen_interactions_enabled": False,
        "keyboard_activity_enabled": keyboard,
        "excluded_title_patterns": [],
        **(config or {}),
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert cm.run(path) == 0
    return events, heartbeats


def spans(events, kind=None):
    return [e for e in events if kind is None or e["event_type"] == kind]


def seconds(event) -> float:
    return (real_datetime.fromisoformat(event["observed_at"]) - T0).total_seconds()


def assert_no_overlap_and_no_hole(events, end):
    timed = sorted((seconds(e), seconds(e) + e["duration_seconds"]) for e in events if e["event_type"] in {"focus_span", "away_span"})
    for (a1, b1), (a2, b2) in zip(timed, timed[1:]):
        assert a2 == pytest.approx(b1), (a1, b1, a2, b2)
    assert timed[0][0] == 0 and timed[-1][1] == pytest.approx(end)


# --- away ----------------------------------------------------------------------------------------

def test_idle_becomes_an_away_span_and_duration_keeps_wall_clock_meaning(tmp_path, monkeypatch):
    last_input = {"t": 0.0}

    def world(t):
        if t <= 150 or t >= 900:
            last_input["t"] = t  # typing
        return ("Microsoft Word", "Report.docx", t - last_input["t"], False)

    events, heartbeats = drive(tmp_path, monkeypatch, world, end=1000)
    away = spans(events, "away_span")
    # Away starts once 300 s passed without input (the first 300 s stay with Word
    # as reading/thinking time) and ends when input returns.
    assert [(seconds(e), e["duration_seconds"]) for e in away][0] == (450, 120)  # checkpointed
    assert sum(e["duration_seconds"] for e in away) == pytest.approx(450)
    assert {e["metadata"]["reason"] for e in away} == {"no_input"}
    assert away[0]["metadata"]["idle_source"] == "os_input_clock" and away[0]["app"] == "Away"
    focus = spans(events, "focus_span")
    assert any(e["metadata"]["focus_boundary"] == "away" and seconds(e) + e["duration_seconds"] == 450 for e in focus)
    assert seconds([e for e in focus if seconds(e) >= 900][0]) == 900  # Word resumes with input
    assert_no_overlap_and_no_hole(events, 1000)
    assert any(h["app"] == "Away" and h["activity"]["away"] for h in heartbeats)


def test_screen_lock_is_away_immediately(tmp_path, monkeypatch):
    events, _ = drive(tmp_path, monkeypatch, lambda t: ("Mail", "Inbox", 0.0, 100 <= t < 200), end=300)
    away = spans(events, "away_span")
    assert [(seconds(e), e["duration_seconds"], e["metadata"]["reason"]) for e in away] == [(100, 100, "screen_locked")]
    assert_no_overlap_and_no_hole(events, 300)


def test_without_any_idle_signal_it_never_guesses_away(tmp_path, monkeypatch):
    # No OS idle clock (Linux today) and no working input sensors: long spans stay
    # focus spans, as before, rather than inventing absences.
    events, _ = drive(tmp_path, monkeypatch, lambda t: ("Terminal", "zsh", None, None), end=900)
    assert not spans(events, "away_span")
    assert sum(e["duration_seconds"] for e in spans(events, "focus_span")) == pytest.approx(900)


def test_away_spans_never_leave_the_computer():
    away = {"event_type": "away_span", "app": "Away", "metadata": {}, "event_id": "x"}
    assert prepare_event_for_gateway(away, {"allowed_event_types": []}) is None
    assert prepare_event_for_gateway(away, {"allowed_event_types": ["away_span"]}) is None
    focus = {"event_type": "focus_span", "app": "Word", "metadata": {}, "event_id": "y"}
    assert prepare_event_for_gateway(focus, {"allowed_event_types": []}) is not None


# --- same-app documents ------------------------------------------------------------------------

def test_document_switch_in_same_app_is_a_debounced_boundary(tmp_path, monkeypatch):
    def world(t):
        if t < 60:
            title = "Report.docx"
        elif t < 80:
            title = "(2) Report.docx — Edited"  # noise: unread badge + edited marker
        elif t in (80.0, 82.0):
            title = "Budget.docx"  # a 1-poll flicker must not split... (confirmed on 2nd poll)
        elif t < 100:
            title = "Budget.docx"
        elif t == 100:
            title = "Save As"  # a one-poll dialog: no boundary
        else:
            title = "Budget.docx"
        return ("Microsoft Word", title, 0.0, False)

    events, _ = drive(tmp_path, monkeypatch, world, end=140)
    focus = spans(events, "focus_span")
    assert [(seconds(e), e["window_title"], e["metadata"]["focus_boundary"]) for e in focus] == [
        (0, "Report.docx", "document_change"),
        (80, "Budget.docx", "shutdown"),  # boundary backdated to when Budget.docx first appeared
    ]
    assert_no_overlap_and_no_hole(events, 140)


def test_old_default_value_gets_document_boundaries_and_app_only_is_opt_in(tmp_path, monkeypatch):
    world = lambda t: ("Word", "A.docx" if t < 50 else "B.docx", 0.0, False)
    # "application" was copied into every config.json as the old default.
    events, _ = drive(tmp_path, monkeypatch, world, end=100, config={"change_detection": "application"})
    assert [e["window_title"] for e in spans(events, "focus_span")] == ["A.docx", "B.docx"]
    events, _ = drive(tmp_path / "only", monkeypatch, world, end=100, config={"change_detection": "application_only"})
    assert [e["window_title"] for e in spans(events, "focus_span")] == ["A.docx"]


def test_example_config_ships_the_new_defaults():
    example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text())
    assert example["change_detection"] == "application_and_document"
    assert example["away_detection_enabled"] is True and example["away_after_seconds"] == 300


def test_document_key_ignores_noise_but_not_real_changes():
    assert document_key("(3) Inbox - Outlook") == document_key("Inbox - Outlook")
    assert document_key("● main.py — owg") == document_key("main.py — owg")
    assert document_key("Budget.xlsx - Edited") == document_key("Budget.xlsx")
    assert document_key("Mötesanteckningar — Redigerad") == document_key("Mötesanteckningar")
    assert document_key("Q3 plan.docx (Not Responding)") == document_key("Q3 plan.docx")
    assert document_key("Report.docx") != document_key("Budget.docx")
    assert not is_material_change("report.docx", "")  # a title going empty is not a new document


def test_title_privacy_modes_are_respected(tmp_path, monkeypatch):
    world = lambda t: ("Word", "A.docx" if t < 50 else ("(3) B.docx" if t < 70 else "B.docx"), 0.0, False)
    # "none": no title information is recorded, so none is used for boundaries.
    events, _ = drive(tmp_path, monkeypatch, world, end=100, config={"window_title_mode": "none"})
    focus = spans(events, "focus_span")
    assert len(focus) == 1 and focus[0]["window_title"] == ""
    # "hash": only hashes are stored; the badge change is not a new document.
    events, _ = drive(tmp_path / "h", monkeypatch, world, end=100, config={"window_title_mode": "hash"})
    focus = spans(events, "focus_span")
    assert len(focus) == 2 and all(len(e["window_title"]) == 16 for e in focus)
    assert "document_key" not in json.dumps(events)


# --- health --------------------------------------------------------------------------------------

def test_routine_checkpoints_do_not_produce_health_evidence(tmp_path, monkeypatch):
    events, heartbeats = drive(tmp_path, monkeypatch, lambda t: ("Code", "main.py", 0.0, False), end=3600)
    assert not spans(events, "capture_health")
    assert len([e for e in spans(events, "focus_span") if e["metadata"]["focus_boundary"] == "periodic_checkpoint"]) == 29
    assert heartbeats[-1]["activity"]["diagnostics"]["focus_checkpoint_count"] == 29


def test_real_degradation_is_reported_once_then_rate_limited(tmp_path, monkeypatch):
    # The foreground window is unreadable for a stretch (e.g. automation denied).
    events, _ = drive(tmp_path, monkeypatch, lambda t: ("Unknown" if 100 <= t < 1500 else "Code", "", 0.0, False), end=1600)
    health = spans(events, "capture_health")
    assert 1 <= len(health) <= 4
    first = health[0]["metadata"]
    assert first["capture_health"]["active_window_unavailable"] >= 1
    assert "focus_checkpoint_count" not in first["capture_health"] and "focus_checkpoint_count" in first["diagnostics"]


def test_missing_macos_permission_is_stated_not_hidden(tmp_path, monkeypatch, capsys):
    events, heartbeats = drive(
        tmp_path, monkeypatch, lambda t: ("Code", "main.py", 0.0, False), end=20,
        permissions={"accessibility": True, "input_monitoring": False}, keyboard=True,
    )
    out = capsys.readouterr().out
    assert "Keyboard activity: BLOCKED (macOS input monitoring permission not granted" in out
    assert "Keyboard activity: ON" not in out
    assert "Window titles and UI labels: ON" in out
    assert heartbeats[0]["keyboard_sensor"] is False
    assert heartbeats[0]["activity"]["permissions"] == {"accessibility": True, "input_monitoring": False}
    health = spans(events, "capture_health")
    assert health and health[0]["metadata"]["missing_permissions"] == ["input_monitoring"]


# --- one collector -----------------------------------------------------------------------------

def test_second_collector_for_the_same_data_folder_does_not_start(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cm, "LOCAL_DIR", tmp_path)
    holder = CollectorLock(tmp_path)
    assert holder.acquire()
    try:
        assert cm.run(tmp_path / "config.json") == EXIT_ALREADY_RUNNING
        assert "already recording" in capsys.readouterr().out
        other = CollectorLock(tmp_path / "demo")
        assert other.acquire()  # a different data folder (demo) is independent
        other.release()
    finally:
        holder.release()
    again = CollectorLock(tmp_path)
    assert again.acquire()
    again.release()


def test_lock_is_released_when_the_holder_crashes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(root)!r})
        from collector.instance_lock import CollectorLock
        held = CollectorLock({str(tmp_path)!r})
        assert held.acquire()
        print("locked", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
        assert not CollectorLock(tmp_path).acquire()
    finally:
        proc.kill()
        proc.wait(timeout=10)
    lock = CollectorLock(tmp_path)
    assert lock.acquire(), "a crashed holder must not leave a stale lock"
    lock.release()


def test_supervisor_backs_off_instead_of_respawning_every_second(monkeypatch):
    import collector.secure_main as sm

    spawned = []

    class Worker:
        def __init__(self, *args, **kwargs):
            spawned.append(real_time.monotonic())

        def poll(self):
            return EXIT_ALREADY_RUNNING

        def terminate(self):
            pass

    monkeypatch.setattr(sm, "initialize_run", lambda *_a: None)
    monkeypatch.setattr(sm, "read_state", lambda: {"state": "recording", "generation": 1})
    monkeypatch.setattr(sm.subprocess, "Popen", Worker)
    monkeypatch.setattr(sm.signal, "signal", lambda *_a: None)
    stop_at = real_time.monotonic() + 3.0
    original_sleep = real_time.sleep

    def fake_sleep(seconds):
        original_sleep(0.05)
        if real_time.monotonic() >= stop_at:
            sm._STOP = True

    monkeypatch.setattr(sm.time, "sleep", fake_sleep)
    sm.run_supervisor(Path("/nonexistent/config.json"))
    assert len(spawned) == 1  # without the back-off this is 3


def test_untrusted_process_blocks_mouse_and_keyboard_too(tmp_path, monkeypatch, capsys):
    """pynput needs Accessibility for both listeners on macOS (seen on a real Mac)."""
    monkeypatch.setattr(cm.InteractionSensor, "start", lambda self: True)
    monkeypatch.setattr(cm.InteractionSensor, "stop", lambda self: None)
    _events, heartbeats = drive(
        tmp_path, monkeypatch, lambda t: ("Code", "main.py", 0.0, False), end=10,
        permissions={"accessibility": False, "input_monitoring": True}, keyboard=True,
        config={"screen_interactions_enabled": True},
    )
    out = capsys.readouterr().out
    assert "Screen interaction capture: BLOCKED (macOS accessibility permission" in out
    assert "Keyboard activity: BLOCKED (macOS accessibility permission" in out
    assert heartbeats[0]["keyboard_sensor"] is False


def test_document_change_seen_at_a_checkpoint_never_emits_an_empty_span(tmp_path, monkeypatch):
    # The new title first appears in the same poll as the 120 s checkpoint.
    events, _ = drive(tmp_path, monkeypatch, lambda t: ("Word", "A.docx" if t < 120 else "B.docx", 0.0, False), end=200)
    focus = spans(events, "focus_span")
    assert all(e["duration_seconds"] > 0 for e in focus), [(seconds(e), e["duration_seconds"]) for e in focus]
    assert [(seconds(e), e["window_title"]) for e in focus] == [(0, "A.docx"), (120, "B.docx")]
    assert_no_overlap_and_no_hole(events, 200)
