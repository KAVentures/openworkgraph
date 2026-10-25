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
    assert fix == {"minutes": 180.0, "mean_minutes": 180.0, "resources": 1, "time_source": "median observed gap"}
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
const exact=[{activities:[' Review > ','Close→','__proto__'],weight:0.000001}];
assert.deepEqual(plain(m.parseWeightedPaths(m.formatWeightedPaths(exact))),exact);
assert.deepEqual(plain(m.parseWeightedPaths(m.formatWeightedPaths([{activities:['A'],weight:0.1234567}]))),[{activities:['A'],weight:0.1234567}]);
assert.throws(()=>m.parseWeightedPaths('[{"activities":["A"],"weight":0}]'));
assert.throws(()=>m.parseWeightedPaths('[{"activities":[3],"weight":1}]'));
assert.deepEqual(plain(m.parseWeightedPaths('[{"activities":["A"],"weight":0},{"activities":["B"],"weight":1}]')),[{activities:['A'],weight:0},{activities:['B'],weight:1}]);
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_calibration_preserves_small_observed_variant_weights():
    rows=['case,activity,time,who']+[f'c{i},{"Rare" if i==0 else "Common"},2024-01-01T00:00:{i%60:02d}Z,person' for i in range(1001)]
    log=workspace.import_log(BytesIO(('\n'.join(rows)).encode()),name='weights',source_format='csv',mapping=MAPPING)['log_id']
    weights={tuple(p['activities']):p['weight'] for p in sim.calibrate(log)['paths']}
    assert weights[('Rare',)]==1/1001
    assert weights[('Common',)]==1000/1001


def test_calibration_excludes_long_cases_whole_and_measures_the_selected_population():
    rows=['case,activity,time,who']
    for case,hour in [('first',0),('second',1)]:
        rows += [f'{case},A,2024-01-01T{hour:02}:00:00Z,person',
                 f'{case},B,2024-01-01T{hour:02}:01:00Z,person']
    rows += [f'long,A,2024-01-01T10:{n//60:02}:{n%60:02}Z,outsider{n}' for n in range(101)]
    log=workspace.import_log(BytesIO('\n'.join(rows).encode()),name='complete paths',source_format='csv',mapping=MAPPING)['log_id']
    cal=sim.calibrate(log)
    assert cal['paths']==[{'activities':['A','B'],'weight':1}]
    assert cal['observed_cases']==3 and cal['calibrated_cases']==2
    assert cal['paths_cover_cases_share']==pytest.approx(2/3,abs=1e-6)
    assert cal['excluded_case_reasons']=={'path_length_limit':1,'activity_limit':0,'variant_limit':0}
    assert cal['arrival_rate_basis']=='selected_complete_cases' and cal['arrivals_per_hour']==1
    assert cal['activities']['A']['resources']==1 and cal['activities']['B']['minutes']==1
    result=sim.what_if(log,{'cases':10})
    assert result['calibration']['calibrated_cases']==2
    assert result['calibration']['excluded_case_reasons']==cal['excluded_case_reasons']


def test_calibration_limits_partition_whole_cases_without_truncating_or_missing_parameters(monkeypatch):
    monkeypatch.setattr(sim,'MAX_PATH_LENGTH',3)
    monkeypatch.setattr(sim,'MAX_ACTIVITIES',2)
    paths=[['A','B'],['A','B'],['A','A','B'],['A','C'],['A','A','A','B']]
    rows=['case,activity,time,who']+[f'c{case},{a},2024-01-01T{case:02}:0{n}:00Z,person'
        for case,path in enumerate(paths) for n,a in enumerate(path)]
    log=workspace.import_log(BytesIO('\n'.join(rows).encode()),name='limits',source_format='csv',mapping=MAPPING)['log_id']
    cal=sim.calibrate(log,max_paths=1)
    assert cal['calibrated_cases']==2 and cal['observed_cases']==5
    assert cal['excluded_case_reasons']=={'path_length_limit':1,'activity_limit':1,'variant_limit':1}
    assert cal['paths']==[{'activities':['A','B'],'weight':1}] and set(cal['activities'])=={'A','B'}
    both=sim.calibrate(log,max_paths=2)
    assert both['calibrated_cases']==3
    assert any(p['activities']==['A','A','B'] for p in both['paths'])
    assert sum(both['excluded_case_reasons'].values())+both['calibrated_cases']==both['observed_cases']


def test_no_supported_complete_cases_cannot_produce_a_baseline(monkeypatch):
    monkeypatch.setattr(sim,'MAX_PATH_LENGTH',1)
    log=_log()
    cal=sim.calibrate(log)
    assert cal['paths']==[] and cal['activities']=={} and cal['arrivals_per_hour'] is None
    assert cal['calibrated_cases']==0 and cal['paths_cover_cases_share']==0
    assert cal['excluded_case_reasons']['path_length_limit']==cal['observed_cases']==60
    with pytest.raises(workspace.EventLogWorkspaceError,match='no complete case paths'):
        sim.what_if(log,{'arrivals_per_hour':1,'paths':[{'activities':['A'],'weight':1}],
                         'activities':{'A':{'minutes':1}},'cases':10})


def test_single_selected_case_needs_an_explicit_disclosed_arrival_assumption():
    rows=['case,activity,time,who','one,A,2024-01-01T00:00:00Z,person']
    log=workspace.import_log(BytesIO('\n'.join(rows).encode()),name='single case',source_format='csv',mapping=MAPPING)['log_id']
    with pytest.raises(workspace.EventLogWorkspaceError,match='too few cases'):
        sim.what_if(log,{'cases':10})
    result=sim.what_if(log,{'cases':10,'arrivals_per_hour':2})
    assert result['calibration']['arrivals_per_hour'] is None
    assert result['baseline_arrival_rate_source']=='user_assumption' and result['baseline_arrivals_per_hour']==2
    assert result['baseline']['cases_measured']==result['result']['cases_measured']==9
    assert any("user's explicit rate" in line for line in result['assumptions'])
