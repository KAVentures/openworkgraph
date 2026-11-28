"""R16 phased cash-flow scenarios with independent arithmetic and storage oracles."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import random

import pytest
from pydantic import ValidationError

from shared import phased_investment_business_case as core
from server import automation_investment_case as investment
from server import automation_benefits as benefits
from server import automation_pipeline as pipeline


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,"DATA_DIR",tmp_path)


def phase(start=1,end=3,*,cases=100,effect=1,nonlabor=0,fixed=100,one_time=0):
    return {"start_month":start,"end_month":end,"monthly_cases":cases,
            "retained_effort_effect_share":effect,"nonlabor_cash_change_per_case":nonlabor,
            "recurring_fixed_cash_cost":fixed,"one_time_cash_cost_at_phase_start":one_time}


def design(label="Base",conversion=.5,initial=1000,phases=None):
    return {"label":label,"cash_conversion_share":conversion,"initial_cash_cost":initial,
            "phases":phases or [phase()]}


def spec(**changes):
    return {"snapshot_sha256":"a"*64,"measurement_ids":["meas_"+"1"*16,"meas_"+"2"*16],
            "currency":"SEK","labor_hour_value":100,"monthly_discount_rate":0,
            "horizon_months":3,"designs":[design()],"conversion_sensitivity":[0,.25,.5,.75,1],
            **changes}


def solve(delta=Fraction(1,10),**changes):
    return core.evaluate(core.Scenario.model_validate(spec(**changes)),delta)


def approx(value,expected):
    assert float(value)==pytest.approx(float(expected),abs=1e-6)


def test_hand_calculated_cash_capacity_and_exact_threshold():
    result=solve()["designs"][0]
    # 10 hours released/month at 100; only 50% is assumed cashable.
    # Net cash 400/month; net economic 900/month; month zero -1000.
    assert result["cash_npv"]=="200.000000"
    assert result["economic_npv"]=="1700.000000"
    assert result["released_hours"]=="30.000000"
    assert result["unconverted_released_hours"]=="15.000000"
    assert result["peak_undiscounted_cash_funding_requirement"]=="1000.000000"
    assert result["discounted_cash_break_even"]["sustained_nonnegative_from_month"]==3
    s=result["cash_conversion_review"]
    assert s["minimum_cash_conversion_share_for_nonnegative_npv"]=="0.4333333334"
    assert s["cash_npv_at_zero_conversion"]=="-1300.000000"
    assert s["cash_npv_at_full_conversion"]=="1700.000000"
    assert s["minimum_share_display_rounded_up"]
    assert result["months"][1]["net_cash_flow"]=="400.000000"
    assert result["months"][1]["net_economic_value"]=="900.000000"


def test_discount_and_later_reversal():
    phases=[phase(1,1,fixed=0),phase(2,2,cases=0,fixed=0,one_time=1500),
            phase(3,3,cases=200,fixed=0)]
    r=solve(monthly_discount_rate=.1,designs=[design(conversion=1,initial=500,phases=phases)])["designs"][0]
    approx(r["cash_npv"],-500+Fraction(1000,1)/Fraction(11,10)
           -Fraction(1500,1)/Fraction(11,10)**2+Fraction(2000,1)/Fraction(11,10)**3)
    assert r["undiscounted_cash_break_even"]=={"first_nonnegative_month":1,
        "sustained_nonnegative_from_month":3,"returns_below_zero_after_first_crossing":True,
        "within_declared_horizon_only":True}
    assert r["peak_undiscounted_cash_funding_requirement"]=="1000.000000"
    assert r["discounted_cash_break_even"]["returns_below_zero_after_first_crossing"]


def test_deterioration_never_becomes_capacity_or_cash_saving():
    r=solve(Fraction(-1,10),designs=[design(conversion=0,initial=0,phases=[phase(fixed=0)])])["designs"][0]
    assert r["released_hours"]=="0.000000"
    assert r["additional_hours"]=="30.000000"
    assert r["cash_npv"]==r["economic_npv"]=="-3000.000000"
    assert r["cash_conversion_review"]["status"]=="not_attainable_without_positive_release"
    assert r["cash_conversion_review"]["minimum_cash_conversion_share_for_nonnegative_npv"] is None
    assert r["discounted_cash_break_even"]["first_nonnegative_month"]==0
    assert r["discounted_cash_break_even"]["sustained_nonnegative_from_month"] is None


def test_explicit_alternatives_and_impossible_threshold():
    result=solve(designs=[design("Base",0),design("Full",1),design("Costly",1,10000)])
    base,full,costly=result["designs"]
    assert full["change_vs_first_design"]["cash_npv"]=="3000.000000"
    assert full["change_vs_first_design"]["economic_npv"]=="0.000000"
    assert costly["cash_conversion_review"]["status"]=="not_attainable_even_at_full_conversion"
    assert not result["scenario_probabilities_assumed"] and not result["actual_cash_savings_verified"]


def test_zero_cash_conversion_can_still_break_even_on_nonlabor():
    r=solve(Fraction(0),designs=[design(conversion=0,initial=0,phases=[phase(nonlabor=2,fixed=100)])])["designs"][0]
    assert r["cash_npv"]=="300.000000"
    assert r["cash_conversion_review"]["status"]=="nonnegative_without_cash_conversion"
    assert r["cash_conversion_review"]["minimum_cash_conversion_share_for_nonnegative_npv"]=="0.0000000000"


def test_random_monthly_oracle_independently_computes_cash_and_economic_values():
    rng=random.Random(926)
    for _ in range(120):
        n=rng.randint(1,8)
        delta=Fraction(rng.randint(-6,6),10)
        conversion=Fraction(rng.randint(0,10),10)
        hourly=rng.randint(0,200)
        initial=rng.randint(0,2000)
        discount=Fraction(rng.randint(0,10),100)
        phases=[phase(i,i,cases=rng.randint(0,1000),effect=rng.randint(0,10)/10,
                     nonlabor=rng.randint(-5,5),fixed=rng.randint(0,300),one_time=rng.randint(0,200))
                for i in range(1,n+1)]
        r=solve(delta,horizon_months=n,labor_hour_value=hourly,monthly_discount_rate=float(discount),
                designs=[design(conversion=float(conversion),initial=initial,phases=phases)])["designs"][0]
        cash=[Fraction(-initial)];econ=[Fraction(-initial)]
        for p in phases:
            hours=delta*p["monthly_cases"]*Fraction(str(p["retained_effort_effect_share"]))
            other=p["nonlabor_cash_change_per_case"]*p["monthly_cases"]-p["recurring_fixed_cash_cost"]-p["one_time_cash_cost_at_phase_start"]
            cash.append(other+hours*hourly*(conversion if hours>0 else 1))
            econ.append(other+hours*hourly)
        cash_npv=sum(v/(1+discount)**i for i,v in enumerate(cash))
        econ_npv=sum(v/(1+discount)**i for i,v in enumerate(econ))
        approx(r["cash_npv"],cash_npv);approx(r["economic_npv"],econ_npv)
        assert r["cash_npv_nonnegative"]==(cash_npv>=0)
        assert r["economic_npv_nonnegative"]==(econ_npv>=0)
        cumulative=[sum(cash[:i+1]) for i in range(len(cash))]
        approx(r["peak_undiscounted_cash_funding_requirement"],max(0,-min(cumulative)))
        assert r["undiscounted_cash_break_even"]["sustained_nonnegative_from_month"]==next(
            (i for i in range(len(cumulative)) if all(x>=0 for x in cumulative[i:])),None)
        for row,v in zip(r["months"],cash):
            approx(row["net_cash_flow"],v)
        # Exact affine monotonic cash conversion relation at every requested share.
        if delta>0:
            s=r["cash_conversion_review"]
            a=float(s["cash_npv_at_zero_conversion"]);b=float(s["cash_npv_at_full_conversion"])
            for x in s["sensitivity"]:
                assert float(x["cash_npv"])==pytest.approx(a+(b-a)*float(x["cash_conversion_share"]),abs=2e-6)


@pytest.mark.parametrize("kind",["unknown","bool","duplicate_measurement","duplicate_design","bad_label",
    "phase_gap","phase_overlap","phase_reverse","missing_month","duplicate_sensitivity","negative_cost","nan"])
def test_invalid_or_incomplete_assumptions(kind):
    p=spec()
    if kind=="unknown":p["execute"]=True
    elif kind=="bool":p["horizon_months"]=True
    elif kind=="duplicate_measurement":p["measurement_ids"]*=2
    elif kind=="duplicate_design":p["designs"].append(design("base"))
    elif kind=="bad_label":p["designs"][0]["label"]=" "
    elif kind=="phase_gap":p["designs"][0]["phases"]=[phase(1,1),phase(3,3)]
    elif kind=="phase_overlap":p["designs"][0]["phases"]=[phase(1,2),phase(2,3)]
    elif kind=="phase_reverse":p["designs"][0]["phases"]=[phase(1,1),phase(2,1)]
    elif kind=="missing_month":p["designs"][0]["phases"]=[phase(1,2)]
    elif kind=="duplicate_sensitivity":p["conversion_sensitivity"]=[.5,.5]
    elif kind=="negative_cost":p["designs"][0]["initial_cash_cost"]=-1
    else:p["monthly_discount_rate"]=float("nan")
    with pytest.raises((ValueError,ValidationError)):
        core.evaluate(core.Scenario.model_validate(p),Fraction(1,10))


def make():
    idea=pipeline.create({"title":"Reviewed investment"})
    key=idea["idea_id"]
    idea=pipeline.transition(key,{"to":"assessing","expected_revision":idea["revision"]})
    idea=pipeline.assess(key,{"ease":{k:5 for k in pipeline.EASE_QUESTIONS},"cases_per_month":100,
        "minutes_per_case":12,"eligible_share":1,"expected_revision":idea["revision"]})
    idea=pipeline.transition(key,{"to":"approved","expected_revision":idea["revision"]})
    pipeline.transition(key,{"to":"implemented","expected_revision":idea["revision"]})
    today=datetime.now(timezone.utc).date()
    yesterday=today-timedelta(days=1)
    benefits.record(key,{"period":"baseline","start":yesterday.isoformat(),"end":yesterday.isoformat(),
        "cases":100,"total_hours":20,"source_reference":"PRIVATE_REPORT_REFERENCE","note":"PRIVATE_NOTE"})
    benefits.record(key,{"period":"after","start":today.isoformat(),"end":today.isoformat(),
        "cases":100,"total_hours":10,"source":"time_tracking"})
    inv=investment.inventory(key)
    return key,spec(snapshot_sha256=inv["snapshot_sha256"],measurement_ids=[r["measurement_id"] for r in inv["measurements"]])


def test_retained_snapshot_real_storage_and_no_mutation():
    key,p=make()
    before=benefits.report(key)
    r=investment.evaluate(key,p)
    assert r["designs"][0]["cash_npv"]=="200.000000"
    assert r["retained_effort_basis"]["baseline_minus_after_minutes_per_case"]=="6.000000"
    assert not r["retained_effort_basis"]["source_kind_sets_match"]
    assert not r["portfolio_modified"]
    assert benefits.report(key)==before
    assert "PRIVATE_REPORT_REFERENCE" not in json.dumps(r)
    assert "PRIVATE_NOTE" not in json.dumps(investment.inventory(key))


def test_stale_snapshot_and_overlapping_periods():
    key,p=make()
    inv=investment.inventory(key)
    row=next(r for r in inv["measurements"] if r["period"]=="baseline")
    benefits.record(key,{"period":"baseline","start":row["start"],"end":row["end"],"cases":20,"total_hours":3})
    with pytest.raises(benefits.BenefitError,match="changed"):
        investment.evaluate(key,p)
    inv=investment.inventory(key)
    p.update(snapshot_sha256=inv["snapshot_sha256"],measurement_ids=[r["measurement_id"] for r in inv["measurements"]])
    with pytest.raises(benefits.BenefitError,match="overlap"):
        investment.evaluate(key,p)


def test_source_changed_during_evaluation(monkeypatch):
    key,p=make()
    real=investment._snapshot
    first=real(key);second={**first,"snapshot_sha256":"b"*64}
    rows=iter([first,second])
    monkeypatch.setattr(investment,"_snapshot",lambda _:next(rows))
    with pytest.raises(benefits.BenefitError,match="during review"):
        investment.evaluate(key,p)


def test_malformed_measurement_refused():
    key,p=make()
    with closing(benefits._connect()) as db,db:
        db.execute("UPDATE idea_measurements SET body=? WHERE measurement_id=?",["{}",p["measurement_ids"][0]])
    with pytest.raises(benefits.BenefitError,match="inventory is invalid"):
        investment.inventory(key)


def test_authenticated_routes_and_no_store(monkeypatch):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from server import automation_pipeline_routes as routes
    key,p=make()
    def auth(request):
        if request.headers.get("authorization")!="OWG-Session local":
            raise HTTPException(403,"session required")
    monkeypatch.setattr(routes,"_require_dashboard",auth)
    app=FastAPI();app.include_router(routes.router)
    base="/v1/automation-pipeline/"+key+"/investment-case"
    owner={"Authorization":"OWG-Session local"}
    with TestClient(app) as client:
        assert client.get(base+"/inventory").status_code==403
        assert client.post(base,json=p,headers={"Authorization":"Bearer api"}).status_code==403
        inv=client.get(base+"/inventory",headers=owner)
        assert inv.status_code==200 and inv.headers["cache-control"]=="no-store"
        result=client.post(base,json=p,headers=owner)
        assert result.status_code==200,result.text
        assert result.headers["cache-control"]=="no-store"
        assert result.json()["designs"][0]["cash_npv"]=="200.000000"
        p["snapshot_sha256"]="b"*64
        assert client.post(base,json=p,headers=owner).status_code==422
