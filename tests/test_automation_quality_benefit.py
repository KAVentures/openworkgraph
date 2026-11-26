"""Pinned effort, adoption and quality review with explicit denominators."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_benefits as b
from server import automation_pipeline as p
from server import automation_quality_benefit as q


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(p,"DATA_DIR",tmp_path)


def make():
    idea=p.create({"title":"Quality review"})
    idea=p.transition(idea["idea_id"],{"to":"assessing","expected_revision":idea["revision"]})
    idea=p.assess(idea["idea_id"],{"ease":{k:5 for k in p.EASE_QUESTIONS},"cases_per_month":100,
        "minutes_per_case":20,"eligible_share":1,"expected_revision":idea["revision"]})
    idea=p.transition(idea["idea_id"],{"to":"approved","expected_revision":idea["revision"]})
    idea=p.transition(idea["idea_id"],{"to":"implemented","expected_revision":idea["revision"]})
    key=idea["idea_id"]
    today=datetime.now(timezone.utc).date()
    yesterday=today-timedelta(days=1)
    b.record(key,{"period":"baseline","start":yesterday.isoformat(),"end":yesterday.isoformat(),
                  "cases":100,"total_hours":20,"source_reference":"private source"})
    b.record(key,{"period":"after","start":today.isoformat(),"end":today.isoformat(),
                  "cases":100,"total_hours":10})
    inv=q.inventory(key)
    periods=[]
    for row in inv["measurements"]:
        after=row["period"]=="after"
        periods.append({"measurement_id":row["measurement_id"],"eligible_cases":80,
            "automation_attempted_cases":60 if after else 0,"automation_handoff_cases":10 if after else 0,
            "quality_checked_cases":50,"quality_failed_cases":5 if after else 2,
            "extra_review_hours":3 if after else 0,
            "review_effort_basis":"additional_to_retained_effort" if after else "already_in_retained_effort",
            "nonlabor_cost":50 if after else 20})
    return key,{"snapshot_sha256":inv["snapshot_sha256"],"currency":"SEK",
                "labor_cost_per_hour":100,"one_time_cost":1000,"minimum_quality_coverage":0.5,
                "minimum_after_adoption":0.7,"maximum_quality_failure_rate_increase":0.03,
                "periods":periods}


def test_effort_cost_and_quality_are_separate_with_hand_calculated_oracle():
    key,spec=make()
    result=q.analyze(key,spec)
    f=result["financial_scenario"]
    assert f["after_monthly_case_volume"]==3044
    assert f["retained_effort_change_hours_at_after_volume"]==304.4
    assert f["effort_change_with_extra_review_hours_at_after_volume"]==213.08
    assert f["nonlabor_cost_change_at_after_volume"]==-913.2
    assert f["modeled_net_monthly_value_change"]==20394.8
    assert f["modeled_simple_payback_months"]==pytest.approx(1000/20394.8)
    assert result["after"]["attempted_share_of_eligible"]==0.75
    assert result["after"]["handoff_share_of_attempts"]==pytest.approx(1/6)
    assert result["after"]["non_handoff_share_of_eligible"]==0.625
    assert result["baseline"]["quality_failure_rate"]==0.04
    assert result["after"]["quality_failure_rate"]==0.1
    assert result["review_gates"][-1]["status"]=="above_threshold"
    population=result["whole_population_quality"]
    assert population["failure_rate_change_lower"]==-.47
    assert population["failure_rate_change_upper"]==.53
    assert population["threshold_status"]=="unresolved"
    assert population["after"]["nonhandoff_and_quality_pass_count_lower"]==0
    assert population["after"]["nonhandoff_and_quality_pass_count_upper"]==50
    assert not result["all_declared_gates_met"]
    assert not result["financial_return_verified"] and not result["savings_claimed"]
    assert "private source" not in str(q.inventory(key))
    assert "private source" not in str(result)
    assert q.inventory(key)["snapshot_sha256"]==spec["snapshot_sha256"]


def test_no_checked_cases_and_no_eligible_cases_are_unknown_not_zero():
    key,spec=make()
    for row in spec["periods"]:
        row.update(quality_checked_cases=0,quality_failed_cases=0,eligible_cases=0,
                   automation_attempted_cases=0,automation_handoff_cases=0)
    spec["minimum_quality_coverage"]=0
    spec["minimum_after_adoption"]=0
    result=q.analyze(key,spec)
    assert result["after"]["quality_failure_rate"] is None
    assert result["after"]["attempted_share_of_eligible"] is None
    assert [g["status"] for g in result["review_gates"]]==["insufficient","insufficient","unknown","unknown"]
    pop=result["whole_population_quality"]
    assert pop["failure_rate_change_lower"]==-1 and pop["failure_rate_change_upper"]==1
    assert pop["after"]["nonhandoff_and_quality_pass_share_of_eligible_upper"] is None


@pytest.mark.parametrize("patch",[
    {"eligible_cases":101},{"automation_attempted_cases":81},
    {"automation_handoff_cases":81},{"quality_checked_cases":101},
    {"quality_failed_cases":51},{"extra_review_hours":5,"review_effort_basis":"already_in_retained_effort"},
    {"eligible_cases":True},{"nonlabor_cost":-1},
])
def test_invalid_denominators_and_double_count_refused(patch):
    key,spec=make()
    spec["periods"][0].update(patch)
    with pytest.raises(b.BenefitError):
        q.analyze(key,spec)


def test_removed_or_added_period_invalidates_snapshot_and_duplicate_ids_refused():
    key,spec=make()
    bad=deepcopy(spec)
    bad["periods"][1]=bad["periods"][0]
    with pytest.raises(b.BenefitError,match="distinct"):
        q.analyze(key,bad)
    b.delete(key,spec["periods"][0]["measurement_id"])
    with pytest.raises(b.BenefitError,match="changed"):
        q.analyze(key,spec)


def test_calendar_overlap_and_zero_cases_refused():
    key,spec=make()
    original=q.inventory(key)
    base=next(row for row in original["measurements"] if row["period"]=="baseline")
    b.record(key,{"period":"baseline","start":base["start"],"end":base["end"],"cases":100,"total_hours":20})
    inv=q.inventory(key)
    extra=next(r for r in inv["measurements"] if r["measurement_id"] not in {x["measurement_id"] for x in original["measurements"]})
    spec["snapshot_sha256"]=inv["snapshot_sha256"]
    matching=next(row for row in spec["periods"] if row["measurement_id"]==base["measurement_id"])
    spec["periods"].append({**matching,"measurement_id":extra["measurement_id"]})
    with pytest.raises(b.BenefitError,match="overlap"):
        q.analyze(key,spec)


def test_http_auth_and_strict_request(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_quality_benefit import make
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    key,spec=make()
    url="/v1/automation-pipeline/"+key+"/quality-benefit"
    assert client.get(url+"/inventory",headers=bearer).status_code in (401,403)
    assert client.post(url,json=spec,headers=bearer).status_code in (401,403)
    inv=client.get(url+"/inventory",headers=h)
    assert inv.status_code==200 and inv.headers["cache-control"]=="no-store"
    result=client.post(url,json=spec,headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    assert result.json()["financial_scenario"]["modeled_net_monthly_value_change"]==20394.8
    assert result.json()["whole_population_quality"]["threshold_status"]=="unresolved"
    assert client.post(url,content=b'{"currency":"SEK","currency":"EUR"}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
