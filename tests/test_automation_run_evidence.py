"""Exact report evidence, population snapshots and stable repeated-step comparison."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_monitoring as m
from server import automation_run_evidence as evidence


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)


def run(rid,status="failed",steps=None,day=1):
    at=f"2026-01-{day:02d}T00:00:00Z"
    return {"run_id":rid,"started_at":at,"finished_at":None if status=="running" else at,
            "status":status,"steps":[{"step_id":step,"activity":label,"status":state,"at":at}
                for step,label,state in (steps if steps is not None else
                [("receive","Receive","completed"),("check","Check","failed")])],
            "outcome":{"verified":True,"source":"declared source"} if status=="succeeded" else {}}


def report(runs, digest=None):
    deployment={"name":"Evidence test","runtime":"other"}
    if digest is not None:
        deployment["workflow_spec_sha256"]=digest
    return {"format":m.FORMAT,"deployment":deployment,"runs":runs}


def imported(runs,digest=None):
    return m.import_report(report(runs,digest))["deployment"]["deployment_id"]


def test_exact_filter_requires_same_step_and_counts_runs_not_attempts():
    d=imported([run("one"),run("two",steps=[("check","Check","completed"),("other","Other","failed")]),
                run("three",steps=[("check","Check","failed"),("check","Check","failed")])])
    result=evidence.search(d,{"step_id":"check","step_status":"failed"})
    assert result["matching_runs"]==2 and result["matching_steps"]==4
    assert {r["run_id"] for r in result["rows"]}=={"one","three"}
    assert result["status_counts"]["failed"]==2
    assert all(r["first_reported_issue"]["step_id"]=="check" for r in result["rows"])
    assert not result["execution_authorized"] and not result["business_outcomes_verified"]


def test_pages_pin_full_filtered_population_and_exact_step_payload():
    d=imported([run("a"),run("b"),run("c")])
    first=evidence.search(d,{"limit":1})
    assert first["matching_runs"]==3 and first["next_offset"]==1
    second=evidence.search(d,{"limit":1,"offset":1,"expected_population_sha256":first["population_sha256"]})
    assert first["rows"][0]["run_id"]=="a" and second["rows"][0]["run_id"]=="b"
    pin=first["rows"][0]["reference"]
    assert evidence.detail(d,"a",pin["run_sha256"])["reference"]==pin
    changed=run("a")
    changed["steps"][0]["activity"]="Changed label only"
    m.import_report(report([changed]),d)
    with pytest.raises(m.MonitoringError,match="population|evidence changed"):
        evidence.search(d,{"limit":1,"offset":1,"expected_population_sha256":first["population_sha256"]})
    with pytest.raises(m.MonitoringError,match="report changed"):
        evidence.detail(d,"a",pin["run_sha256"])


def test_filter_changes_refuse_old_population_but_unrelated_runs_do_not():
    d=imported([run("failed"),run("ok","succeeded",[])])
    first=evidence.search(d,{"status":"failed"})
    m.import_report(report([run("other","succeeded",[])]),d)
    assert evidence.search(d,{"status":"failed"})["population_sha256"]==first["population_sha256"]
    with pytest.raises(m.MonitoringError,match="evidence changed"):
        evidence.search(d,{"status":"succeeded","expected_population_sha256":first["population_sha256"]})


def test_time_bounds_and_unknown_attribution():
    d=imported([run("one",day=1),run("two",day=2)],"a"*64)
    m.import_report(report([run("three",day=3)]),d)
    assert evidence.search(d,{"started_from":"2026-01-01T00:00:00Z",
                               "started_before":"2026-01-02T00:00:00Z"})["matching_runs"]==1
    assert evidence.search(d,{"attribution":"unknown"})["rows"][0]["run_id"]=="three"
    with pytest.raises(m.MonitoringError,match="attach a design"):
        evidence.search(d,{"attribution":"attached_design"})


def test_repeated_step_alignment_pins_both_and_preserves_status_changes():
    d=imported([run("left",steps=[("a","A","completed"),("b","B","failed"),("a","A","completed")]),
                run("right","succeeded",steps=[("a","A","completed"),("b","Renamed B","completed"),
                                              ("x","Extra","completed"),("a","A","completed")])],"a"*64)
    left=evidence.detail(d,"left")["reference"]
    right=evidence.detail(d,"right")["reference"]
    result=evidence.compare({"left":left,"right":right})
    assert result["same_reported_spec"] is True
    assert result["minimum_step_id_edits"]==1
    assert result["counts"]=={"matched_step_id":3,"left_only":0,"right_only":1}
    assert result["matched_status_changes"]==result["matched_activity_label_changes"]==1
    assert [r["left"]["seq"] for r in result["alignment"] if r["left"] is not None]==[0,1,2]
    assert [r["right"]["seq"] for r in result["alignment"] if r["right"] is not None]==[0,1,2,3]
    assert not result["business_case_identity_verified"]
    m.import_report(report([run("left")],"a"*64),d)
    with pytest.raises(m.MonitoringError,match="changed"):
        evidence.compare({"left":left,"right":right})


def test_cross_deployment_unknown_spec_and_exact_identity():
    a=imported([run("same")])
    b=imported([run("same")],"b"*64)
    l=evidence.detail(a,"same")["reference"]
    r=evidence.detail(b,"same")["reference"]
    assert evidence.compare({"left":l,"right":r})["same_reported_spec"] is None
    with pytest.raises(m.MonitoringError,match="distinct"):
        evidence.compare({"left":l,"right":l})
    m.delete_deployment(a)
    with pytest.raises(m.MonitoringError):
        evidence.compare({"left":l,"right":r})


@pytest.mark.parametrize("bad",[
    {"limit":True},{"limit":101},{"offset":1},{"status":"unknown"},{"step_id":""},
    {"started_from":"2026-01-02T00:00:00Z","started_before":"2026-01-01T00:00:00Z"},
    {"unexpected":True},
])
def test_strict_bounded_settings(bad):
    d=imported([run("one")])
    with pytest.raises(m.MonitoringError):
        evidence.search(d,bad)


def test_resource_bounds_refuse_instead_of_sampling(monkeypatch):
    d=imported([run("one"),run("two")])
    monkeypatch.setattr(evidence,"MAX_MATCHING_RUNS",1)
    with pytest.raises(m.MonitoringError,match="too many matching runs"):
        evidence.search(d,{})
    monkeypatch.setattr(evidence,"MAX_MATCHING_RUNS",10)
    monkeypatch.setattr(evidence,"MAX_MATCHING_STEPS",1)
    with pytest.raises(m.MonitoringError,match="too many matching steps"):
        evidence.search(d,{})


def test_http_dashboard_auth_strict_json_and_no_store(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_run_evidence import report,run
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    stored={**h,"X-OWG-Local-Storage-Confirmed":"true"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    made=client.post("/v1/automation-monitoring/reports",json=report([run("one"),run("two")]),headers=stored)
    assert made.status_code==200,made.text
    dep=made.json()["deployment"]["deployment_id"]
    base="/v1/automation-monitoring/deployments/"+dep+"/runs"
    assert client.post(base+"/search",json={},headers=bearer).status_code in (401,403)
    page=client.post(base+"/search",json={},headers=h)
    assert page.status_code==200,page.text
    assert page.headers["cache-control"]=="no-store"
    left,right=[x["reference"] for x in page.json()["rows"]]
    detail=client.get(base+"/detail",params={"run_id":left["run_id"],"expected_sha256":left["run_sha256"]},headers=h)
    assert detail.status_code==200,detail.text
    assert detail.json()["reference"]==left
    assert client.get(base+"/detail",params={"run_id":"one"},headers=bearer).status_code in (401,403)
    url="/v1/automation-monitoring/runs/compare"
    assert client.post(url,json={"left":left,"right":right},headers=bearer).status_code in (401,403)
    compare=client.post(url,json={"left":left,"right":right},headers=h)
    assert compare.status_code==200,compare.text
    assert compare.json()["minimum_step_id_edits"]==0
    assert client.post(base+"/search",content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
    step_url=base.removesuffix("/runs")+"/step-reliability"
    settings={"reported_spec_sha256":None,"start_date":"2026-01-01","end_date":"2026-01-02"}
    assert client.post(step_url,json=settings,headers=bearer).status_code in (401,403)
    initial=client.post(step_url,json=settings,headers=h)
    assert initial.status_code==200,initial.text
    assert initial.headers["cache-control"]=="no-store"
    assert initial.json()["matching_runs"]==2
    step=client.post(step_url,json={**settings,"step_id":"check","run_filter":"failed",
        "expected_population_sha256":initial.json()["population_sha256"]},headers=h)
    assert step.status_code==200,step.text
    assert step.json()["page"]["matching"]==2
    assert client.post(step_url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert client.post(step_url,json={**settings,"step_id":"check","run_filter":"failed"},headers=h).status_code==422
    recovery_url=base.removesuffix("/runs")+"/recovery-patterns"
    assert client.post(recovery_url,json=settings,headers=bearer).status_code in (401,403)
    result=client.post(recovery_url,json=settings,headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    value=result.json()
    assert value["summary"]["runs"]==2
    assert value["summary"]["episode_summary"]["episodes"]==2
    pattern=value["page"]["rows"][0]["pattern_sha256"]
    focused=client.post(recovery_url,json={**settings,"view":"runs","pattern_sha256":pattern,
        "expected_population_sha256":value["population_sha256"]},headers=h)
    assert focused.status_code==200,focused.text
    assert focused.json()["page"]["matching"]==2
    assert client.post(recovery_url,json={**settings,"expected_population_sha256":"0"*64},headers=h).status_code==422
    assert client.post(recovery_url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert client.post(recovery_url,json={**settings,"view":"runs","pattern_sha256":pattern},headers=h).status_code==422

"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    p=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
                     cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr
