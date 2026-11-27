"""Service objective bounds and exact retained population pagination."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_monitoring as monitoring
from server import automation_service_objectives as slo

START=datetime(2024,3,1,tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(monitoring,"DATA_DIR",tmp_path)


def stamp(minutes):
    return (START+timedelta(minutes=minutes)).isoformat()


def run(ident,start,status,finish=None,verified=None):
    return {"run_id":ident,"started_at":stamp(start),"finished_at":stamp(finish) if finish is not None else None,
            "status":status,"steps":[],"outcome":{"verified":verified}}


def make():
    runs=[run("met",0,"succeeded",5,True),
          run("late",0,"succeeded",11,True),
          run("failed",0,"failed",3),
          run("outcome_missing",0,"succeeded",5),
          run("outcome_false",0,"succeeded",5,False),
          run("overdue",0,"running"),
          run("pending",14,"running"),
          run("future_finish",14,"succeeded",30,True)]
    doc={"format":monitoring.FORMAT,"deployment":{"name":"Test","runtime":"other"},"runs":runs}
    return monitoring.import_report(doc)["deployment"]["deployment_id"]


def settings(**overrides):
    return {"start":stamp(0),"end":stamp(15),"as_of":stamp(15),"deadline_seconds":600,
            "target_basis_points":5000,"require_reported_verified_outcome":True,**overrides}


def test_objective_counts_and_integer_error_budget():
    result=slo.analyze(make(),settings())
    summary=result["summary"]
    assert summary["counts"]=={"met":1,"missed":4,"pending":2,"unknown":1}
    assert summary["required_met_runs"]==4
    assert summary["remaining_error_budget_after_known_misses"]==0
    assert summary["remaining_error_budget_if_unresolved_miss"]==-3
    assert summary["lower_met_fraction"]==1/8
    assert summary["upper_met_fraction"]==4/8
    assert summary["verdict"]=="unresolved"
    assert result["reason_counts"]["running_past_deadline"]==1
    assert result["reason_counts"]["finish_after_cutoff"]==1
    assert not result["historical_reporting_state_reconstructed"]
    assert not result["independent_business_outcomes_verified"]


def test_optional_outcome_and_exact_spec_unknown():
    dep=make()
    result=slo.analyze(dep,settings(require_reported_verified_outcome=False))
    assert result["summary"]["counts"]=={"met":3,"missed":3,"pending":2,"unknown":0}
    result=slo.analyze(dep,settings(exact_spec_sha256="a"*64))
    assert result["summary"]["counts"]=={"met":0,"missed":0,"pending":0,"unknown":8}


def test_error_budget_boundaries_and_empty_denominator():
    assert slo._summary({"met":9,"missed":1,"pending":0,"unknown":0},9000)["verdict"]=="target_met_even_if_unresolved_fail"
    assert slo._summary({"met":9,"missed":1,"pending":0,"unknown":0},9001)["verdict"]=="target_missed_even_if_unresolved_succeed"
    assert slo._summary(dict.fromkeys(slo.STATES,0),9500)["lower_met_fraction"] is None
    assert slo._summary(dict.fromkeys(slo.STATES,0),9500)["verdict"]=="no_runs"


def test_exact_deadline_and_missing_timestamp_classification():
    began=START.replace(tzinfo=None)
    cutoff=began+timedelta(minutes=10)
    r=("id",began,cutoff,"succeeded",True,"","a"*64)
    assert slo._classify(r,cutoff,600,True,None)[0]=="met"
    assert slo._classify(("id",began,None,"running",None,"",None),cutoff,600,False,None)[0]=="pending"
    assert slo._classify(("id",began,None,"failed",None,"",None),cutoff,600,False,None)[0]=="unknown"


def test_filtered_page_pin_and_replacement_invalidates_digest():
    dep=make()
    first=slo.analyze(dep,settings(status="missed",limit=1))
    assert first["matching_runs"]==4 and first["next_offset"]==1
    second=slo.analyze(dep,settings(status="missed",limit=1,offset=1,
        expected_population_sha256=first["population_sha256"]))
    assert first["rows"][0]["run_id"]!=second["rows"][0]["run_id"]
    with pytest.raises(monitoring.MonitoringError,match="page one"):
        slo.analyze(dep,settings(offset=1))
    monitoring.import_report({"format":monitoring.FORMAT,"deployment":{"name":"Test","runtime":"other"},
        "runs":[run("overdue",0,"succeeded",5,True)]},dep)
    with pytest.raises(monitoring.MonitoringError,match="changed"):
        slo.analyze(dep,settings(expected_population_sha256=first["population_sha256"]))


@pytest.mark.parametrize("bad",[{"target_basis_points":True},{"target_basis_points":10001},{"deadline_seconds":0},
    {"start":stamp(15)},{"as_of":stamp(14)},{"extra":1}])
def test_invalid_settings(bad):
    with pytest.raises(monitoring.MonitoringError):
        slo.analyze(make(),settings(**bad))


def test_daily_bucket_bounds_and_work_limit(monkeypatch):
    dep=make()
    result=slo.analyze(dep,settings(end=stamp(1441),as_of=stamp(1441)))
    assert len(result["daily_start_cohorts"])==2
    assert result["daily_start_cohorts"][1]["runs"]==0
    assert result["daily_start_cohorts"][1]["end"]=="2024-03-02T00:01:00Z"
    monkeypatch.setattr(slo,"MAX_RUNS",1)
    with pytest.raises(monitoring.MonitoringError,match="bound"):
        slo.analyze(dep,settings())


def test_dashboard_auth_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_service_objectives import make,settings
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    dep=make();url="/v1/automation-monitoring/deployments/"+dep+"/service-objectives"
    assert c.post(url,json=settings(),headers=bearer).status_code in (401,403)
    r=c.post(url,json=settings(),headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["summary"]["counts"]["missed"]==4
    assert c.post(url,content=b'{"limit":1,"limit":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
