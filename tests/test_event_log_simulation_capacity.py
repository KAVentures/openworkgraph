"""R14 capacity stress grid: demand, design, complete target accounting, portable inputs."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_capacity as capacity
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture_log():
    return workspace.import_log(BytesIO(
        b"case,activity,time,who\na,Receive,2026-01-01T00:00:00Z,r\n"
        b"a,Finish,2026-01-01T01:00:00Z,r\nb,Receive,2026-01-02T00:00:00Z,r\n"
        b"b,Finish,2026-01-02T01:00:00Z,r\nc,Receive,2026-01-03T00:00:00Z,r\n"
        b"c,Finish,2026-01-03T01:00:00Z,r\n"),
        name="capacity", source_format="csv", mapping={"case_column":"case","activity_column":"activity",
            "timestamp_column":"time","resource_column":"who"})["log_id"]


def proposal():
    return {"format": capacity.FORMAT, "baseline": {"cases": 400, "seed": 22, "distribution": "fixed"},
            "designs": [{"label":"Two reviewers","activities":{"Finish":{"resources":2}}}],
            "arrival_rates_per_hour":[1.5, .1], "replications":3,
            "target":{"cycle_time_minutes":240,"minimum_completion_share":.9}}


def test_real_grid_one_calibration_and_deterministic_replay(monkeypatch):
    log = fixture_log()
    original = sim.calibrate
    calls = []
    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(sim, "calibrate", counted)
    spec = proposal()
    snapshot = deepcopy(spec)
    report = capacity.run_grid(log, spec)
    assert spec == snapshot
    assert calls == [(log, None)]
    assert report["seeds"] == [22, 23, 24]
    assert report["simulated_cases"] == 4800
    assert report["single_calibration_used"]
    assert not report["production_capacity_certified"]
    assert not report["execution_authorized"]
    assert len(report["calibration_and_inputs_sha256"]) == 64
    assert [c["arrival_rate_per_hour"] for c in report["cells"]] == [.1, 1.5, .1, 1.5]
    assert report["cells"][0]["status"] == "met_in_all_runs"
    assert report["cells"][1]["status"] == "overloaded"
    assert report["cells"][3]["overloaded_replications"] == 0
    assert report["tested_envelope"][0]["highest_tested_rate_meeting_target"] == .1
    for cell in report["cells"]:
        for run in cell["runs"]:
            assert run["post_warmup_arrivals"] == 360
            assert run["completed_on_time"] + run["completed_late"] + run["abandoned"] + run["unresolved"] == 360
    assert capacity.run_grid(log, report["inputs"]) == report


def test_same_design_uses_identical_random_streams():
    spec = proposal()
    spec["designs"] = [{"label":"Unchanged"}]
    report = capacity.run_grid(fixture_log(), spec)
    for left, right in zip(report["cells"][:2], report["cells"][2:]):
        assert left["runs"] == right["runs"]


def test_complete_target_bounds_include_late_abandoned_and_unresolved():
    raw = {"cases_simulated":100, "cases_measured":60, "cases_abandoned_after_warmup":10,
           "unstable":False, "activities":[], "cycle_time_seconds":{"p90":100}}
    run = capacity.summarize_run(raw, [10]*50+[100]*10, 50, 1)
    assert run["post_warmup_arrivals"] == 90
    assert run["completed_on_time"] == 50
    assert run["completed_late"] == 10
    assert run["abandoned"] == 10 and run["unresolved"] == 20
    assert run["target_share_lower"] == pytest.approx(50/90)
    assert run["target_share_upper"] == pytest.approx(70/90)
    assert capacity.summarize_cell("A",1,[run],.7)["status"] == "indeterminate"
    assert capacity.summarize_cell("A",1,[run],.8)["status"] == "not_met_in_all_runs"
    assert capacity.summarize_cell("A",1,[run],.5)["status"] == "met_in_all_runs"
    assert capacity.summarize_cell("A",1,[{**run,"overloaded":True}],.5)["status"] == "overloaded"


def test_no_partial_or_impossible_accounting():
    raw = {"cases_simulated":100,"cases_measured":80,"cases_abandoned_after_warmup":11,
           "unstable":False,"activities":[],"cycle_time_seconds":{"p90":100}}
    with pytest.raises(workspace.EventLogWorkspaceError, match="accounting"):
        capacity.summarize_run(raw, [1]*80, 50, 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="accounting"):
        capacity.summarize_run({**raw,"cases_abandoned_after_warmup":0}, [1],50,1)


def test_nonmonotonic_envelope_does_not_interpolate():
    cells = [{"design":"D","arrival_rate_per_hour":r,"status":s}
             for r,s in [(1,"met_in_all_runs"),(2,"indeterminate"),(3,"met_in_all_runs")]]
    envelope = capacity._envelope(cells)[0]
    assert envelope["highest_tested_rate_meeting_target"] == 3
    assert envelope["tested_rates_meeting_target"] == [1,3]
    assert envelope["tested_rates_not_demonstrated"] == [2]
    assert envelope["nonmonotonic_tested_pattern"]
    assert envelope["untested_rates_have_no_result"]


@pytest.mark.parametrize("change", [
    lambda s:s.update(format="bad"),
    lambda s:s.update(unknown=True),
    lambda s:s.update(arrival_rates_per_hour=[1,1]),
    lambda s:s.update(arrival_rates_per_hour=[1,float("nan")]),
    lambda s:s.update(arrival_rates_per_hour=[1,True]),
    lambda s:s.update(arrival_rates_per_hour=[1]),
    lambda s:s.update(replications=1),
    lambda s:s.update(replications=True),
    lambda s:s["baseline"].update(seed=2**31),
    lambda s:s["baseline"].update(cases=3001),
    lambda s:s["baseline"].update(distribution="normal"),
    lambda s:s["baseline"].update(arrivals_per_hour=2),
    lambda s:s["target"].update(minimum_completion_share=0),
    lambda s:s["target"].update(cycle_time_minutes=float("inf")),
    lambda s:s["designs"][0].update(label="Baseline"),
    lambda s:s["designs"][0].update(label="A\nB"),
    lambda s:s["designs"][0].update(seed=3),
    lambda s:s["designs"][0].update(activities={"Finish":{"secret":"no"}}),
    lambda s:s.update(designs=[{"label":str(i)} for i in range(4)]),
    lambda s:s.update(arrival_rates_per_hour=list(range(1,9)),designs=[{"label":str(i)} for i in range(3)]),
    lambda s:(s["baseline"].update(cases=3000),s.update(replications=8,arrival_rates_per_hour=list(range(1,9)))),
])
def test_refuses_invalid_or_unbounded_inputs(change):
    spec = proposal()
    change(spec)
    with pytest.raises(workspace.EventLogWorkspaceError):
        capacity.normalize(spec)


def test_invalid_design_is_rejected_before_any_run(monkeypatch):
    log = fixture_log()
    spec = proposal()
    spec["designs"][0]["activities"] = {"Not in paths":{"resources":3}}
    monkeypatch.setattr(sim, "simulate", lambda *args:pytest.fail("partial grid executed"))
    with pytest.raises(workspace.EventLogWorkspaceError, match="absent"):
        capacity.run_grid(log, spec)


def test_work_budget_checked_before_any_run(monkeypatch):
    log = fixture_log()
    monkeypatch.setattr(capacity, "MAX_ROUTE_STEPS", 1)
    monkeypatch.setattr(sim, "simulate", lambda *args:pytest.fail("partial grid executed"))
    with pytest.raises(workspace.EventLogWorkspaceError, match="route-step budget"):
        capacity.run_grid(log, proposal())


def test_concurrent_run_refused_and_guard_recovers(monkeypatch):
    log = fixture_log()
    entered, release = threading.Event(), threading.Event()
    original = sim.calibrate
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(sim, "calibrate", held)
    errors = []
    def work():
        try:
            capacity.run_grid(log, proposal())
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(workspace.EventLogWorkspaceError, match="Another local capacity"):
            capacity.run_grid(log, proposal())
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert capacity.run_grid(log, proposal())["single_calibration_used"]


def test_failed_grid_releases_guard(monkeypatch):
    log = fixture_log()
    original = sim.simulate
    def fail(*args):
        raise ValueError("out of range")
    monkeypatch.setattr(sim, "simulate", fail)
    with pytest.raises(workspace.EventLogWorkspaceError, match="time range"):
        capacity.run_grid(log, proposal())
    monkeypatch.setattr(sim, "simulate", original)
    assert capacity.run_grid(log, proposal())["cells"]


def test_actual_dashboard_only_http_contract(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as ws
from server.event_log_simulation_capacity import FORMAT, REPORT_FORMAT
log=ws.import_log(BytesIO(b"case,activity,time\na,A,2026-01-01T00:00:00Z\na,B,2026-01-01T01:00:00Z\nb,A,2026-01-02T00:00:00Z\nb,B,2026-01-02T01:00:00Z\n"),
    name="capacity",source_format="csv",mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
path=f"/v1/event-logs/{log}/simulation/capacity-grid"
body={"format":FORMAT,"baseline":{"cases":100},"designs":[{"label":"Two","activities":{"B":{"resources":2}}}],
      "arrival_rates_per_hour":[.1,2],"replications":2,"target":{"cycle_time_minutes":240,"minimum_completion_share":.9}}
with TestClient(app) as c:
    assert c.post(path,json=body).status_code==401
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert c.post(path,headers=bearer,json=body).status_code in (401,403)
    auth={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    response=c.post(path,headers=auth,json=body)
    assert response.status_code==200,response.text
    report=response.json()
    assert report["format"]==REPORT_FORMAT and len(report["cells"])==4
    assert response.headers["cache-control"]=="no-store"
    assert not report["execution_authorized"]
    extended={**body,"service_review":{"targets":[{"label":"Standard","cycle_time_minutes":240,"minimum_completion_share":.9}],
                                      "inverse_completion_shares":[.5,.9]}}
    response=c.post(path,headers=auth,json=extended)
    assert response.status_code==200,response.text
    assert response.json()["service_review"]["single_simulation_per_original_grid_run"]
    assert response.headers["cache-control"]=="no-store"
    assert c.post(path,headers=bearer,json=extended).status_code in (401,403)
    assert c.post(path,headers=auth,json={**body,"replications":1}).status_code==422
    assert c.post(path,headers={**auth,"Content-Type":"application/json"},content=b"{}"+b" "*70000).status_code==413
    assert c.post(path,headers={**auth,"Content-Type":"application/json"},content=b'{"format":"a","format":"b"}').status_code==422
'''
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    done=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],
        env=env,text=True,capture_output=True,timeout=60)
    assert done.returncode==0,done.stdout+done.stderr


def test_paired_bounds_and_cycle_deltas_use_same_rate_and_seed():
    def run(seed, lower, upper, p90, unresolved=0, overloaded=False):
        return {"seed":seed,"post_warmup_arrivals":90,
                "target_share_lower":lower,"target_share_upper":upper,
                "unresolved":unresolved,"abandoned":0,"overloaded":overloaded,
                "cycle_time_seconds_completed_only":{"p90":p90}}
    base = {"design":"Baseline","arrival_rate_per_hour":1,
            "runs":[run(1,.4,.6,20,18),run(2,.5,.5,50)]}
    alternative = {"design":"A","arrival_rate_per_hour":1,
                   "runs":[run(2,.7,.7,40),run(1,.65,.8,15,14)]}
    result = capacity.paired_comparisons([base,alternative])[0]
    assert result["target_share_change_bounds_across_tested_seeds"]["lower"] == pytest.approx(.05)
    assert result["target_share_change_bounds_across_tested_seeds"]["upper"] == pytest.approx(.4)
    assert result["positive_target_change_demonstrated_in_all_runs"]
    assert result["comparable_cycle_replications"] == 1
    assert result["completed_case_p90_change_seconds_across_replications"] is None
    assert result["pairs"][0]["completed_case_p90_change_seconds"] == -10
    assert result["pairs"][1]["cycle_delta_withheld_reasons"] == ["incomplete_completion_population"]
    base["runs"][0] = run(1,.4,.4,20)
    alternative["runs"][1] = run(1,.65,.65,15)
    complete = capacity.paired_comparisons([base,alternative])[0]
    assert complete["completed_case_p90_change_seconds_across_replications"] == {
        "min":-10,"median":-7.5,"max":-5,"replications_with_value":2}
    alternative["runs"][1]["overloaded"] = True
    overloaded = capacity.paired_comparisons([base,alternative])[0]
    assert overloaded["completed_case_p90_change_seconds_across_replications"] is None


def test_paired_comparison_refuses_mismatched_seeds():
    base = {"design":"Baseline","arrival_rate_per_hour":1,"runs":[{"seed":1}]}
    other = {"design":"Other","arrival_rate_per_hour":1,"runs":[{"seed":2}]}
    with pytest.raises(workspace.EventLogWorkspaceError, match="seeds"):
        capacity.paired_comparisons([base,other])


def test_pooled_activity_staffing_override_is_rejected_before_any_simulation(monkeypatch):
    spec=proposal()
    spec["baseline"]["pools"]={"Shared":{"resources":1,"activities":["Receive","Finish"]}}
    monkeypatch.setattr(sim,"simulate",lambda *args:pytest.fail("invalid grid executed"))
    with pytest.raises(workspace.EventLogWorkspaceError,match="shared pool resources"):
        capacity.run_grid(fixture_log(),spec)


def test_shared_pool_staffing_is_effective_and_reported():
    spec=proposal()
    spec["baseline"]["pools"]={"Shared":{"resources":1,"activities":["Receive","Finish"]}}
    spec["designs"]=[{"label":"Two shared reviewers","pools":{"Shared":{"resources":2,
                         "activities":["Receive","Finish"]}}}]
    result=capacity.run_grid(fixture_log(),spec)
    before=result["cells"][0]["effective_resource_basis"]
    after=result["cells"][2]["effective_resource_basis"]
    assert before["dedicated_activities"]==after["dedicated_activities"]=={}
    assert before["shared_pools"]["Shared"]["resources"]==1
    assert after["shared_pools"]["Shared"]["resources"]==2
    assert after["shared_pools"]["Shared"]["activities"]==["Finish","Receive"]


def decision_spec():
    spec=proposal()
    spec["decision_review"]={"currency":"SEK","default_resource_hour_cost":100,
        "maximum_staffed_hour_cost":300,
        "demand_weights":[{"arrival_rate_per_hour":.1,"weight":1},{"arrival_rate_per_hour":1.5,"weight":3}],
        "service_share_thresholds":[.5,.9,1],"dedicated_hour_costs":{"Finish":120}}
    return spec


def test_capacity_decision_real_grid_reproducible_without_extra_runs(monkeypatch):
    log=fixture_log()
    spec=decision_spec()
    original=sim.simulate
    calls=[]
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(sim,"simulate",counted)
    result=capacity.run_grid(log,spec)
    assert len(calls)==12
    review=result["decision_review"]
    designs={d["design"]:d for d in review["designs"]}
    assert designs["Baseline"]["staffed_hour_cost_decimal"]=="220"
    assert designs["Two reviewers"]["staffed_hour_cost_decimal"]=="340"
    assert designs["Baseline"]["within_budget"] and not designs["Two reviewers"]["within_budget"]
    assert designs["Baseline"]["service_status_all_tested_rates"]=="overloaded"
    assert not designs["Baseline"]["budget_and_service_demonstrated"]
    assert not designs["Two reviewers"]["budget_and_service_demonstrated"]
    assert review["normalized_demand_weights"]==[
        {"arrival_rate_per_hour":.1,"weight":.25},{"arrival_rate_per_hour":1.5,"weight":.75}]
    assert capacity.run_grid(log,result["inputs"])==result
    assert len(calls)==24


def test_invalid_cost_override_refused_before_simulation(monkeypatch):
    spec=decision_spec()
    spec["decision_review"]["pool_hour_costs"]={"Absent":200}
    monkeypatch.setattr(sim,"simulate",lambda *args:pytest.fail("invalid decision simulated"))
    with pytest.raises(workspace.EventLogWorkspaceError,match="does not match"):
        capacity.run_grid(fixture_log(),spec)


def test_shared_pool_cost_is_counted_once_per_resource():
    spec=decision_spec()
    spec["baseline"]["pools"]={"Shared":{"resources":1,"activities":["Receive","Finish"]}}
    spec["designs"]=[{"label":"Two shared reviewers","pools":{"Shared":{"resources":2,
        "activities":["Receive","Finish"]}}}]
    spec["decision_review"]["dedicated_hour_costs"]={}
    spec["decision_review"]["pool_hour_costs"]={"Shared":125}
    result=capacity.run_grid(fixture_log(),spec)["decision_review"]
    assert [d["staffed_hour_cost_decimal"] for d in result["designs"]]==["125","250"]
    assert [len(d["cost_basis"]) for d in result["designs"]]==[1,1]
    assert all(d["cost_basis"][0]["kind"]=="shared_pool" for d in result["designs"])


def service_settings():
    return {"targets":[{"label":"Fast","cycle_time_minutes":30,"minimum_completion_share":.5},
                       {"label":"Standard","cycle_time_minutes":240,"minimum_completion_share":.9}],
            "inverse_completion_shares":[.5,.9,1]}


def test_multi_service_grid_reuses_each_simulation_and_calibration(monkeypatch):
    log=fixture_log()
    spec=proposal()
    base=capacity.run_grid(log,spec)
    spec["service_review"]=service_settings()
    cal,simulate=sim.calibrate,sim.simulate
    calls={"calibrate":0,"simulate":0}
    def counted_cal(*args,**kwargs):
        calls["calibrate"]+=1
        return cal(*args,**kwargs)
    def counted_run(*args,**kwargs):
        calls["simulate"]+=1
        return simulate(*args,**kwargs)
    monkeypatch.setattr(sim,"calibrate",counted_cal)
    monkeypatch.setattr(sim,"simulate",counted_run)
    report=capacity.run_grid(log,spec)
    assert calls=={"calibrate":1,"simulate":12}
    assert report["cells"]==base["cells"]
    assert report["simulated_cases"]==base["simulated_cases"]
    assert report["paired_comparisons"]==base["paired_comparisons"]
    review=report["service_review"]
    assert review["single_simulation_per_original_grid_run"]
    assert not review["observed_sla_verified"]
    assert len(review["cells"])==4
    for cell,original in zip(review["cells"],report["cells"]):
        for run,source in zip(cell["runs"],original["runs"]):
            assert run["arrivals"]==source["post_warmup_arrivals"]
            for target in run["targets"]:
                assert target["on_time"]+target["known_misses"]+target["unresolved"]==run["arrivals"]
            assert run["targets"][1]["on_time"]==source["completed_on_time"]
            assert run["targets"][1]["share_lower"]==source["target_share_lower"]
            assert run["targets"][0]["on_time"]<=run["targets"][1]["on_time"]
    assert capacity.run_grid(log,report["inputs"])==report


def test_exact_share_boundary_and_inverse_unknown_abandonment():
    from server import event_log_service_levels as service
    assert service.required_count(.07,100)==7
    assert service.required_count(.58,50)==29
    assert service.required_count(.91,10)==10
    samples=[0,5,10,20,20]
    # Ten arrivals: five finished, two abandoned, three unknown.
    assert service.inverse(samples,10,3,.2)["minimum_possible_seconds"]==0
    assert service.inverse(samples,10,3,.5)["minimum_possible_seconds"]==5
    assert service.inverse(samples,10,3,.5)["minimum_guaranteed_seconds"]==20
    assert service.inverse(samples,10,3,.8)["minimum_possible_seconds"]==20
    assert service.inverse(samples,10,3,.8)["minimum_guaranteed_seconds"] is None
    assert service.inverse(samples,10,3,.9)["minimum_possible_seconds"] is None
    assert service.classify(7,0,7,False)=="met"
    assert service.classify(7,0,7,True)=="overloaded"
    assert service.classify(6,0,7,False)=="known_miss"
    assert service.classify(6,1,7,False)=="indeterminate"


def test_inverse_deadlines_and_target_bounds_against_independent_enumeration():
    from itertools import product
    from fractions import Fraction
    import random
    from server import event_log_service_levels as service
    rng=random.Random(920)
    for _ in range(120):
        n=rng.randint(1,8)
        unknown=rng.randint(0,min(3,n))
        completed=rng.randint(0,n-unknown)
        abandoned=n-unknown-completed
        durations=sorted(rng.randint(0,5)*60 for _ in range(completed))
        share=Fraction(rng.randint(1,10),10)
        need=next(k for k in range(n+1) if Fraction(k,n)>=share)
        settings=service.normalize({"targets":[{"label":"Test","cycle_time_minutes":2,"minimum_completion_share":float(share)}],
                                    "inverse_completion_shares":[float(share)]})
        run={"seed":17,"post_warmup_arrivals":n,"completed":completed,"abandoned":abandoned,
             "unresolved":unknown,"overloaded":False}
        actual=service.summarize(run,durations,settings)
        # Unknown completions can have any duration or never complete.
        possibilities=list(product([0,60,120,180,240,300,None],repeat=unknown))
        hits=[];thresholds=[]
        for values in possibilities:
            all_finished=sorted(durations+[v for v in values if v is not None])
            hits.append(sum(t<=120 for t in all_finished))
            thresholds.append(all_finished[need-1] if len(all_finished)>=need else None)
        target=actual["targets"][0];inverse=actual["inverse_deadlines"][0]
        assert target["on_time"]==min(hits)
        assert target["on_time"]+unknown==max(hits)
        finite=[v for v in thresholds if v is not None]
        assert inverse["minimum_possible_seconds"]==(min(finite) if finite else None)
        assert inverse["minimum_guaranteed_seconds"]==(max(finite) if all(v is not None for v in thresholds) else None)
        assert target["required_completions"]==need
        expected="met" if min(hits)>=need else "known_miss" if max(hits)<need else "indeterminate"
        assert target["status"]==expected


def test_service_joint_envelope_and_pairwise_dominance_with_tradeoffs():
    from server import event_log_service_levels as service
    settings=service.normalize(service_settings())
    def cell(label,rate,samples,unknown=0,overload=False):
        runs=[]
        for seed in (1,2):
            run={"seed":seed,"post_warmup_arrivals":10,"completed":len(samples),
                 "abandoned":10-len(samples)-unknown,"unresolved":unknown,"overloaded":overload}
            runs.append(service.summarize(run,samples,settings))
        return service.cell(label,rate,runs,settings)
    cells=[cell("A",1,[100]*5+[20000]*5),cell("A",2,[100]*10),
           cell("B",1,[100]*10),cell("B",2,[100]*10,overload=True),
           cell("C",1,[100]*5+[20000]*5),cell("C",2,[100]*5,unknown=5)]
    r=service.compare(cells,settings)
    a,b,c=r["joint_operating_envelopes"]
    assert a["jointly_passing_tested_rates"]==[2] and a["nonmonotonic_tested_pattern"]
    assert b["jointly_passing_tested_rates"]==[1]
    assert c["jointly_passing_tested_rates"]==[]
    ab=[p for p in r["pairwise_service_comparisons"] if p["left_design"]=="A" and p["right_design"]=="B"]
    assert ab[0]["direction"]=="right_weakly_dominates_all_targets"
    assert ab[1]["direction"]=="overload_prevents_dominance"
    assert cells[-1]["joint_status"]=="indeterminate"
    assert cells[-1]["inverse_deadlines"][1]["minimum_guaranteed_deadline_across_all_runs_seconds"] is None
    assert cells[-1]["inverse_deadlines"][1]["minimum_possible_deadline_across_all_runs_seconds"]==100


@pytest.mark.parametrize("change",[
    lambda s:s.update(targets=[]),
    lambda s:s.update(targets=s["targets"]*5),
    lambda s:s["targets"][1].update(label="fast"),
    lambda s:s["targets"][0].update(cycle_time_minutes=True),
    lambda s:s["targets"][0].update(minimum_completion_share=float("nan")),
    lambda s:s["targets"][0].update(extra=True),
    lambda s:s.update(inverse_completion_shares=[.5,.5]),
    lambda s:s.update(inverse_completion_shares=[0]),
    lambda s:s.update(inverse_completion_shares=[True]),
    lambda s:s.update(unknown=True),
])
def test_bad_service_settings_fail_before_calibration(change,monkeypatch):
    spec=proposal();spec["service_review"]=service_settings()
    change(spec["service_review"])
    monkeypatch.setattr(sim,"calibrate",lambda *a,**k:pytest.fail("invalid inputs calibrated"))
    with pytest.raises(workspace.EventLogWorkspaceError):
        capacity.run_grid("not-read",spec)
