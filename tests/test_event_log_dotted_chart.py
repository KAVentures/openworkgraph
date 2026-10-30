"""Dotted chart of events over time (roadmap #326 H2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_dotted_chart as dots
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases=20, extra_activities=0):
    rows = ["case,activity,time"]
    for c in range(cases):
        steps = ["Receive", "Check", "Close"] + [f"Rare {i}" for i in range(extra_activities) if c == 0]
        for h, a in enumerate(steps):
            rows.append(f"c{c:03d},{a},{(START + timedelta(days=c, hours=h * (c + 1))).isoformat()}")
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="d", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def test_absolute_and_relative_time_with_ordering():
    log = _log()
    absolute = dots.dotted_chart(log)
    first = absolute["cases"][0]
    assert first["case_id"] == "c000" and first["points"][0] == [0, first["points"][0][1]]
    assert absolute["cases"][5]["points"][0][0] == 5 * 86400       # calendar time from the log's first event
    relative = dots.dotted_chart(log, time="relative", order="duration")
    assert relative["cases"][0]["case_id"] == "c019"               # longest first
    assert relative["cases"][0]["points"][0][0] == 0 and relative["cases"][0]["duration_seconds"] == 2 * 20 * 3600
    assert relative["time_origin"] is None and absolute["time_origin"] == "2024-01-01T00:00:00Z"


def test_colours_follow_overall_frequency_and_fold_into_other():
    log = _log(cases=10, extra_activities=9)
    result = dots.dotted_chart(log)
    names = [a["activity"] for a in result["activities"]]
    assert names[:3] == ["Check", "Close", "Receive"] and len(names) == 8 and names[-1] is None
    assert result["activities"][-1]["activities"] == 5 and result["activities"][-1]["slot"] == 7
    filtered = dots.dotted_chart(log, cohort={"activity": "Rare 0"})
    assert [a["activity"] for a in filtered["activities"]][:3] == names[:3]   # a filter never repaints


def test_sampling_and_thinning_keep_bounds(monkeypatch):
    log = _log(cases=50)
    full = dots.dotted_chart(log, max_cases=50)
    assert full["coverage"]["events"] == full["coverage"]["events_in_shown_cases"] == 150
    assert full["coverage"]["events_omitted_by_case_sampling"] == 0
    assert full["coverage"]["events_omitted_by_point_thinning"] == 0
    sampled = dots.dotted_chart(log, max_cases=10)
    assert sampled["coverage"]["cases_shown"] == 10 and sampled["coverage"]["cases_sampled"] is True
    ids = [c["case_id"] for c in sampled["cases"]]
    assert ids[0] == "c000" and ids == sorted(ids) and ids[-1] >= "c040"       # spread across the whole log
    assert sampled["coverage"]["events"] == 150
    assert sampled["coverage"]["events_in_shown_cases"] == sampled["coverage"]["points_shown"] == 30
    assert sampled["coverage"]["events_omitted_by_case_sampling"] == 120
    assert sampled["coverage"]["events_omitted_by_point_thinning"] == 0
    monkeypatch.setattr(dots, "MAX_POINTS", 10)
    thin = dots.dotted_chart(log, max_cases=10)
    assert thin["coverage"]["points_shown"] == 10 and thin["coverage"]["points_thinned"] is True
    assert thin["coverage"]["events_in_shown_cases"] == 30
    assert thin["coverage"]["events_omitted_by_case_sampling"] == 120
    assert thin["coverage"]["events_omitted_by_point_thinning"] == 20
    assert (thin["coverage"]["points_shown"] + thin["coverage"]["events_omitted_by_point_thinning"]
            + thin["coverage"]["events_omitted_by_case_sampling"] == thin["coverage"]["events"])
    for bad in ({"time": "x"}, {"order": "x"}, {"max_cases": 0}, {"max_cases": 5000}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            dots.dotted_chart(log, **bad)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/dotted-chart"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path + "?time=relative", headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["cases"][0]["points"] == [[0, 0], [3600, 1]]
    assert client.get(path + "?max_cases=0", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_sample_quickly():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        for s in range(5):
            rows.append(f"c{c},S{s},{(START + timedelta(seconds=c * 10 + s)).isoformat()}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    started = time.perf_counter()
    result = dots.dotted_chart(log, max_cases=1000)
    assert time.perf_counter() - started < 20
    assert result["coverage"]["points_shown"] <= dots.MAX_POINTS and len(result["cases"]) == 1000
