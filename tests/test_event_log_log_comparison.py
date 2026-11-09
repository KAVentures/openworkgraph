"""Comparing two saved event logs."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_log_comparison as comparison
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(name, cases):
    """``cases`` is a list of (path, hours between steps)."""
    rows = []
    for i, (path, hours) in enumerate(cases):
        for k, activity in enumerate(path):
            rows.append([f"{name}{i}", activity, (START + timedelta(days=i, hours=k * hours)).isoformat(), "a"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def _logs():
    north = _import("north", [(["Receive", "Check", "Approve", "Pay"], 1)] * 30
                    + [(["Receive", "Check", "Check", "Approve", "Pay"], 1)] * 10)
    # South has no Check step, a new Escalate step, and takes longer.
    south = _import("south", [(["Receive", "Approve", "Pay"], 3)] * 25 + [(["Receive", "Escalate", "Approve", "Pay"], 3)] * 15)
    return north, south


def test_two_logs_are_compared():
    north, south = _logs()
    r = comparison.compare({"baseline": {"log_id": north}, "comparison": {"log_id": south}})
    assert r["format"] == comparison.FORMAT
    assert r["baseline"]["name"] == "north" and r["comparison"]["name"] == "south"
    kb, kc, d = r["kpis"]["baseline"], r["kpis"]["comparison"], r["kpis"]["difference"]
    assert kb["cases"] == kc["cases"] == 40 and d["cases"] == 0
    assert kb["repeat_rate"] == 0.25 and kc["repeat_rate"] == 0 and d["repeat_rate"] == -0.25
    assert d["repeat_rate_interval_95"][1] < 0
    assert kb["duration_seconds"]["median"] == 3 * 3600 and kc["duration_seconds"]["median"] == 6 * 3600
    assert r["duration"]["probability_comparison_case_takes_longer"] > 0.9
    assert r["activities"]["only_baseline"] == ["Check"] and r["activities"]["only_comparison"] == ["Escalate"]
    assert r["activities"]["shared"] == 3
    by = {row["activity"]: row for row in r["activities"]["largest_share_differences"]}
    assert by["Check"]["baseline_share"] == 1.0 and by["Check"]["comparison_share"] == 0
    assert by["Check"]["share_difference"] == -1.0 and by["Check"]["interval_excludes_zero"]
    assert by["Pay"]["share_difference"] == 0 and not by["Pay"]["interval_excludes_zero"]
    edges = {(e["from"], e["to"]): e for e in r["paths"]["largest_share_differences"]}
    assert edges[("Receive", "Approve")]["presence"] == "only_comparison"
    assert edges[("Check", "Check")]["presence"] == "only_baseline"
    assert r["variants"]["shared_variants"] == 0 and r["variants"]["baseline_cases_in_shared_variants"] == 0
    assert "not evidence of its cause" in r["interpretation"]


def test_one_log_against_a_filtered_version_of_itself_and_limits():
    north, _ = _logs()
    r = comparison.compare({"baseline": {"log_id": north},
                            "comparison": {"log_id": north, "cohort": {"follows": [{"from": "Check", "to": "Check", "mode": "direct", "negate": False}]}},
                            "limit": 2})
    assert r["kpis"]["comparison"]["cases"] == 10 and r["variants"]["shared_variants"] == 1
    assert r["variants"]["comparison_cases_in_shared_variants"] == 1.0
    assert r["variants"]["baseline_cases_in_shared_variants"] == 0.25
    assert len(r["activities"]["largest_share_differences"]) <= 2 and len(r["variants"]["largest_share_differences"]) <= 2


def test_invalid_specs():
    north, south = _logs()
    bad = [None, {}, {"baseline": {"log_id": north}}, {"baseline": {"log_id": north}, "comparison": {"log_id": north}},
           {"baseline": {"log_id": north}, "comparison": {"log_id": south}, "limit": 0},
           {"baseline": {"log_id": north}, "comparison": {"log_id": south}, "limit": True},
           {"baseline": {"log_id": north, "x": 1}, "comparison": {"log_id": south}},
           {"baseline": {"log_id": north, "cohort": {"nope": 1}}, "comparison": {"log_id": south}},
           {"baseline": {"log_id": north}, "comparison": {"log_id": "log_" + "0" * 16}},
           {"baseline": {"log_id": north}, "comparison": {"log_id": south, "cohort": {"activity": "Nowhere"}}}]
    for spec in bad:
        with pytest.raises(workspace.EventLogWorkspaceError):
            comparison.compare(spec)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
def rows(prefix, activity):
    return ["case,activity,time"] + [f"{prefix}{i},Start,2024-03-04T08:00:0{i}Z" for i in range(3)] + [f"{prefix}{i},{activity},2024-03-04T09:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    ids = []
    for prefix, activity in (("a", "Done"), ("b", "Finish")):
        imported = client.post(f"/v1/event-logs/import?format=csv&name={prefix}&case_column=case&activity_column=activity&timestamp_column=time",
                               content="\n".join(rows(prefix, activity)).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
        assert imported.status_code == 200, imported.text
        ids.append(imported.json()["log_id"])
    body = {"baseline": {"log_id": ids[0]}, "comparison": {"log_id": ids[1]}}
    assert client.post("/v1/event-logs/comparison", json=body, headers=bearer).status_code == 403
    found = client.post("/v1/event-logs/comparison", json=body, headers=owner)
    assert found.status_code == 200, found.text
    assert found.json()["activities"]["only_comparison"] == ["Finish"]
    assert client.post("/v1/event-logs/comparison", json={"baseline": body["baseline"]}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
