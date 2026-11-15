"""R10 per-job quiet hours: explicit opt-in, no fabricated runs, local DST semantics."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import local_scheduler as scheduler

ROOT = Path(__file__).resolve().parents[1]
STOCKHOLM = {"timezone": "Europe/Stockholm", "start": "22:00", "end": "07:00"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "_check_target", lambda kind, target: None)
    monkeypatch.setattr(scheduler, "_signal_rule_set_pin", lambda log_id: {"fingerprint": "a" * 64, "count": 1})


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _create(quiet_hours=None):
    return scheduler.create("event_log_signals", "log_synthetic",
                            interval_minutes=15, confirm_scheduled_runs=True,
                            quiet_hours=quiet_hours)


def _due(schedule_id: str, stamp: str) -> int:
    epoch = _epoch(stamp)
    data = scheduler._read()
    data["schedules"][schedule_id]["next_run_epoch"] = epoch
    scheduler._write(data)
    return epoch


def _schedule(schedule_id: str) -> dict:
    return scheduler._read()["schedules"][schedule_id]


def test_default_off_and_strict_iana_window_validation():
    first = _create()
    assert "quiet_hours" not in first
    assert first["quiet_hours_active"] is False
    scheduler.delete(first["schedule_id"])
    valid = [
        {"timezone": "UTC", "start": "09:00", "end": "17:00"},
        STOCKHOLM,
        {"timezone": "America/New_York", "start": "00:00", "end": "23:59"},
    ]
    for config in valid:
        item = _create(config)
        assert item["quiet_hours"] == config
        scheduler.delete(item["schedule_id"])
    bad = [
        {}, [], {"timezone": "UTC", "start": "22:00"},
        {"timezone": "UTC", "start": "22:00", "end": "07:00", "permit_bearer": True},
        {"timezone": "UTC", "start": "00:00", "end": "00:00"},
        {"timezone": "UTC", "start": "24:00", "end": "07:00"},
        {"timezone": "UTC", "start": "22:61", "end": "07:00"},
        {"timezone": "UTC", "start": 2200, "end": "07:00"},
        {"timezone": "Mars/Phobos", "start": "22:00", "end": "07:00"},
        {"timezone": "../etc/passwd", "start": "22:00", "end": "07:00"},
        {"timezone": "/etc/zoneinfo", "start": "22:00", "end": "07:00"},
        {"timezone": True, "start": "22:00", "end": "07:00"},
    ]
    for config in bad:
        with pytest.raises(scheduler.ScheduleError):
            _create(config)
    assert scheduler.status()["schedules"] == []


def test_overnight_local_window_defers_without_running_or_forging_success():
    item = _create(STOCKHOLM)
    now = _due(item["schedule_id"], "2026-11-07T21:30:00Z")  # 22:30 CET
    called = []
    runner = lambda k, t: called.append((k, t)) or ("succeeded", 0.0)
    assert scheduler.run_due(now=now, runner=runner) == []
    assert called == []
    state = _schedule(item["schedule_id"])
    assert state["next_run_epoch"] == _epoch("2026-11-08T06:00:00Z")  # 07:00 CET
    assert state["last_deferred_until"] == "2026-11-08T06:00:00+00:00"
    assert state["last_deferred_at"] == "2026-11-07T21:30:00+00:00"
    assert state["history"] == [] and state["last_result"] is None
    assert state["last_run_epoch"] is None and state["consecutive_failures"] == 0
    assert scheduler.run_due(now=now + 60, runner=runner) == []
    assert scheduler.run_due(now=state["next_run_epoch"], runner=runner) == [{
        "schedule_id": item["schedule_id"], "outcome": "succeeded"}]
    assert called == [("event_log_signals", "log_synthetic")]
    assert len(_schedule(item["schedule_id"])["history"]) == 1


def test_daytime_quiet_window_and_midnight_boundaries():
    spec = {"timezone": "UTC", "start": "09:00", "end": "17:00"}
    assert scheduler._quiet_until(_epoch("2026-11-07T08:59:00Z"), spec) is None
    assert scheduler._quiet_until(_epoch("2026-11-07T09:00:00Z"), spec) == _epoch("2026-11-07T17:00:00Z")
    assert scheduler._quiet_until(_epoch("2026-11-07T16:59:45Z"), spec) == _epoch("2026-11-07T17:00:00Z")
    assert scheduler._quiet_until(_epoch("2026-11-07T17:00:00Z"), spec) is None
    assert scheduler._quiet_until(_epoch("2026-11-07T23:59:00Z"), spec) is None
    for time_str in ("2026-11-07T20:59:00Z", "2026-11-08T06:00:00Z"):
        assert scheduler._quiet_until(_epoch(time_str), STOCKHOLM) is None


def test_spring_gap_never_reschedules_into_nonexistent_02_hour():
    spec = {"timezone": "Europe/Stockholm", "start": "01:30", "end": "03:30"}
    # 2026-03-29 Stockholm local clock jumps 01:59 to 03:00 (CET->CEST).
    assert scheduler._quiet_until(_epoch("2026-03-29T00:45:00Z"), spec) == _epoch("2026-03-29T01:30:00Z")
    assert scheduler._quiet_until(_epoch("2026-03-29T01:15:00Z"), spec) == _epoch("2026-03-29T01:30:00Z")


def test_autumn_fold_keeps_both_repeated_local_02_hours_quiet():
    spec = {"timezone": "Europe/Stockholm", "start": "02:00", "end": "03:30"}
    # The clock repeats 02:00-02:59; both folds must remain inside window.
    end = _epoch("2026-10-25T02:30:00Z")
    assert scheduler._quiet_until(_epoch("2026-10-25T00:30:00Z"), spec) == end
    assert scheduler._quiet_until(_epoch("2026-10-25T01:30:00Z"), spec) == end
    assert scheduler._quiet_until(end, spec) is None


def test_update_edit_clear_and_resume_keep_legacy_job_authorization():
    item = _create()
    sid = item["schedule_id"]
    config = {"timezone": "UTC", "start": "21:00", "end": "06:00"}
    new = scheduler.update(sid, quiet_hours=config)
    assert new["quiet_hours"] == config
    assert scheduler.update(sid, enabled=False)["enabled"] is False
    assert scheduler.update(sid, enabled=True)["quiet_hours"] == config
    scheduler.update(sid, interval_minutes=60)
    assert _schedule(sid)["interval_minutes"] == 60
    for bad in (False, "", {"timezone": "UTC", "start": "21:00", "end": "21:00"}):
        with pytest.raises(scheduler.ScheduleError):
            scheduler.update(sid, quiet_hours=bad)
        assert _schedule(sid)["quiet_hours"] == config
    cleared = scheduler.update(sid, quiet_hours=None)
    assert "quiet_hours" not in cleared
    assert "quiet_hours" not in _schedule(sid)
    assert scheduler.update(sid, enabled=False)["enabled"] is False


def test_invalid_persisted_quiet_window_fails_closed_not_an_execution():
    item = _create()
    now = _due(item["schedule_id"], "2026-11-07T10:00:00Z")
    data = scheduler._read()
    data["schedules"][item["schedule_id"]]["quiet_hours"] = {
        "timezone": "Invalid/Zone", "start": "22:00", "end": "07:00"}
    scheduler._write(data)
    called = []
    result = scheduler.run_due(now=now, runner=lambda *args: called.append(args) or ("succeeded", 0))
    assert result == [{"schedule_id": item["schedule_id"], "outcome": "failed:invalid_quiet_hours"}]
    assert not called
    state = _schedule(item["schedule_id"])
    assert state["consecutive_failures"] == 1
    assert state["next_run_epoch"] > now
    assert state["history"][0]["outcome"] == "failed:invalid_quiet_hours"
    assert scheduler.status()["schedules"][0]["quiet_hours_active"] is True


def test_auth_routes_create_update_clear_windows_and_reject_ai_bearer(tmp_path):
    script = r'''
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.local_scheduler_routes import router
from server import local_auth, local_scheduler

app = FastAPI()
app.include_router(router)
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    ai = {"Authorization": "Bearer synthetic-ai"}
    spec = {"timezone":"UTC","start":"22:00","end":"07:00"}
    url = "/v1/schedules"
    payload = {"kind":"local_backup","target_id":"local","interval_minutes":1440,
               "confirm_scheduled_runs":True,"confirm_unencrypted_local_backup":True,
               "quiet_hours":spec}
    assert client.post(url, json=payload).status_code == 403
    assert client.post(url, json=payload, headers=ai).status_code == 403
    made=client.post(url, json=payload, headers=owner)
    assert made.status_code == 200, made.text
    sid=made.json()["schedule_id"]
    assert made.json()["quiet_hours"] == spec
    assert made.json()["unencrypted_local_backup_acknowledged"] is True
    assert client.post(url+"/update",json={"schedule_id":sid,"quiet_hours":"false"},
                       headers=owner).status_code == 409
    assert client.post(url+"/update",json={"schedule_id":sid,"quiet_hours":spec},
                       headers=ai).status_code == 403
    assert client.post(url+"/update",json={"schedule_id":sid,"quiet_hours":{
        "timezone":"UTC","start":"21:30","end":"06:30"}},headers=owner).status_code == 200
    assert client.get(url, headers=owner).json()["schedules"][0]["quiet_hours"]["start"] == "21:30"
    cleared=client.post(url+"/update",json={"schedule_id":sid,"quiet_hours":None},headers=owner)
    assert cleared.status_code == 200
    assert "quiet_hours" not in cleared.json()
    assert client.get(url,headers=owner).json()["schedules"][0].get("quiet_hours") is None
    assert client.post(url+"/delete",json={"schedule_id":sid},headers=owner).json()["removed"]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
                "OWG_BACKUP_DIR": str(tmp_path / "backups")})
    run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"


def test_privacy_ui_wires_optional_and_existing_job_controls():
    s = (ROOT / "dashboard/basic_mode.js").read_text(encoding="utf-8")
    assert 'id="pvScheduleQuietEnabled"' in s
    assert 'id="pvScheduleQuietTimezone"' in s
    assert 'id="pvScheduleQuietStart"' in s
    assert 'id="pvScheduleQuietEnd"' in s
    assert "readQuietWindow()" in s
    assert "quiet_hours: quietEnabled.checked ? readQuietWindow() : null" in s
    assert "quiet_hours: null" in s
    assert "next_run_at" in s and "last_deferred_until" in s



def test_second_due_job_paused_during_first_job_is_never_dispatched():
    a = _create()
    b = scheduler.create("event_log_signals", "log_other", interval_minutes=15,
                         confirm_scheduled_runs=True)
    when = _epoch("2026-11-07T10:00:00Z")
    data = scheduler._read()
    for item in data["schedules"].values():
        item["next_run_epoch"] = when
    scheduler._write(data)
    called = []
    def runner(kind, target):
        called.append(target)
        if target == "log_synthetic":
            scheduler.update(b["schedule_id"], enabled=False)
        return "succeeded", 0.0
    assert scheduler.run_due(now=when, runner=runner) == [{
        "schedule_id": a["schedule_id"], "outcome": "succeeded"}]
    assert called == ["log_synthetic"]
    assert scheduler.status()["schedules"][1]["enabled"] is False
    assert _schedule(b["schedule_id"])["history"] == []


def test_second_due_job_freshly_configured_quiet_during_first_is_deferred():
    a = _create()
    b = scheduler.create("event_log_signals", "log_other", interval_minutes=15,
                         confirm_scheduled_runs=True)
    when = _epoch("2026-11-07T10:00:00Z")
    data = scheduler._read()
    for item in data["schedules"].values():
        item["next_run_epoch"] = when
    scheduler._write(data)
    called = []
    def runner(kind, target):
        called.append(target)
        if target == "log_synthetic":
            scheduler.update(b["schedule_id"], quiet_hours={
                "timezone": "UTC", "start": "09:00", "end": "17:00"})
        return "succeeded", 0.0
    result = scheduler.run_due(now=when, runner=runner)
    assert result == [{"schedule_id": a["schedule_id"], "outcome": "succeeded"}]
    assert called == ["log_synthetic"]
    other = _schedule(b["schedule_id"])
    assert other["next_run_epoch"] == _epoch("2026-11-07T17:00:00Z")
    assert other["history"] == [] and other["last_run_epoch"] is None


def test_second_due_job_deleted_during_first_is_never_dispatched():
    a = _create()
    b = scheduler.create("event_log_signals", "log_other", interval_minutes=15,
                         confirm_scheduled_runs=True)
    when = _epoch("2026-11-07T10:00:00Z")
    data = scheduler._read()
    for item in data["schedules"].values():
        item["next_run_epoch"] = when
    scheduler._write(data)
    called = []
    def runner(kind, target):
        called.append(target)
        if target == "log_synthetic":
            scheduler.delete(b["schedule_id"])
        return "succeeded", 0.0
    assert scheduler.run_due(now=when, runner=runner) == [{
        "schedule_id": a["schedule_id"], "outcome": "succeeded"}]
    assert called == ["log_synthetic"]
    assert b["schedule_id"] not in scheduler._read()["schedules"]
