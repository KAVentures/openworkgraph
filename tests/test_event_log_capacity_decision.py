"""Independent finite-grid cost, demand-mix and robust service review oracles."""
from copy import deepcopy
from fractions import Fraction
import pytest

from server import event_log_capacity_decision as decision
from server.event_log_workspace import EventLogWorkspaceError


def spec():
    return {"currency":"SEK","default_resource_hour_cost":100,
            "maximum_staffed_hour_cost":250,
            "demand_weights":[{"arrival_rate_per_hour":1,"weight":1},
                              {"arrival_rate_per_hour":2,"weight":3}],
            "service_share_thresholds":[.5,.8,.9],
            "dedicated_hour_costs":{},"pool_hour_costs":{}}


def normalized():
    return decision.normalize(spec(),[1.,2.],.8)


def cost(total):
    return {"staffed_resources":1,"staffed_hour_cost_decimal":str(total),
            "cost_basis":[],"within_budget":total<=250}


def run(seed,on,unknown=0,over=False):
    return {"seed":seed,"post_warmup_arrivals":10,"completed_on_time":on,
            "unresolved":unknown,"overloaded":over}


def grid(label,first,second):
    return [{"design":label,"arrival_rate_per_hour":rate,"runs":runs}
            for rate,runs in [(1,first),(2,second)]]


def test_demand_mix_exact_weights_and_worst_seed_not_pooled():
    cells=grid("A",[run(1,10),run(2,5,2)],[run(1,6,2),run(2,10)])
    r=decision.review(cells,{"A":cost(200)},normalized(),.8)
    d=r["designs"][0]
    assert d["worst_rate_and_seed_target_bounds"]=={"lower":.5,"upper":.7}
    assert d["demand_mix_by_seed"]==[
        {"seed":1,"target_bounds":{"lower":.7,"upper":.85}},
        {"seed":2,"target_bounds":{"lower":.875,"upper":.925}}]
    assert d["worst_seed_demand_mix_target_bounds"]=={"lower":.7,"upper":.85}
    assert d["service_status_all_tested_rates"]=="known_target_miss"
    assert not d["budget_and_service_demonstrated"]
    assert r["service_threshold_sensitivity"][0]["demonstrated_designs"]==["A"]
    assert not r["staffing_recommendation_made"]


def test_robust_dominance_requires_every_rate_and_seed_and_strict_improvement():
    a=grid("A",[run(1,9),run(2,9)],[run(1,9),run(2,9)])
    b=grid("B",[run(1,8,1),run(2,8)],[run(1,7),run(2,9)])
    c=grid("C",[run(1,10),run(2,9)],[run(1,6),run(2,9)])
    d=grid("D",[run(1,10,0,True),run(2,10)],[run(1,10),run(2,10)])
    r=decision.review(a+b+c+d,{"A":cost(200),"B":cost(220),"C":cost(190),"D":cost(100)},normalized(),.8)
    assert [(w["dominant_design"],w["dominated_design"]) for w in r["robust_dominance_witnesses"]]==[("A","B")]
    assert r["not_robustly_dominated_tested_designs"]==["A","C","D"]
    assert r["robust_dominance_witnesses"][0]["minimum_service_advantage_lower_bound"]==0
    assert r["robust_dominance_witnesses"][0]["matched_rate_seed_cells"]==4


def test_service_threshold_equality_budget_and_minimum_cost_ties():
    a=grid("A",[run(1,8),run(2,8)],[run(1,8),run(2,8)])
    b=grid("B",[run(1,9),run(2,9)],[run(1,9),run(2,9)])
    c=grid("C",[run(1,10),run(2,10)],[run(1,10),run(2,10)])
    r=decision.review(a+b+c,{"A":cost(200),"B":cost(200),"C":cost(300)},normalized(),.8)
    sensitivity=r["service_threshold_sensitivity"]
    assert sensitivity[1]["equal_minimum_cost_designs"]==["A","B"]
    assert sensitivity[1]["minimum_tested_qualifying_staffed_hour_cost_decimal"]=="200"
    assert sensitivity[2]["demonstrated_designs"]==["B"]
    assert not r["designs"][2]["budget_and_service_demonstrated"]
    assert all(not r[k] for k in ("production_capacity_certified","financial_savings_verified","execution_authorized"))


@pytest.mark.parametrize("patch",[
    {"currency":"sek"},{"maximum_staffed_hour_cost":True},{"default_resource_hour_cost":float("nan")},
    {"demand_weights":[{"arrival_rate_per_hour":1,"weight":1}]},
    {"demand_weights":[{"arrival_rate_per_hour":1,"weight":1},{"arrival_rate_per_hour":1,"weight":2}]},
    {"demand_weights":[{"arrival_rate_per_hour":1,"weight":0},{"arrival_rate_per_hour":2,"weight":2}]},
    {"service_share_thresholds":[.5,.9]},{"service_share_thresholds":[.8,.8]},
    {"dedicated_hour_costs":{"A":-1}},{"unexpected":True}
])
def test_strict_cost_review_inputs(patch):
    with pytest.raises(EventLogWorkspaceError):
        decision.normalize({**spec(),**patch},[1.,2.],.8)


def test_missing_duplicate_or_mismatched_cells_fail_closed():
    a=grid("A",[run(1,8)],[run(1,8)])
    for broken in ([a[0]],a+[a[0]],grid("A",[run(1,8)],[run(2,8)]),
                   grid("A",[run(1,8),run(1,8)],[run(1,8)])):
        with pytest.raises(EventLogWorkspaceError):
            decision.review(broken,{"A":cost(200)},normalized(),.8)


def test_cost_exact_decimal_boundary_and_no_pool_double_count():
    plan=[("A",1,{"activities":{"X":{"resources":3},"Y":{"resources":2}},
                   "pools":{"Team":{"activities":["X"],"resources":2}}})]
    s=normalized()
    s.update(default_resource_hour_cost=.1,maximum_staffed_hour_cost=.4)
    c=decision.preflight(plan,s)["A"]
    assert c["staffed_resources"]==4
    assert c["staffed_hour_cost_decimal"]=="0.4"
    assert c["within_budget"]
    assert {r["name"] for r in c["cost_basis"]}=={"Y","Team"}
    s["dedicated_hour_costs"]={"X":.2}
    with pytest.raises(EventLogWorkspaceError,match="does not match"):
        decision.preflight(plan,s)
