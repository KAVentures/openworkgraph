from fractions import Fraction as F
from itertools import product
import random

import pytest

from server import agent_procurement_workforce_allocation as allocation


def option(i,saving,gross,cases,extra,eligible=True):
    return {"id":i,"eligible":eligible,"saving":F(saving),"gross":F(gross),
            "ai_cases":cases,"additional":F(extra),"released":-F(extra),"reasons":[]}


def test_exact_optimizer_against_independent_oracle():
    rng=random.Random(926)
    for _ in range(600):
        groups=[[allocation._defer(),*[option(str(j),rng.randrange(1,20),rng.randrange(0,20),
                 rng.randrange(1,10),rng.randrange(0,20),rng.choice([True,True,False]))
                 for j in range(3)]] for i in range(3)]
        limits=tuple(rng.randrange(0,40) for _ in range(3))
        selected,count,feasible=allocation.solve(groups,limits)
        oracle=[]
        for combo in product(*groups):
            if not all(x["eligible"] for x in combo):continue
            g,c,e=(sum(x[k] for x in combo) for k in ("gross","ai_cases","additional"))
            if g<=limits[0] and c<=limits[1] and e<=limits[2]:
                oracle.append((sum(x["saving"] for x in combo),g,e,c,tuple(x["id"] or "" for x in combo)))
        optimum=sorted(oracle,key=lambda x:(-x[0],*x[1:]))[0]
        actual=(sum(x["saving"] for x in selected),sum(x["gross"] for x in selected),
                sum(x["additional"] for x in selected),sum(x["ai_cases"] for x in selected),
                tuple(x["id"] or "" for x in selected))
        assert actual==optimum
        assert feasible==len(oracle)


def test_too_many_alternatives_and_deterministic_tie():
    g=[allocation._defer(),option("z",5,2,1,1),option("a",5,2,1,1)]
    assert allocation.solve([g],(2,1,1))[0][0]["id"]=="a"
    with pytest.raises(ValueError,match="too many"):
        allocation.solve([g]*12,(100,100,100))


def money(a,b):
    def val(v):return None if v is None else {"numerator":str(v),"denominator":"1"}
    return {"lower_microunits":val(a),"upper_microunits":val(b)}


def test_gross_ai_cost_not_netted_and_missing_evidence_blocked():
    p={"portfolio_id":"x","declared_evidence_status":"pass","first_year_savings":money(100,200),
       "annual_released_person_ms":{"lower":{"numerator":"-7","denominator":"1"}},
       "financial_evidence_complete":True,"capacity_evidence_complete":True,"ai_cases":20,
       "candidate_and_routing_annual_fixed_microunits":"3","candidate_and_routing_one_time_microunits":"5",
       "routing_variable_cost":{"numerator":"11","denominator":"1"},
       "routes":[{"candidate_id":"agent","annual_cases":20,"annual_variable_cost":money(30,40)},
                 {"candidate_id":None,"annual_cases":10,"annual_variable_cost":money(100,200)}]}
    o=allocation._option(p)
    assert o["eligible"] and o["gross"]==59 and o["saving"]==100 and o["additional"]==7
    p["routes"][0]["annual_variable_cost"]=money(30,None)
    assert not allocation._option(p)["eligible"]
    p["declared_evidence_status"]="unknown"
    assert "outcome_or_trial_gate" in allocation._option(p)["reasons"]
