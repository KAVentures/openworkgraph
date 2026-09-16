from collector.interactions import ActivityTracker


def test_activity_tracker_counts_without_key_identity():
    t = ActivityTracker()
    t.record('key', 101.0)
    t.record('key', 102.0)
    t.record('click', 103.0)
    t.record('scroll', 104.0)
    out = t.summarize(100.0, 110.0, active_window_seconds=2, engaged_grace_seconds=5)
    assert out['keypress_count'] == 2
    assert out['click_count'] == 1
    assert out['scroll_count'] == 1
    assert out['input_events'] == 4
    assert 0 < out['active_input_seconds'] <= 10
    assert 0 < out['engaged_seconds'] <= 10
    assert out['idle_seconds'] >= 0
    assert 'key' not in out


def test_activity_tracker_marks_long_no_input_tail_idle():
    t = ActivityTracker()
    t.record('click', 1.0)
    out = t.summarize(0.0, 120.0, engaged_grace_seconds=60)
    assert out['engaged_seconds'] == 60.0
    assert out['idle_seconds'] == 60.0
