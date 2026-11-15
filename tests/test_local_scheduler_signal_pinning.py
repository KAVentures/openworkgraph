"""R10 local signal schedule pinning: no unreviewed rule changes run automatically."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from server import event_log_signals as signals
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from test_event_log_signals import _log, _rule

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    (tmp_path / "event_logs").mkdir()
    (tmp_path / "schedules").mkdir()
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "event_logs")
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path / "schedules")


def _save_rule(log, name="Alert", threshold=0):
    return signals.add_rule(log, _rule(
        name, kind="kpi", metric="case_count",
        operator="above", threshold=threshold))["rule_id"]


def _schedule(log):
    return scheduler.create(
        "event_log_signals", log, interval_minutes=15,
        confirm_scheduled_runs=True)


def test_signal_schedule_pins_immutable_rule_definitions_not_evaluation_or_data_revision():
    log = _log()
    with pytest.raises(scheduler.ScheduleError, match="save at least one"):
        _schedule(log)
    rid = _save_rule(log)
    item = _schedule(log)
    assert item["signal_rules_count"] == 1
    assert len(item["signal_rules_fingerprint"]) == 64
    original = item["signal_rules_fingerprint"]
    signals.evaluate(log)
    assert scheduler._signal_rule_set_pin(log)["fingerprint"] == original
    assert scheduler.run_due(now=10 ** 10)[0]["outcome"].startswith("succeeded:")
    after = scheduler.status()["schedules"][0]
    assert after["enabled"] is True and after["signal_rules_fingerprint"] == original
    assert after["last_result"].startswith("succeeded:")
    assert "Alert" not in json.dumps(scheduler._read())
    assert rid not in json.dumps(scheduler._read())
    assert scheduler.status()["delivers_to_third_parties"] is False


def test_new_signal_rule_pauses_schedule_without_evaluating_the_added_rule():
    log = _log()
    rid = _save_rule(log)
    item = _schedule(log)
    first = scheduler.run_due(now=10 ** 10)
    assert first[0]["outcome"].startswith("succeeded:")
    new_id = _save_rule(log, name="New rule", threshold=10)
    before = signals.list_rules(log)["rules"]
    assert next(x for x in before if x["rule_id"] == new_id)["last_evaluated_at"] is None
    outcome = scheduler.run_due(now=10 ** 10 + 901)
    assert outcome == [{"schedule_id": item["schedule_id"], "outcome": "paused:signal_rules_changed"}]
    assert scheduler.run_due(now=10 ** 10 + 1800) == []
    paused = scheduler.status()["schedules"][0]
    assert paused["enabled"] is False
    assert paused["paused_reason"] == "signal_rules_changed_requires_reapproval"
    assert paused["next_run_at"] is None
    assert paused["consecutive_failures"] == 0
    assert next(x for x in signals.list_rules(log)["rules"] if x["rule_id"] == new_id)["last_evaluated_at"] is None
    with pytest.raises(scheduler.ScheduleError, match="reapprove"):
        scheduler.update(item["schedule_id"], enabled=True)
    with pytest.raises(scheduler.ScheduleError, match="true or false"):
        scheduler.update(item["schedule_id"], confirm_current_signal_rules="true")
    resumed = scheduler.update(item["schedule_id"], enabled=True, confirm_current_signal_rules=True)
    assert resumed["enabled"] is True and resumed["signal_rules_count"] == 2
    assert resumed["signal_rules_fingerprint"] != item["signal_rules_fingerprint"]
    assert scheduler.run_due(now=10 ** 10 + 1800)[0]["outcome"] == "succeeded:2_signals"
    assert next(x for x in signals.list_rules(log)["rules"] if x["rule_id"] == new_id)["last_evaluated_at"]
    signals.delete_rule(log, rid)
    assert scheduler.run_due(now=10 ** 10 + 3000)[0]["outcome"] == "paused:signal_rules_changed"
    assert scheduler.status()["schedules"][0]["enabled"] is False


def test_legacy_unpinned_schedules_fail_closed_and_need_explicit_reapproval():
    log = _log()
    _save_rule(log)
    item = _schedule(log)
    data = scheduler._read()
    old = data["schedules"][item["schedule_id"]]
    old.pop("signal_rules_fingerprint")
    old.pop("signal_rules_count")
    scheduler._write(data)
    assert scheduler.run_due(now=10 ** 10)[0]["outcome"] == "paused:signal_rules_changed"
    assert scheduler.status()["schedules"][0]["enabled"] is False
    reapproved = scheduler.update(item["schedule_id"], confirm_current_signal_rules=True)
    assert reapproved["enabled"] is False
    assert reapproved["signal_rules_count"] == 1
    resumed = scheduler.update(item["schedule_id"], enabled=True)
    assert resumed["enabled"] is True


def test_changed_rule_set_does_not_cause_fake_failed_job_or_retry_counter():
    log = _log()
    _save_rule(log)
    item = _schedule(log)
    _save_rule(log, name="Changed", threshold=2)
    result = scheduler.run_due(now=10 ** 10)
    assert result[0]["outcome"] == "paused:signal_rules_changed"
    state = scheduler.status()["schedules"][0]
    assert state["consecutive_failures"] == 0
    assert len(state["history"]) == 1
    assert state["history"][0]["outcome"] == "paused:signal_rules_changed"


def test_source_inaccessible_during_pinned_check_uses_failure_backoff(monkeypatch):
    log = _log()
    _save_rule(log)
    item = _schedule(log)
    def unavailable(target):
        raise scheduler.ScheduleError("offline data")
    monkeypatch.setattr(scheduler, "_signal_rule_set_pin", unavailable)
    out = scheduler.run_due(now=10 ** 10)
    assert out[0]["outcome"] == "failed:signal_rules_unavailable"
    state = scheduler.status()["schedules"][0]
    assert state["enabled"] is True
    assert state["consecutive_failures"] == 1
    assert state["paused_reason"] is None


def test_reapproval_refuses_stale_schedule_edit(monkeypatch):
    log = _log()
    _save_rule(log)
    item = _schedule(log)
    old_pin = scheduler._signal_rule_set_pin

    def concurrent_edit(log_id):
        data = scheduler._read()
        data["schedules"][item["schedule_id"]]["configuration_revision"] += 1
        scheduler._write(data)
        return old_pin(log_id)

    monkeypatch.setattr(scheduler, "_signal_rule_set_pin", concurrent_edit)
    with pytest.raises(scheduler.ScheduleError, match="schedule changed"):
        scheduler.update(item["schedule_id"], enabled=True, confirm_current_signal_rules=True)
    current = scheduler.status()["schedules"][0]
    assert current["signal_rules_fingerprint"] == item["signal_rules_fingerprint"]


def test_other_schedule_kinds_cannot_approve_signal_rules(monkeypatch):
    monkeypatch.setattr(scheduler, "_check_target", lambda *args: None)
    item = scheduler.create("retained_jira_profile", "jira_example",
                            interval_minutes=15, confirm_scheduled_runs=True)
    with pytest.raises(scheduler.ScheduleError, match="only signal"):
        scheduler.update(item["schedule_id"], confirm_current_signal_rules=True)


def test_schedule_dashboard_exposes_explicit_reapproval_and_no_silent_resume():
    root = Path(__file__).resolve().parents[1]
    source = (root / "dashboard/basic_mode.js").read_text()
    routes = (root / "server/local_scheduler_routes.py").read_text()
    assert "Review rules and resume" in source
    assert "confirm_current_signal_rules: true" in source
    assert "signal_rules_changed_requires_reapproval" in source
    assert "signal_rules_fingerprint" in source
    assert "confirm_current_signal_rules" in routes


def test_schedule_reapproval_http_is_local_dashboard_only_and_rejects_bad_confirmation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = r"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import local_auth, local_scheduler as scheduler, local_scheduler_routes

scheduler._check_target = lambda kind, target: None
pin = {"fingerprint": "a" * 64, "count": 1}
scheduler._signal_rule_set_pin = lambda log_id: dict(pin)
app = FastAPI()
app.include_router(local_scheduler_routes.router)
auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
with TestClient(app) as client:
    made = client.post("/v1/schedules", headers=auth, json={
      "kind": "event_log_signals", "target_id": "log_example",
      "interval_minutes": 15, "confirm_scheduled_runs": True})
    assert made.status_code == 200, made.text
    item = made.json()
    assert item["signal_rules_fingerprint"] == "a" * 64
    pin["fingerprint"] = "b" * 64
    due = scheduler.run_due(now=10 ** 10)
    assert due[0]["outcome"] == "paused:signal_rules_changed"
    update = "/v1/schedules/update"
    normal = client.post(update, headers=auth,
      json={"schedule_id": item["schedule_id"], "enabled": True})
    assert normal.status_code == 409, normal.text
    bad = client.post(update, headers=auth,
      json={"schedule_id": item["schedule_id"], "enabled": True,
            "confirm_current_signal_rules": "true"})
    assert bad.status_code == 409, bad.text
    assert client.post(update, headers={"Authorization": "Bearer synthetic-AI"},
      json={"schedule_id": item["schedule_id"], "enabled": True,
            "confirm_current_signal_rules": True}).status_code == 403
    approved = client.post(update, headers=auth,
      json={"schedule_id": item["schedule_id"], "enabled": True,
            "confirm_current_signal_rules": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["signal_rules_fingerprint"] == "b" * 64
    assert approved.json()["enabled"] is True
    assert approved.json()["paused_reason"] is None
    assert scheduler._read()["schedules"][item["schedule_id"]]["signal_rules_count"] == 1
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    r = subprocess.run([sys.executable, "-c", source], cwd=root, env=env,
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, f"stdout={r.stdout}\\nstderr={r.stderr}"
