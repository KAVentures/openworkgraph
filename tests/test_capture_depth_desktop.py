"""Desktop drag detection and pointer sampling gated by capture depth (#634)."""
import json

import pytest

from collector.interactions import (DragDetector, HoverTracker, InteractionSensor, PointerSampler,
                                    capture_depth_signals, interactive_target)


def test_drag_needs_distance_and_time_and_clicks_are_not_drags():
    d = DragDetector()
    d.press(100, 100, "left", 1.0)
    assert d.release(103, 101, 1.5) is None          # too short a move
    d.press(100, 100, "left", 1.0)
    assert d.release(200, 150, 1.05) is None         # too quick
    d.press(100, 100, "left", 1.0)
    drag = d.release(220, 160, 1.6)
    assert drag.kind == "drag" and (drag.x, drag.y, drag.dx, drag.dy) == (100.0, 100.0, 120.0, 60.0)
    assert d.release(300, 300, 2.0) is None          # release without press


def test_pointer_sampler_throttles_and_batches():
    s = PointerSampler(hz=4, max_points=5)
    out = [s.move(i * 10, i * 5, i * 0.05) for i in range(200)]
    batches = [b for b in out if b is not None]
    assert batches and all(len(b.points) == 5 for b in batches)
    times = [p[0] for p in batches[0].points]
    assert all(b - a >= 249 for a, b in zip(times, times[1:]))
    assert s.flush() is None or s.flush() is None


def test_capture_depth_file_is_read_and_unacknowledged_trajectories_stay_off(tmp_path):
    assert capture_depth_signals(tmp_path) == {"drag_drop": False, "pointer_trajectory": False, "trajectory_hz": 4,
                                               "hover_dwell": False, "hover_dwell_ms": 750}
    (tmp_path / "capture_depth.json").write_text(json.dumps({"signals": {"drag_drop": True, "pointer_trajectory": True},
                                                             "parameters": {"trajectory_hz": 9}}))
    assert capture_depth_signals(tmp_path) == {"drag_drop": True, "pointer_trajectory": False, "trajectory_hz": 9,
                                               "hover_dwell": False, "hover_dwell_ms": 750}
    (tmp_path / "capture_depth.json").write_text(json.dumps({"signals": {"pointer_trajectory": True},
                                                             "full_acknowledged_version": "capture-depth-full-v1"}))
    assert capture_depth_signals(tmp_path)["pointer_trajectory"] is True


def test_sensor_callbacks_follow_live_depth(monkeypatch):
    seen = []
    depth = {"drag_drop": False, "pointer_trajectory": False, "trajectory_hz": 20}
    sensor = InteractionSensor(seen.append, depth=lambda: depth)

    class FakeListener:
        def __init__(self, **handlers):
            self.handlers = handlers
            self.daemon = True
        def start(self):
            pass
    import types, sys
    fake = types.SimpleNamespace(Listener=FakeListener)
    monkeypatch.setitem(sys.modules, "pynput", types.SimpleNamespace(mouse=fake))
    monkeypatch.setitem(sys.modules, "pynput.mouse", fake)
    assert sensor.start()
    h = sensor._mouse_listener.handlers
    button = types.SimpleNamespace(name="left")
    times = iter([float(i) for i in range(1000)])
    monkeypatch.setattr("collector.interactions.time.monotonic", lambda: next(times))
    h["on_click"](10, 10, button, True); h["on_click"](300, 300, button, False)
    assert [r.kind for r in seen] == ["click"], "drags are off at Standard"
    depth["drag_drop"] = True
    h["on_click"](10, 10, button, True); h["on_click"](300, 300, button, False)
    assert [r.kind for r in seen][-2:] == ["click", "drag"]
    for i in range(3):
        h["on_move"](i, i)
    assert not any(r.kind == "pointer_trajectory" for r in seen)
    depth["pointer_trajectory"] = True
    sensor._sampler.max_points = 3
    for i in range(10):
        h["on_move"](i * 10, i * 10)
    assert any(r.kind == "pointer_trajectory" and len(r.points) == 3 for r in seen)


@pytest.fixture
def depth(tmp_path, monkeypatch):
    from server import capture_depth
    monkeypatch.setattr(capture_depth, "DATA_DIR", tmp_path)
    return capture_depth


def test_server_ingest_filter_enforces_depth_and_bounds(depth):
    drag = {"event_type": "screen_drag", "metadata": {"action": "drag"}}
    traj = {"event_type": "screen_pointer_trajectory", "metadata": {"points": [[0, 10, 20], [250, -1900, 40]]}}
    click = {"event_type": "screen_click", "metadata": {}}
    assert depth.filter_desktop_events([drag, traj, click]) == [click]
    depth.save({"profile": "full", "acknowledgement_version": depth.FULL_ACK_VERSION})
    assert depth.filter_desktop_events([drag, traj, click]) == [drag, traj, click]
    excluded = {"event_type": "screen_drag", "metadata": {"excluded": True}}
    bad = {"event_type": "screen_pointer_trajectory", "metadata": {"points": [[0, "x", 1]]}}
    assert depth.filter_desktop_events([excluded, bad]) == []


def test_desktop_trajectories_are_summarized_and_never_steps():
    from server.privacy_pipeline import summarize_pointer_trajectories
    from server.analytics import _is_distinct_user_action_after_completion, _task_action_token
    event = {"event_type": "screen_pointer_trajectory", "metadata": {"action": "pointer_trajectory", "points": [[0, 1, 2]]}}
    assert "points" not in summarize_pointer_trajectories(event)["metadata"]
    assert _task_action_token(event) == ""
    assert _is_distinct_user_action_after_completion(event, "", 0.0, 10.0) is False


def test_hover_tracker_reports_rests_that_end_by_moving_or_pressing():
    h = HoverTracker(threshold_ms=750)
    assert h.move(100, 100, 0.0) is None
    assert h.move(101, 101, 0.5) is None              # jitter within tolerance keeps resting
    dwell = h.move(400, 300, 1.0)
    assert dwell.kind == "hover_dwell" and dwell.duration_ms == 1000 and dwell.ended_by == "pointer_left"
    assert (dwell.x, dwell.y) == (100.0, 100.0)
    assert h.move(410, 300, 1.2) is None              # 200 ms is a quick pass
    h.move(500, 500, 2.0)
    pressed = h.activate(3.0)
    assert pressed.ended_by == "activated" and pressed.duration_ms == 1000
    assert h.activate(3.1) is None


@pytest.mark.parametrize("target,expected", [({"role": "AXButton", "label": "Save"}, True), ({"role": "Hyperlink"}, True),
                                             ({"role": "MenuItem"}, True), ({"role": "AXStaticText"}, False),
                                             ({"role": "AXTextField", "secure": True}, False), ({}, False), (None, False)])
def test_only_interactive_non_secure_targets_count(target, expected):
    assert interactive_target(target) is expected


def test_server_drops_desktop_hovers_without_a_safe_target(depth):
    depth.save({"profile": "enhanced"})
    good = {"event_type": "screen_hover_dwell", "metadata": {"duration_ms": 900, "target": {"role": "AXButton"}}}
    secure = {"event_type": "screen_hover_dwell", "metadata": {"duration_ms": 900, "target": {"role": "x", "secure": True}}}
    untargeted = {"event_type": "screen_hover_dwell", "metadata": {"duration_ms": 900}}
    assert depth.filter_desktop_events([good, secure, untargeted]) == [good]
    depth.save({"profile": "standard"})
    assert depth.filter_desktop_events([good]) == []
