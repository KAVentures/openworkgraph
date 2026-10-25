"""Shared resource pools across activities in what-if simulation (#370 R14)."""
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


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _two_queues(pools=None, cases=40000, seed=7):
    # Half the cases need A, half need B; each takes 60 min on average and arrives at 1 case/h in total.
    return sim.simulate({"label": "", "arrivals_per_hour": 1.0, "paths": [(["A"], 0.5), (["B"], 0.5)],
                         "activities": {"A": {"minutes": 60, "resources": 1}, "B": {"minutes": 60, "resources": 1}},
                         "distribution": "exponential", "cases": cases, "seed": seed, "pools": pools or {}})


def test_pooling_two_dedicated_servers_matches_erlang_c():
    dedicated = _two_queues()
    # Two separate M/M/1 queues at rho 0.5: Wq = 1 h each.
    for row in dedicated["activities"]:
        assert row["mean_wait_seconds"] / 3600 == pytest.approx(1.0, rel=0.08) and "pool" not in row
    pooled = _two_queues({"Team": {"resources": 2, "activities": ["A", "B"]}})
    # One M/M/2 queue at rho 0.5: Erlang C wait probability 1/3, Wq = 1/3 h.
    team = pooled["pools"][0]
    assert team["mean_wait_seconds"] / 3600 == pytest.approx(1 / 3, rel=0.1)
    assert team["utilization"] == pytest.approx(0.5, abs=0.03) and team["offered_load"] == pytest.approx(0.5)
    assert {r["activity"]: (r["pool"], r["resources"]) for r in pooled["activities"]} == {"A": ("Team", 2), "B": ("Team", 2)}
    assert pooled["cycle_time_seconds"]["mean"] < dedicated["cycle_time_seconds"]["mean"]


def test_pools_and_dedicated_activities_mix_and_overload_is_flagged_per_pool():
    mixed = sim.simulate({"label": "", "arrivals_per_hour": 1.0, "paths": [(["A", "C"], 0.5), (["B", "C"], 0.5)],
                          "activities": {"A": {"minutes": 60, "resources": 1}, "B": {"minutes": 60, "resources": 1},
                                         "C": {"minutes": 30, "resources": 1}},
                          "distribution": "fixed", "cases": 2000, "seed": 1,
                          "pools": {"Team": {"resources": 1, "activities": ["A", "B"]}}})
    rows = {r["activity"]: r for r in mixed["activities"]}
    assert "pool" not in rows["C"] and rows["C"]["offered_load"] == pytest.approx(0.5)
    assert rows["A"]["offered_load"] == pytest.approx(1.0) and mixed["pools"][0]["overloaded"] is True
    assert mixed["unstable"] is True


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_what_if_pools_only_the_scenario():
    log = _log()
    result = sim.what_if(log, {"label": "Shared reviewers", "cases": 2000,
                               "pools": {"Reviewers": {"resources": 3, "activities": ["Check", "Fix"]}}})
    assert result["scenario"]["pools"] == {"Reviewers": {"resources": 3, "activities": ["Check", "Fix"]}}
    assert "pools" not in result["baseline"] and result["result"]["pools"][0]["pool"] == "Reviewers"
    assert any("shared pool" in line for line in result["assumptions"])
    assert sim.what_if(log, {"cases": 500})["scenario"]["pools"] is None


@pytest.mark.parametrize("pools", [
    [], {}, {"": {"resources": 1, "activities": ["Check"]}}, {"T": {"resources": 0, "activities": ["Check"]}},
    {"T": {"resources": 1.5, "activities": ["Check"]}}, {"T": {"resources": 1, "activities": []}},
    {"T": {"resources": 1, "activities": ["Nowhere"]}}, {"T": {"resources": 1, "activities": ["Check", "Check"]}},
    {"T": {"resources": 1, "activities": ["Check"]}, "U": {"resources": 1, "activities": ["Check"]}},
    {"T": {"resources": 1}}, {"T": {"resources": 1, "activities": ["Check"], "speed": 2}},
    {"T": {"resources": 1, "activities": [["Check"]]}}, {"T": {"resources": 1, "activities": "Check"}}, {"T": 3},
    {f"T{i}": {"resources": 1, "activities": ["Check"]} for i in range(21)},
])
def test_invalid_pools_are_refused(pools):
    with pytest.raises(workspace.EventLogWorkspaceError):
        sim.what_if(_log(), {"cases": 100, "pools": pools})


def test_real_http_route_accepts_pools(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-01T{8 + i // 6:02d}:{(i % 6) * 10 + n * 2:02d}:00Z"
                                 for i in range(30) for n, a in enumerate(["A", "B"])]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    ok = client.post(base + "/simulate", json={"cases": 200, "pools": {"Desk": {"resources": 2, "activities": ["A", "B"]}}},
                     headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["result"]["pools"][0]["activities"] == ["A", "B"]
    assert client.post(base + "/simulate", json={"cases": 200, "pools": {"Desk": {"resources": 2, "activities": ["Z"]}}},
                       headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
