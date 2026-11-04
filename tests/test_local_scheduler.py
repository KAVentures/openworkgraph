"""Opt-in local schedules: consent, backoff, pause and cleanup."""
from __future__ import annotations

from pathlib import Path

import pytest

from server import local_scheduler as scheduler


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "_check_target", lambda kind, target: None)


def _make(**kwargs):
    defaults = {"interval_minutes": 15, "confirm_scheduled_runs": True}
    return scheduler.create("event_log_signals", "log_abc", **{**defaults, **kwargs})


def test_nothing_is_scheduled_by_default_and_creation_needs_consent():
    assert scheduler.status()["schedules"] == []
    with pytest.raises(scheduler.ScheduleError, match="confirmation"):
        _make(confirm_scheduled_runs=False)
    for bad in (14, 7 * 24 * 60 + 1, "60", True):
        with pytest.raises(scheduler.ScheduleError):
            _make(interval_minutes=bad)
    with pytest.raises(scheduler.ScheduleError):
        scheduler.create("shell_command", "x", interval_minutes=15, confirm_scheduled_runs=True)
    created = _make()
    assert created["enabled"] and created["next_run_at"]
    with pytest.raises(scheduler.ScheduleError, match="already"):
        _make()


def test_due_jobs_run_and_reschedule():
    item = _make()
    calls = []
    runner = lambda kind, target: calls.append((kind, target)) or ("succeeded", 0.0)
    assert scheduler.run_due(now=0, runner=runner) == []
    start = 10 ** 10
    assert scheduler.run_due(now=start, runner=runner) == [{"schedule_id": item["schedule_id"], "outcome": "succeeded"}]
    state = scheduler.status()["schedules"][0]
    assert calls == [("event_log_signals", "log_abc")] and state["last_result"] == "succeeded"
    assert scheduler.run_due(now=start + 60, runner=runner) == []


def test_failures_back_off_respect_retry_after_and_pause_after_five():
    item = _make()
    data = scheduler._read()
    t = 10 ** 10
    for attempt in range(1, 6):
        ran = scheduler.run_due(now=t, runner=lambda k, i: ("failed:jira_rate_limited", 7200.0))
        assert ran and ran[0]["outcome"] == "failed:jira_rate_limited"
        live = scheduler._read()["schedules"][item["schedule_id"]]
        expected = max(min(24 * 3600, 900 * 2 ** (attempt - 1)), 7200)
        if attempt < 5:
            assert live["next_run_epoch"] == t + expected
        t = live["next_run_epoch"]
    live = scheduler.status()["schedules"][0]
    assert live["enabled"] is False and live["paused_reason"] == "paused_after_repeated_failures"
    resumed = scheduler.update(item["schedule_id"], enabled=True)
    assert resumed["enabled"] and resumed["consecutive_failures"] == 0


def test_one_crashing_job_does_not_stop_others():
    _make()
    scheduler.create("retained_jira_profile", "jira_abc", interval_minutes=15, confirm_scheduled_runs=True)
    def runner(kind, target):
        if kind == "event_log_signals":
            raise RuntimeError("boom")
        return "succeeded", 0.0
    outcomes = sorted(r["outcome"] for r in scheduler.run_due(now=10 ** 10, runner=runner))
    assert outcomes == ["failed:runtimeerror", "succeeded"]


def test_deleted_targets_lose_their_schedule(monkeypatch):
    _make()
    def gone(kind, target):
        raise scheduler.ScheduleError("unknown event log")
    monkeypatch.setattr(scheduler, "_check_target", gone)
    assert scheduler.run_due(now=10 ** 10)[0]["outcome"] == "removed_target_gone"
    assert scheduler.status()["schedules"] == []


def test_paused_recording_skips_scheduled_jira_reads(monkeypatch):
    monkeypatch.setattr(scheduler, "_recording", lambda: False)
    assert scheduler._run_job("retained_jira_profile", "jira_abc") == ("skipped_recording_paused", 0.0)


def test_inbox_reports_scheduled_checks_and_ui_is_wired():
    _make()
    assert scheduler.scheduled("event_log_signals", "log_abc") is True
    assert scheduler.scheduled("event_log_signals", "other") is False
    root = Path(__file__).resolve().parents[1]
    assert 'id="pvScheduleAdd"' in (root / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")
    assert "local_scheduler.start()" in (root / "server" / "enterprise_runner.py").read_text(encoding="utf-8")
