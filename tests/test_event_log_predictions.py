"""Predictive monitoring for open cases (roadmap #326 G1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_predictions as predictions
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
FAST = [("Receive", 0), ("Check", 1), ("Close", 2)]
SLOW = [("Receive", 0), ("Fix", 1), ("Check", 21), ("Close", 22)]   # fixing adds 20 hours


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(completed=200, open_cases=(("open-fix", SLOW[:2]), ("open-check", FAST[:2]))):
    rows = ["case,activity,time,who"]
    for c in range(completed):
        for activity, hours in (SLOW if c % 4 == 0 else FAST):
            rows.append(f"c{c},{activity},{(START + timedelta(hours=c * 3 + hours)).isoformat()},person{c % 3}")
    late = START + timedelta(hours=completed * 3 + 30)
    for case, steps in open_cases:
        for activity, hours in steps:
            rows.append(f"{case},{activity},{(late + timedelta(hours=hours)).isoformat()},person0")
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="pred", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def _by_case(result):
    return {p["case_id"]: p for p in result["predictions"]}


def test_remaining_time_comes_from_comparable_states_and_beats_the_naive_baseline():
    result = predictions.predict(_log(), {"end_activities": ["Close"]})
    assert (result["completed_cases"], result["open_cases"], result["predicted_open_cases"]) == (200, 2, 2)
    fix, check = _by_case(result)["open-fix"], _by_case(result)["open-check"]
    assert fix["basis"] == check["basis"] == "same_activity_and_step"
    assert fix["median_remaining_seconds"] == pytest.approx(21 * 3600)
    assert check["median_remaining_seconds"] == pytest.approx(3600)
    assert fix["comparable_cases"] == 50 and check["comparable_cases"] == 150
    assert result["predictions"][0]["case_id"] == "open-fix"  # longest expected remaining first
    backtest = result["backtest"]
    assert backtest["available"] and backtest["scored_prefixes"] == backtest["test_prefixes"]
    assert backtest["improvement_over_naive"] > 0.5 and backtest["p10_p90_coverage"] >= 0.8
    assert result["resources_used"] is False and "person0" not in str(result) and "person1" not in str(result)


def test_outcome_risk_with_intervals_and_brier_against_the_base_rate():
    result = predictions.predict(_log(), {"end_activities": ["Close"],
                                          "outcome": {"kind": "duration_over", "seconds": 10 * 3600}})
    fix, check = _by_case(result)["open-fix"], _by_case(result)["open-check"]
    assert fix["outcome_probability"] == 1 and check["outcome_probability"] == 0
    assert fix["outcome_probability_ci95"][0] > 0.9 and check["outcome_probability_ci95"][1] < 0.05
    assert result["backtest"]["brier_score"] < result["backtest"]["base_rate_brier_score"]
    rework = predictions.predict(_log(), {"end_activities": ["Close"],
                                          "outcome": {"kind": "contains_activity", "activity": "Fix"}})
    assert _by_case(rework)["open-fix"]["outcome_probability"] == 1


def test_sparse_states_fall_back_and_unsupported_cases_are_reported():
    result = predictions.predict(_log(open_cases=(("odd", [("Receive", 0), ("Check", 1), ("Check", 2), ("Check", 3)]),)),
                                 {"end_activities": ["Close"], "min_support": 20})
    assert _by_case(result)["odd"]["basis"] == "same_activity"  # nobody had Check as a fourth event
    tiny = predictions.predict(_log(completed=8), {"end_activities": ["Close"], "min_support": 20})
    assert tiny["predicted_open_cases"] == 0 and tiny["unsupported_open_cases"] == 2
    assert tiny["backtest"]["available"] is False


def test_cohorts_limit_and_settings_validation():
    log = _log()
    limited = predictions.predict(log, {"end_activities": ["Close"], "limit": 1})
    assert len(limited["predictions"]) == 1 and limited["predictions_truncated"] is True
    cohort = predictions.predict(log, {"end_activities": ["Close"], "cohort": {"activity": "Fix"}, "min_support": 10})
    assert cohort["completed_cases"] == 50 and set(_by_case(cohort)) == {"open-fix"}
    for bad, message in [({"end_activities": []}, "invalid"),
                         ({"end_activities": ["Close"], "outcome": {"kind": "duration_over"}}, "needs seconds"),
                         ({"end_activities": ["Close"], "outcome": {"kind": "contains_activity", "seconds": 5}}, "only it"),
                         ({"end_activities": ["Close"], "backtest_share": 1.5}, "invalid"),
                         ({"end_activities": ["Close"], "model": "neural"}, "invalid")]:
        with pytest.raises(workspace.EventLogWorkspaceError, match=message):
            predictions.predict(log, bad)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
start = datetime(2024, 1, 1, tzinfo=timezone.utc)
rows = ["case,activity,time"]
for c in range(40):
    for i, a in enumerate(("A", "B", "C")):
        rows.append(f"c{c},{a},{(start + timedelta(hours=c * 2 + i)).isoformat()}")
rows.append(f"open,A,{(start + timedelta(hours=200)).isoformat()}")
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=("\n".join(rows) + "\n").encode(),
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/predictions"
    body = {"end_activities": ["C"], "min_support": 10}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json=body, headers=bearer).status_code in (401, 403)
    result = client.post(path, json=body, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["predictions"][0]["case_id"] == "open"
    assert result.json()["predictions"][0]["median_remaining_seconds"] == 7200
    assert client.post(path, json={"end_activities": []}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_stay_interactive():
    import time
    rows = ["case,activity,time,who"]
    for c in range(250_000):
        for activity, hours in (SLOW if c % 4 == 0 else FAST)[: (2 if c % 50 == 0 else None)]:
            rows.append(f"c{c},{activity},{(START + timedelta(minutes=c * 3 + hours * 60)).isoformat()},r")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})["log_id"]
    started = time.perf_counter()
    result = predictions.predict(log, {"end_activities": ["Close"], "outcome": {"kind": "duration_over", "seconds": 36000}})
    assert time.perf_counter() - started < 60 and result["open_cases"] == 5000 and result["backtest"]["available"]
