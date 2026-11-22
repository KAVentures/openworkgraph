"""Work in progress over time with a Little's law check."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_wip as wip
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(rows):
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


def test_daily_arrivals_completions_open_and_littles_law():
    # Two cases arrive each day for 10 days; each takes exactly 2 days; one never closes.
    rows = []
    for d in range(10):
        for k in range(2):
            case, t = f"c{d}_{k}", START + timedelta(days=d, hours=1 + k)
            rows.append((case, "Open", t.isoformat()))
            if not (d == 9 and k == 1):
                rows.append((case, "Close", (t + timedelta(days=2)).isoformat()))
    r = wip.work_in_progress(_log(rows), {"end_activities": ["Close"]})
    days = r["buckets"]
    assert len(days) == 12 and [b["arrivals"] for b in days[:10]] == [2] * 10
    assert [b["completions"] for b in days] == [0, 0] + [2] * 9 + [1]
    assert [b["open_at_end"] for b in days] == [2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1]
    assert r["never_completed_cases"] == 1 and r["completed_cases"] == 19
    law = r["littles_law"]
    assert law["mean_cycle_time_seconds"] == 2 * 86400 and law["arrival_rate_per_day"] > 1.6
    assert 0.8 < law["ratio"] < 1.2, law


def test_weekly_buckets_without_end_activities_and_validation():
    rows = [(f"c{i}", a, (START + timedelta(days=i, hours=h)).isoformat()) for i in range(14) for h, a in ((0, "A"), (5, "B"))]
    log = _log(rows)
    r = wip.work_in_progress(log, {"bucket": "week"})
    assert len(r["buckets"]) == 2 and sum(b["arrivals"] for b in r["buckets"]) == 14
    assert r["buckets"][-1]["open_at_end"] == 0 and r["completion_rule"] == "every case completes at its last event"
    assert wip.work_in_progress(log, {"cohort": {"activity": "B"}})["cases"] == 14
    for bad in ({"bucket": "month"}, {"end_activities": [""]}, {"unknown": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            wip.work_in_progress(log, bad)
    long_rows = [("x", "A", START.isoformat()), ("x", "B", (START + timedelta(days=500)).isoformat())]
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most 400 buckets"):
        wip.work_in_progress(_log(long_rows))
    assert len(wip.work_in_progress(_log(long_rows), {"bucket": "week"})["buckets"]) <= 73


def test_evidence_matches_every_count_with_utc_boundaries_and_completion_rules():
    log = _log([
        ("a", "Start", "2024-03-04T12:00:00Z"),
        ("a", "Close", "2024-03-05T00:00:00Z"),
        ("a2", "Start", "2024-03-04T12:00:00Z"),
        ("a2", "Close", "2024-03-04T23:59:59.999999Z"),
        ("b", "Start", "2024-03-05T00:00:00Z"),
        ("b", "Hold", "2024-03-06T01:00:00Z"),
        ("c", "Start", "2024-03-11T00:00:00Z"),
    ])
    for report_settings in ({"end_activities": ["Close"]}, {"bucket": "week", "end_activities": ["Close"]},
                            {}, {"cohort": {"activity": "Close"}}):
        report = wip.work_in_progress(log, report_settings)
        for bucket in report["buckets"]:
            for group in ("arrivals", "completions", "open_at_end"):
                evidence = wip.bucket_cases(log, {
                    **report_settings, "bucket_start": bucket["start"][:10], "group": group,
                })
                assert evidence["total"] == len(evidence["cases"]) == bucket[group]
    day = {"end_activities": ["Close"], "bucket_start": "2024-03-04"}
    open_cases = wip.bucket_cases(log, {**day, "group": "open_at_end"})
    assert open_cases["cases"] == [{"case_id": "a", "started_at": "2024-03-04T12:00:00Z", "completed_at": None}]
    finished = wip.bucket_cases(log, {**day, "group": "completions"})
    assert finished["cases"][0]["completed_at"] == "2024-03-04T23:59:59.999999Z"


def test_evidence_pagination_uses_literal_keys_and_rejects_invalid_settings():
    log = _log([(key, "Open", START.isoformat()) for key in ("a2", "a11", "a1", "a10")])
    spec = {"bucket": "week", "bucket_start": "2024-03-04", "group": "arrivals", "limit": 2}
    first = wip.bucket_cases(log, spec)
    second = wip.bucket_cases(log, {**spec, "after": first["next_after"]})
    assert first["total"] == second["total"] == 4
    assert [row["case_id"] for row in first["cases"]] == ["a1", "a10"]
    assert [row["case_id"] for row in second["cases"]] == ["a11", "a2"]
    assert second["next_after"] is None
    for patch in ({"bucket_start": "2024-03-05"}, {"bucket_start": "2024-04-01"}, {"bucket_start": "2024-02-30"},
                  {"after": ""}, {"group": "unknown"}, {"limit": True}, {"limit": 101}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            wip.bucket_cases(log, {**spec, **patch})


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-0{1+i}T0{k}:00:00Z" for i in range(3) for k, a in enumerate(["Open", "Close"])]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    path = f"/v1/event-logs/{imported.json()['log_id']}/work-in-progress"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    r = client.post(path, json={"end_activities": ["Close"]}, headers=owner)
    assert r.status_code == 200 and r.json()["completed_cases"] == 3, r.text
    assert client.post(path, json={"bucket": "year"}, headers=owner).status_code == 422
    spec = {"bucket_start": "2024-03-01", "group": "completions"}
    assert client.post(path + "/cases", json=spec, headers=bearer).status_code == 403
    assert client.post(path + "/cases", json=spec, headers=owner).json()["cases"][0]["case_id"] == "c0"
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
