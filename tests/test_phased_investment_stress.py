"""R16 common scenario arithmetic and exact selection regression tests."""
from fractions import Fraction
import random

import pytest

from shared import phased_investment_business_case as base
from shared import phased_investment_stress as stress
from tests.test_phased_investment_business_case import spec, design, phase, make, store


def shock(label="Downside", **kwargs):
    return {"label": label, "volume_factor": 1, "effect_factor": 1,
            "conversion_factor": 1, "cost_factor": 1, "nonlabor_factor": 1,
            "discount_rate": 0, **kwargs}


def exact(x):
    return Fraction(int(x["numerator"]), int(x["denominator"]))


def test_reference_and_downside_exact_gates():
    s = base.Scenario.model_validate(spec(designs=[
        design("Base", .5, 1000), design("Lower cost", .5, 500)]))
    r = stress.review(s, Fraction(1,10), [shock(volume_factor=.5)], 1000)
    a,b = r["designs"]
    assert exact(a["scenarios"][0]["cash_npv"]) == 200
    assert exact(a["worst_cash_npv"]) == -550
    assert exact(b["worst_cash_npv"]) == -50
    assert not r["passing_designs"]
    assert r["robust_dominance"][0]["dominant"] == "Lower cost"
    assert exact(a["maximum_cash_regret"]) == 500
    assert not r["execution_authorized"]
    r2 = stress.review(s, Fraction(1,10), [shock()], 500)
    assert r2["passing_designs"] == ["Lower cost"]
    assert r2["maximin_cash_npv_among_passing_designs"] == ["Lower cost"]


def test_negative_effort_is_full_cost_even_with_zero_conversion():
    s = base.Scenario.model_validate(spec(designs=[design(initial=0, conversion=0,
        phases=[phase(fixed=0)])]))
    r = stress.review(s, Fraction(-1,10), [shock(conversion_factor=0)], None)
    assert exact(r["designs"][0]["worst_cash_npv"]) == -3000
    assert exact(r["designs"][0]["maximum_peak_funding"]) == 3000


@pytest.mark.parametrize("bad", [
    [], [shock("Reference")], [shock("a"), shock("A")],
    [shock(volume_factor=True)], [shock(effect_factor=1.1)],
    [shock(discount_rate=1e-12)], [shock(cost_factor=float("inf"))],
    [shock(extra=1)], [shock(nonlabor_factor=-1)],
])
def test_bad_scenarios_refused(bad):
    with pytest.raises(ValueError):
        stress.review(base.Scenario.model_validate(spec()), Fraction(1,10), bad, None)


def test_random_exact_independent_cash_oracle():
    rng = random.Random(183)
    for _ in range(250):
        n = rng.randint(1, 6)
        delta = Fraction(rng.randint(-6, 6), 10)
        designs = [design(str(k), rng.randint(0,10)/10, rng.randint(0,1000), [
            phase(i,i,cases=rng.randint(0,100),effect=rng.randint(0,10)/10,
                  nonlabor=rng.randint(-5,5),fixed=rng.randint(0,100),one_time=rng.randint(0,100))
            for i in range(1,n+1)]) for k in range(3)]
        s = base.Scenario.model_validate(spec(horizon_months=n, designs=designs,
            monthly_discount_rate=.01, labor_hour_value=100))
        shocks = [shock("A", volume_factor=rng.randint(0,20)/10,
            effect_factor=rng.randint(0,10)/10, conversion_factor=rng.randint(0,10)/10,
            cost_factor=rng.randint(0,20)/10, nonlabor_factor=rng.randint(0,20)/10,
            discount_rate=rng.randint(0,10)/100)]
        r = stress.review(s,delta,shocks,500)
        oracle = []
        for raw in designs:
            cells = []
            for sh in [shock("Reference",discount_rate=.01), *shocks]:
                q = lambda v: Fraction(str(v))
                flows = [-q(raw["initial_cash_cost"])*q(sh["cost_factor"])]
                econ = flows.copy()
                for p in raw["phases"]:
                    volume=q(p["monthly_cases"])*q(sh["volume_factor"])
                    hours=delta*volume*q(p["retained_effort_effect_share"])*q(sh["effect_factor"])
                    common=volume*q(p["nonlabor_cash_change_per_case"])*q(sh["nonlabor_factor"])-q(sh["cost_factor"])*(p["recurring_fixed_cash_cost"]+p["one_time_cash_cost_at_phase_start"])
                    full=hours*100
                    flows.append(common+full*(q(raw["cash_conversion_share"])*q(sh["conversion_factor"]) if full>=0 else 1))
                    econ.append(common+full)
                disc=1+q(sh["discount_rate"])
                cells.append((sum(x/disc**i for i,x in enumerate(flows)),
                              sum(x/disc**i for i,x in enumerate(econ)),
                              max(0,*(-sum(flows[:i+1]) for i in range(len(flows))))))
            oracle.append(cells)
        best=[max(c[j][0] for c in oracle) for j in range(2)]
        for idx,d in enumerate(r["designs"]):
            for j,c in enumerate(d["scenarios"]):
                assert tuple(exact(c[k]) for k in ("cash_npv","economic_npv","peak_funding"))==oracle[idx][j]
            worst=min(x[0] for x in oracle[idx])
            peak=max(x[2] for x in oracle[idx])
            regret=max(best[j]-oracle[idx][j][0] for j in range(2))
            assert exact(d["worst_cash_npv"])==worst
            assert exact(d["maximum_cash_regret"])==regret
            assert d["passes_declared_gates"]==(worst>=0 and peak<=500)


def test_retained_snapshot_and_authenticated_route(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from server import automation_pipeline_routes as routes
    from server import automation_investment_stress as service
    from server import automation_investment_case as investment
    from server import automation_benefits as benefits
    import json
    key, p = make()
    before = benefits.report(key)
    body = {"investment": p, "scenarios": [shock(volume_factor=.5)],
            "funding_limit": 1500, "confirm": True}
    result = service.evaluate(key, body)
    assert exact(result["exact_retained_effort_delta_hours_per_case"]) == Fraction(1,10)
    assert result["designs"][0]["scenarios"][0]["cash_npv"]["decimal"] == "200.000000"
    assert result["snapshot_sha256"] == p["snapshot_sha256"]
    assert not result["portfolio_modified"]
    assert "PRIVATE_REPORT_REFERENCE" not in json.dumps(result)
    assert "PRIVATE_NOTE" not in json.dumps(result)
    assert benefits.report(key) == before

    def auth(request):
        if request.headers.get("authorization") != "OWG-Session local":
            raise HTTPException(403,"session required")
    monkeypatch.setattr(routes, "_require_dashboard", auth)
    app = FastAPI()
    app.include_router(routes.router)
    endpoint = "/v1/automation-pipeline/" + key + "/investment-case/stress"
    owner = {"Authorization": "OWG-Session local"}
    with TestClient(app) as client:
        assert client.get("/investment-stress.js").status_code == 200
        assert client.post(endpoint, json=body).status_code == 403
        assert client.post(endpoint, json=body, headers={"Authorization":"Bearer api"}).status_code == 403
        response = client.post(endpoint, json=body, headers=owner)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == result
        assert client.post(endpoint,json={**body,"confirm":False},headers=owner).status_code == 422
        stale = {**body,"investment":{**p,"snapshot_sha256":"b"*64}}
        assert client.post(endpoint,json=stale,headers=owner).status_code == 422
    first = investment._snapshot(key)
    second = {**first, "snapshot_sha256":"b"*64}
    rows = iter([first,second])
    monkeypatch.setattr(investment,"_snapshot",lambda _:next(rows))
    with pytest.raises(benefits.BenefitError,match="during review"):
        service.evaluate(key,body)
