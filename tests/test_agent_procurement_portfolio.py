"""Exhaustive small conditional portfolio comparison against direct accounting."""
from fractions import Fraction as F
from itertools import product
from types import SimpleNamespace

from shared.agent_procurement_portfolio import PortfolioPolicy,evaluate

CATS=("normal","exception","missing_facts")
IDS=("a","b")


def money(x):
    return None if x is None else {"numerator":str(x.numerator),"denominator":str(x.denominator)}


def un(x):
    return None if x is None else F(int(x["numerator"]),int(x["denominator"]))


def bounds(a,b):
    return {"lower_microunits":money(a),"upper_microunits":money(b)}


def arm(c,t,s,n=2,unknown=False):
    return {"attempts":n,"total_cost":bounds(c,None if unknown else c),
            "human_person_ms":{"lower":t,"upper":None if unknown else t},
            "declared_business_outcomes":{"success_count_lower":s,"success_count_upper":n if unknown else s}}


def policy(vol,assign):
    ai=sum(v for v,c in zip(vol,assign) if c)
    return PortfolioPolicy.model_validate({
        "category_volumes":[{"category":c,"annual_cases":v} for c,v in zip(CATS,vol)],
        "portfolios":[{"portfolio_id":"test","assignments":[{"category":c,"candidate_id":a} for c,a in zip(CATS,assign)],
                       "routing_annual_microunits":7 if ai else 0,"routing_one_time_microunits":5 if ai else 0,
                       "routing_cost_per_ai_case_microunits":3 if ai else 0,"routing_human_ms_per_ai_case":36000 if ai else 0}],
        "avoidable_manual_annual_microunits":19,"confirm_conditional_category_assignment":True,
        "confirm_non_overlapping_routing_costs":True})


def population():
    manual=[arm(200,7200000,2),arm(220,7200000,2),arm(240,7200000,2)]
    fixed={"a":SimpleNamespace(annual_microunits=23,one_time_microunits=11),
           "b":SimpleNamespace(annual_microunits=37,one_time_microunits=13)}
    costs={"a":[100,170,200],"b":[180,110,130]}
    success={"a":[2,1,2],"b":[1,2,2]}
    summary=[]
    for cid in IDS:
        groups=[]
        for i,cat in enumerate(CATS):
            x=arm(costs[cid][i],3600000,success[cid][i]);reg=2-success[cid][i];diff=-reg
            saved=200+20*i-costs[cid][i]
            groups.append({"category":cat,"distinct_cases":1,"manual":manual[i],"candidate":x,
                           "paired_savings":bounds(saved,saved),"declared_outcome_review":{
                               "regression_count":{"lower":reg,"upper":reg},
                               "success_count_change":{"lower":diff,"upper":diff}}})
        summary.append({"candidate_id":cid,"trial_gate_status":"pass","category_reviews":groups})
    return fixed,costs,success,summary


def test_exhaustive_volume_and_assignment_cross_check():
    fixed,costs,success,summary=population()
    for vol in product((1,2,5),repeat=3):
        for assign in product((None,"a","b"),repeat=3):
            ai=sum(v for v,c in zip(vol,assign) if c);active=set(assign)-{None}
            result=evaluate(policy(vol,assign),sum(vol),50,fixed,summary,money,100)["portfolios"][0]
            baseline=sum(F((200+20*i)*v,2) for i,v in enumerate(vol))+50
            expected=sum(F((costs[c][i] if c else 200+20*i)*v,2) for i,(v,c) in enumerate(zip(vol,assign)))+50
            expected+=sum(fixed[c].annual_microunits for c in active)+(7+4*ai if ai else 0)-(19 if ai==sum(vol) else 0)
            one=sum(fixed[c].one_time_microunits for c in active)+(5 if ai else 0)
            es=sum(F((success[c][i] if c else 2)*v,2) for i,(v,c) in enumerate(zip(vol,assign)))
            assert un(result["annual_cost"]["upper_microunits"])==expected
            assert un(result["first_year_savings"]["lower_microunits"])==baseline-expected-one
            assert un(result["annual_released_person_ms"]["lower"])==ai*(1800000-36000)
            assert un(result["annual_declared_success_count"]["upper"])==es
            assert un(result["annual_declared_regression_count"]["lower"])==sum(vol)-es
            assert result["declared_evidence_status"]==("pass" if es==sum(vol) else "fail")
            assert result["financial_evidence_complete"] and result["capacity_evidence_complete"]


def test_identical_manual_path_cancels_unknown_cost_and_regression():
    fixed,_,_,summary=population()
    for row in summary:
        for g in row["category_reviews"]:
            g["manual"]=arm(0,0,0,unknown=True)
    r=evaluate(policy((10,10,10),(None,None,None)),30,50,fixed,summary,money,100)["portfolios"][0]
    assert r["annual_cost"]["upper_microunits"] is None
    assert un(r["first_year_savings"]["lower_microunits"])==un(r["first_year_savings"]["upper_microunits"])==0
    assert un(r["annual_declared_regression_count"]["upper"])==0
    assert r["declared_evidence_status"]=="unknown"
    assert not r["financial_evidence_complete"] and r["capacity_evidence_complete"]
