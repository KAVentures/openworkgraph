"""Follower case filters reused across views, and case playback (#370 R7)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_cohorts as cohorts
from server import event_log_playback as playback
from server import event_log_process_map as maps
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
PATHS = {"direct": ["A", "B", "C"], "eventual": ["A", "X", "B", "C"], "reverse": ["B", "A", "C"],
         "loop": ["A", "A", "C"], "tied": ["A", "B", "C"]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    rows = ["case,activity,time"]
    for n, (name, path) in enumerate(PATHS.items()):
        for k, activity in enumerate(path):
            hours = 0 if name == "tied" and k < 2 else k     # A and B share a time in the "tied" case
            rows.append(f"{name},{activity},{(START + timedelta(days=n, hours=hours)).isoformat()}")
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="f", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def _cases(log, rule):
    result = workspace.variants(log, limit=50, cohort={"follows": [rule]})
    return {tuple(v["path"]) for v in result["variants"]}


def test_direct_and_eventual_follows_with_negation_and_ties():
    log = _log()
    assert _cases(log, {"from": "A", "to": "B", "mode": "direct"}) == {("A", "B", "C")}   # direct and tied (same path)
    assert workspace.variants(log, cohort={"follows": [{"from": "A", "to": "B", "mode": "direct"}]})["cases"] == 2
    assert _cases(log, {"from": "A", "to": "B"}) == {("A", "B", "C"), ("A", "X", "B", "C")}
    assert _cases(log, {"from": "A", "to": "B", "negate": True}) == {("B", "A", "C"), ("A", "A", "C")}
    assert _cases(log, {"from": "A", "to": "A"}) == {("A", "A", "C")}                   # repeated activity
    both = workspace.variants(log, cohort={"follows": [{"from": "A", "to": "B"}, {"from": "X", "to": "B", "mode": "direct"}]})
    assert [v["path"] for v in both["variants"]] == [["A", "X", "B", "C"]]
    assert both["cases_before_filter"] == 5 and both["cohort"]["follows"][1]["mode"] == "direct"


def test_the_same_filter_selects_the_same_cases_in_every_view():
    log = _log()
    rule = {"follows": [{"from": "A", "to": "B"}]}
    summary = workspace.summary(log, cohort=rule)
    assert summary["cases"] == 3 and summary["cases_before_filter"] == 5
    activities = {a["activity"]: a["cases"] for a in workspace.activities(log, cohort=rule)["activities"]}
    assert activities == {"A": 3, "B": 3, "C": 3, "X": 1}
    mapped = maps.process_map(log, cohort=rule)
    assert {(t["from"], t["to"]) for t in mapped["transitions"]} == {("A", "B"), ("B", "C"), ("A", "X"), ("X", "B")}
    played = playback.playback(log, cohort=rule)
    assert played["coverage"]["cases"] == 3
    unfiltered = workspace.summary(log)
    assert "cases_before_filter" not in unfiltered and unfiltered["cases"] == 5


def test_bad_follow_rules_are_refused():
    for bad in ([], "A>B", [{"from": "A"}], [{"from": "A", "to": "B", "mode": "sometimes"}],
                [{"from": "A", "to": "B", "negate": "yes"}], [{"from": "A", "to": "B", "extra": 1}],
                [{"from": "A", "to": "B"}] * 6):
        with pytest.raises(workspace.EventLogWorkspaceError):
            cohorts.parse_cohort({"follows": bad}) if bad != [] else cohorts._parse_follows(bad)


def test_playback_follows_the_map_activities_discloses_ties_and_samples_evenly():
    log = _log()
    full = playback.playback(log)
    assert full["activities"][0] == "A" and full["coverage"]["cases"] == 5
    tied = next(c for c in full["cases"] if c["case_id"] == "tied")
    a, b = full["activities"].index("A"), full["activities"].index("B")
    assert tied["events"][:2] == [[4 * 86400, a], [4 * 86400, b]]
    assert full["coverage"]["steps_sharing_a_timestamp"] == 1
    assert full["origin"] == "2024-01-01T00:00:00Z" and full["span_seconds"] == 4 * 86400 + 2 * 3600
    reduced = playback.playback(log, activities=0.7)
    assert "X" not in reduced["activities"] and reduced["activities_hidden"] >= 1
    assert all(step[1] < len(reduced["activities"]) for c in reduced["cases"] for step in c["events"])
    sampled = playback.playback(log, max_cases=2)
    assert sampled["coverage"]["cases_sampled"] and [c["case_id"] for c in sampled["cases"]] == ["direct", "reverse"]
    for bad in ({"activities": 0}, {"max_cases": 0}, {"max_cases": 10_000}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            playback.playback(log, **bad)


def test_real_http_routes_take_the_follows_filter(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n2,B,2024-01-02T00:00:00Z\n2,A,2024-01-02T01:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    rule = json.dumps([{"from": "A", "to": "B", "mode": "direct"}])
    for view in ("summary", "variants", "activities", "kpis", "process-map", "playback", "dotted-chart",
                 "waiting-time", "drift", "model-discovery"):
        result = client.get(base + "/" + view, params={"follows": rule}, headers=headers)
        assert result.status_code == 200, (view, result.text)
        assert result.json().get("cohort", {}).get("follows"), view
    assert client.get(base + "/summary", params={"follows": rule}, headers=headers).json()["cases"] == 1
    assert client.get(base + "/summary", params={"follows": "not json"}, headers=headers).status_code == 422
    assert client.get(base + "/summary", params={"follows": "[]"}, headers=headers).status_code == 200
    bad = json.dumps([{"from": "A", "to": "B", "mode": "nope"}])
    assert client.get(base + "/variants", params={"follows": bad}, headers=headers).status_code == 422
    assert client.get(base + "/playback", headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_filter_and_playback_within_budget():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        for s in range(5):
            rows.append(f"c{c},S{(s + c % 3) % 7},{(START + timedelta(seconds=c * 10 + s)).isoformat()}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    rule = {"follows": [{"from": "S1", "to": "S4"}, {"from": "S2", "to": "S3", "mode": "direct"}]}
    started = time.perf_counter()
    variants = workspace.variants(log, cohort=rule)
    played = playback.playback(log, cohort=rule, max_cases=500)
    assert time.perf_counter() - started < 30
    assert 0 < variants["cases"] < 200_000 and played["coverage"]["events_shown"] <= playback.MAX_POINTS
