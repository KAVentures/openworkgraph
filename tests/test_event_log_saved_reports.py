"""Saved copies of an event log's HTML report, by hand or on a local schedule."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_saved_reports as saved
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)


def _log(name="invoices"):
    rows = [[f"c{i}", a, (START + timedelta(days=i, hours=k)).isoformat()]
            for i in range(5) for k, a in enumerate(["Receive", "Pay"])]
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_save_list_read_and_keep_the_newest(monkeypatch):
    log = _log()
    assert saved.listing(log)["reports"] == []
    first = saved.save(log)
    assert first["saved"]["name"].startswith("report-") and first["kept"] == 1
    target = saved.path(log, first["saved"]["name"])
    html = target.read_text()
    assert html.startswith("<!doctype html>") and "<h1>invoices</h1>" in html and "<script" not in html.lower()
    # Two copies in the same second get distinct names.
    second = saved.save(log)
    assert second["saved"]["name"] != first["saved"]["name"]
    monkeypatch.setattr(saved, "KEEP", 3)
    for _ in range(4):
        saved.save(log)
    names = [r["name"] for r in saved.listing(log)["reports"]]
    assert len(names) == 3 and first["saved"]["name"] not in names
    # The newest copy is always kept and listed first, even among copies made in the same second.
    newest = saved.save(log)["saved"]["name"]
    assert saved.listing(log)["reports"][0]["name"] == newest and saved.path(log, newest).is_file()
    for bad in ("../x.html", "report.html", "report-20240101T000000Z.txt", ""):
        with pytest.raises(workspace.EventLogWorkspaceError):
            saved.path(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        saved.save("log_" + "0" * 16)


def test_deleting_the_log_removes_copies_and_its_schedules():
    log, other = _log("a"), _log("b")
    saved.save(log)
    scheduler.create("event_log_report", log, interval_minutes=60 * 24, confirm_scheduled_runs=True)
    # Signal schedules need a saved rule to pin.
    from server import event_log_signals as signals
    signals.add_rule(log, {"name": "Many cases", "when": {"kind": "kpi", "metric": "case_count",
                                                          "operator": "above", "threshold": 0}})
    scheduler.create("event_log_signals", log, interval_minutes=60, confirm_scheduled_runs=True)
    kept = scheduler.create("event_log_report", other, interval_minutes=60, confirm_scheduled_runs=True)
    workspace.delete_log(log)
    assert not (workspace.DATA_DIR / "event_log_reports" / log).exists()
    assert [s["schedule_id"] for s in scheduler.status()["schedules"]] == [kept["schedule_id"]]


def test_scheduled_job_saves_a_copy_and_reports_a_missing_log():
    log = _log()
    assert "event_log_report" in scheduler.KINDS
    assert scheduler._run_job("event_log_report", log) == ("succeeded:report_saved", 0.0)
    assert len(saved.listing(log)["reports"]) == 1
    assert scheduler._run_job("event_log_report", "log_" + "0" * 16) == ("failed:event_log_unavailable", 0.0)
    with pytest.raises(scheduler.ScheduleError):
        scheduler.create("event_log_report", "log_" + "0" * 16, interval_minutes=60, confirm_scheduled_runs=True)


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}/saved-reports"
    assert client.post(base, headers=bearer).status_code == 403
    assert client.get(base, headers=bearer).status_code == 403
    made = client.post(base, headers=owner)
    assert made.status_code == 200, made.text
    name = made.json()["saved"]["name"]
    changes = f"/v1/event-logs/{imported.json()['log_id']}/saved-report-changes"
    assert client.get(changes, headers=bearer).status_code == 403
    assert client.get(changes, headers=owner).json()["available"] is False
    assert client.post(base, headers=owner).status_code == 200
    assert client.get(changes, headers=owner).json()["figures"]["cases"]["change"] == 0
    assert client.get(changes + "?older=" + name, headers=owner).status_code == 422
    assert name in [r["name"] for r in client.get(base, headers=owner).json()["reports"]]
    assert client.get(f"{base}/{name}", headers=bearer).status_code == 403
    got = client.get(f"{base}/{name}", headers=owner)
    assert got.status_code == 200 and got.text.startswith("<!doctype html>")
    assert "default-src 'none'" in got.headers["content-security-policy"]
    assert client.get(f"{base}/report-20240101T000000Z.html", headers=owner).status_code == 404
    assert client.get(f"{base}/..%2Fsecrets.html", headers=owner).status_code == 404
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_changes_between_two_saved_copies():
    log = _log()
    assert saved.changes(log)["available"] is False
    first = saved.save(log)["saved"]
    assert first["has_figures"] is True
    assert saved.changes(log)["available"] is False
    # Newer events arrive: two more cases, one through a new Refund step.
    rows = ["case,activity,time", "c5,Receive,2024-03-10T08:00:00Z", "c5,Pay,2024-03-10T09:00:00Z",
            "c6,Receive,2024-03-11T08:00:00Z", "c6,Refund,2024-03-11T12:00:00Z"]
    workspace.append_log(log, BytesIO("\n".join(rows).encode()), source_format="csv", mapping=MAPPING)
    from server import event_log_costs as costs
    costs.save_model(log, {"currency": "EUR", "activity_costs": {"Pay": 3}})
    second = saved.save(log)["saved"]
    c = saved.changes(log)
    assert c["available"] and c["older"]["name"] == first["name"] and c["newer"]["name"] == second["name"]
    assert c["figures"]["cases"] == {"older": 5, "newer": 7, "change": 2}
    assert c["figures"]["events"]["change"] == 4 and c["figures"]["activities"]["change"] == 1
    assert c["activities_added"] == ["Refund"] and c["activities_gone"] == []
    assert c["largest_activity_changes"][0] == {"activity": "Receive", "older_cases": 5, "newer_cases": 7, "change": 2}
    # The first copy had no saved prices, so there is no cost comparison yet.
    assert c["cost"] is None and "appended events" in c["interpretation"]
    third = saved.save(log)["saved"]
    later = saved.changes(log)
    assert later["cost"] == {"currency": "EUR", "older": 18.0, "newer": 18.0, "change": 0.0}
    explicit = saved.changes(log, first["name"], third["name"])
    assert explicit["figures"]["cases"]["change"] == 2
    for bad in ((first["name"], None), ("report-20200101T000000Z.html", third["name"])):
        with pytest.raises(workspace.EventLogWorkspaceError):
            saved.changes(log, *bad)


def test_pruning_removes_figures_too(monkeypatch):
    log = _log()
    monkeypatch.setattr(saved, "KEEP", 2)
    for _ in range(4):
        saved.save(log)
    folder = workspace.DATA_DIR / "event_log_reports" / log
    assert len(list(folder.glob("report-*.html"))) == 2 and len(list(folder.glob("report-*.json"))) == 2
