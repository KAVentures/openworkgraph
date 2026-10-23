"""What-if simulation calibrated from event logs (roadmap #326 C)."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _single(distribution: str, servers: int, seed: int = 1) -> dict:
    return sim.simulate({"label": "", "arrivals_per_hour": 0.5 * servers, "paths": [(["A"], 1.0)],
                         "activities": {"A": {"minutes": 60, "resources": servers}},
                         "distribution": distribution, "cases": 20000, "seed": seed})


@pytest.mark.parametrize("distribution,servers,expected_wait_hours", [
    ("exponential", 1, 1.0),   # M/M/1, rho 0.5: Wq = rho / (mu - lambda)
    ("fixed", 1, 0.5),         # M/D/1 (Pollaczek-Khinchine): Wq = rho / (2 mu (1 - rho))
    ("exponential", 2, 1 / 3),  # M/M/2, rho 0.5: Erlang C wait probability 1/3, Wq = 1/3 h
])
def test_engine_matches_queueing_theory(distribution, servers, expected_wait_hours):
    result = _single(distribution, servers)
    wait = result["activities"][0]["mean_wait_seconds"] / 3600
    assert wait == pytest.approx(expected_wait_hours, rel=0.08)
    assert result["cycle_time_seconds"]["mean"] / 3600 == pytest.approx(expected_wait_hours + 1, rel=0.05)
    assert result["activities"][0]["utilization"] == pytest.approx(0.5, abs=0.03)
    assert result["unstable"] is False


def test_simulation_is_deterministic_per_seed_and_flags_overload():
    assert _single("exponential", 1, seed=5) == _single("exponential", 1, seed=5)
    assert _single("exponential", 1, seed=5) != _single("exponential", 1, seed=6)
    overloaded = sim.simulate({"label": "", "arrivals_per_hour": 2, "paths": [(["A"], 1.0)],
                               "activities": {"A": {"minutes": 60, "resources": 1}},
                               "distribution": "fixed", "cases": 200, "seed": 1})
    assert overloaded["unstable"] is True and overloaded["activities"][0]["offered_load"] == pytest.approx(2)


def test_calibration_measures_the_log():
    cal = sim.calibrate(_log())
    # 60 cases start (first event) from day 1 08:15 to day 20 11:00: 59 gaps over 19 days 2.75 h.
    assert cal["arrivals_per_hour"] == pytest.approx(59 / (19 * 24 + 2.75), rel=1e-5)
    paths = {tuple(p["activities"]): p["weight"] for p in cal["paths"]}
    assert paths[("Receive", "Check", "Approve", "Close")] == pytest.approx(36 / 60)
    assert sum(paths.values()) == pytest.approx(1) and cal["paths_cover_cases_share"] == 1
    fix = cal["activities"]["Fix"]
    assert fix == {"minutes": 180.0, "resources": 1, "time_source": "median observed gap"}
    assert cal["activities"]["Receive"]["minutes"] == 0 and cal["activities"]["Approve"]["resources"] == 2


def test_what_if_compares_a_changed_design_with_the_measured_baseline():
    log_id = _log()
    result = sim.what_if(log_id, {"label": "More fixers", "activities": {"Fix": {"resources": 4}, "Check": {"resources": 4}},
                                  "cases": 5000})
    assert result["scenario"]["activities"]["Fix"]["resources"] == 4
    assert result["baseline"]["cycle_time_seconds"]["p50"] > result["result"]["cycle_time_seconds"]["p50"]
    assert result["median_cycle_time_change_seconds"] < 0
    fix = {a["activity"]: a for a in result["result"]["activities"]}["Fix"]
    assert fix["mean_wait_seconds"] < {a["activity"]: a for a in result["baseline"]["activities"]}["Fix"]["mean_wait_seconds"]
    assert result["simulated_not_observed"] is True and result["forecast_or_guarantee"] is False
    assert any("include any waiting" in line for line in result["assumptions"])

    redesign = sim.what_if(log_id, {"paths": [{"activities": ["Receive", "Validate", "Approve", "Close"], "weight": 1}],
                                    "activities": {"Validate": {"minutes": 10, "resources": 2}}, "cases": 500})
    assert set(redesign["scenario"]["activities"]) == {"Receive", "Validate", "Approve", "Close"}
    assert redesign["result"]["cases_measured"] == 450


@pytest.mark.parametrize("scenario", [
    "x", {"format": "other"}, {"unknown": 1}, {"arrivals_per_hour": 0}, {"arrivals_per_hour": float("nan")},
    {"arrivals_per_hour": True}, {"paths": []}, {"paths": [{"activities": []}]},
    {"paths": [{"activities": ["A"], "weight": 0}]}, {"paths": [{"activities": ["Brand new"]}]},
    {"activities": {"Fix": {"resources": 0}}}, {"activities": {"Fix": {"resources": 1.5}}},
    {"activities": {"Fix": {"minutes": -1}}}, {"activities": {"Fix": {"speed": 2}}},
    {"distribution": "normal"}, {"cases": 5}, {"cases": 10 ** 6},
])
def test_invalid_scenarios_are_refused(scenario):
    log_id = _log()
    with pytest.raises(workspace.EventLogWorkspaceError):
        sim.what_if(log_id, scenario)


def test_real_http_simulation_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

rows = ["case,activity,time,who"] + [f"c{i},{a},2024-01-01T{8 + i // 6:02d}:{(i % 6) * 10 + n * 2:02d}:00Z,u{n}"
                                     for i in range(30) for n, a in enumerate(["A", "B"])]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=who",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"]
    assert client.get(base + "/simulation/calibration").status_code == 401
    cal = client.get(base + "/simulation/calibration", headers=headers)
    assert cal.status_code == 200 and cal.json()["activities"]["B"]["minutes"] == 2.0, cal.text
    ok = client.post(base + "/simulate", json={"cases": 200, "activities": {"B": {"resources": 2}}}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["scenario"]["activities"]["B"]["resources"] == 2 and ok.headers["cache-control"] == "no-store"
    assert client.post(base + "/simulate", json={"cases": 5}, headers=headers).status_code == 422
    assert client.post(base + "/simulate", content=b"x", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_routing_helpers():
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/event_log_workspace.js','utf8'),scope);
const m=scope.window.__owgEventLogModel;
const plain=v=>JSON.parse(JSON.stringify(v));
assert.deepEqual(plain(m.parseWeightedPaths('0.7: A > B\n\n.3: A → C\nA>D')),[{activities:['A','B'],weight:0.7},{activities:['A','C'],weight:0.3},{activities:['A','D'],weight:1}]);
assert.throws(()=>m.parseWeightedPaths(''));assert.throws(()=>m.parseWeightedPaths('0.5:'));
assert.equal(m.formatWeightedPaths([{activities:['A','B'],weight:0.6}]),'0.600: A > B');
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
