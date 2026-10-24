"""Organizational mining over saved event logs (roadmap #326 F1)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from server import event_log_organization as org
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "c1": [("A", "alice"), ("B", "bob"), ("C", "alice")],
    "c2": [("A", "alice"), ("B", "bob"), ("C", "bob")],
    "c3": [("A", "carol"), ("B", "carol")],
    "c4": [("A", "robot"), ("B", "alice")],
}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases=CASES):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time", "who"])
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for n, (case, steps) in enumerate(cases.items()):
        for i, (activity, who) in enumerate(steps):
            writer.writerow([case, activity, (start + timedelta(days=n, minutes=i)).isoformat(), who])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="org", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_handover_and_working_together_are_pseudonymous_by_default():
    result = org.organization(_log(), automation_resources=["robot"])
    assert [(r["resource"], r["events"], r["cases"], r["kind"]) for r in result["resources"]] == [
        ("Resource 01", 4, 3, "unclassified"), ("Resource 02", 3, 2, "unclassified"),
        ("Resource 03", 2, 1, "unclassified"), ("Resource 04", 1, 1, "automation")]
    assert result["handover_of_work"] == [
        {"from": "Resource 01", "to": "Resource 02", "handovers": 2, "cases": 2},
        {"from": "Resource 02", "to": "Resource 01", "handovers": 1, "cases": 1},
        {"from": "Resource 04", "to": "Resource 01", "handovers": 1, "cases": 1}]
    assert result["cases_with_handover"] == 3
    assert result["working_together"] == [{"resources": ["Resource 01", "Resource 02"], "shared_cases": 2},
                                          {"resources": ["Resource 01", "Resource 04"], "shared_cases": 1}]
    matrix = result["activity_resource_matrix"]
    assert matrix["activities"] == ["A", "B", "C"]
    assert matrix["events"][matrix["activities"].index("A")] == [2, 0, 1, 1]
    text = json.dumps(result)
    for name in ("alice", "bob", "carol", "robot"):
        assert name not in text
    assert result["privacy"]["per_person_durations_or_speed_computed"] is False
    assert "seconds" not in text and "duration" not in text.replace("per_person_durations", "")


def test_names_only_when_asked_and_cohorts_apply():
    log = _log()
    shown = org.organization(log, names="shown")
    assert shown["handover_of_work"][0] == {"from": "alice", "to": "bob", "handovers": 2, "cases": 2}
    only_bob = org.organization(log, names="shown", cohort={"resource": "bob"})
    assert {r["resource"] for r in only_bob["resources"]} == {"alice", "bob"}
    assert only_bob["coverage"]["cases"] == 2
    with pytest.raises(workspace.EventLogWorkspaceError):
        org.organization(log, names="everyone")


def test_resources_beyond_the_visible_set_fold_into_one_bucket(monkeypatch):
    monkeypatch.setattr(org, "MAX_RESOURCES", 2)
    result = org.organization(_log())
    assert result["resources_truncated"] is True and len(result["resources"]) == 2
    assert {"from": "Other resources", "to": "Resource 01", "handovers": 1, "cases": 1} in result["handover_of_work"]
    assert "carol" not in json.dumps(result)


def test_crowded_cases_are_excluded_from_pairs_and_reported(monkeypatch):
    monkeypatch.setattr(org, "MAX_RESOURCES_PER_CASE", 2)
    crowded = {**CASES, "c5": [("A", "alice"), ("B", "bob"), ("C", "carol")]}
    result = org.organization(_log(crowded))
    assert result["working_together_excluded_cases"] == 1
    assert {"resources": ["Resource 01", "Resource 02"], "shared_cases": 2} in result["working_together"]


def test_logs_without_resources_report_empty_coverage():
    cases = {"c1": [("A", ""), ("B", "")]}
    result = org.organization(_log(cases))
    assert result["resources"] == [] and result["handover_of_work"] == []
    assert result["coverage"]["resource_share"] == 0


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_stay_interactive():
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time", "who"])
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for case in range(200_000):
        for step in range(5):
            writer.writerow([f"c{case}", f"S{step}", (start + timedelta(seconds=case * 10 + step)).isoformat(),
                             f"r{(case + step) % 150}"])
    log = workspace.import_log(BytesIO(out.getvalue().encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})
    started = time.perf_counter()
    result = org.organization(log["log_id"])
    assert time.perf_counter() - started < 30 and result["coverage"]["events"] == 1_000_000


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time,who\n1,A,2024-01-01T00:00:00Z,ann\n1,B,2024-01-01T00:01:00Z,ben\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&resource_column=who", content=doc,
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    path = "/v1/event-logs/" + made.json()["log_id"] + "/organization"
    assert client.get(path).status_code in (401, 403)
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["handover_of_work"] == [{"from": "Resource 01", "to": "Resource 02", "handovers": 1, "cases": 1}]
    assert "ann" not in result.text
    assert client.get(path + "?names=shown", headers=headers).json()["resources"][0]["resource"] in {"ann", "ben"}
    assert client.get(path + "?names=all", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
