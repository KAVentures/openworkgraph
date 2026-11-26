"""Enumerate route possibilities and shared binary outcome completions."""
from fractions import Fraction as F
from itertools import product, combinations

from shared import agent_procurement_router_rehearsal as r
from shared.agent_procurement_router_change import _pair_row, METRICS
from shared.agent_procurement_portfolio import _pair


def _row(dest, expected, fallback, cost, time):
    return dict(case_id="case_1",repeat=1,true_category="normal",intended_candidate_id=expected,
                possible_candidate_ids=[k for k in dest if k is not None],manual_path_possible=None in dest,
                router_slot_id="slot",status="classified",fallback={"lower":fallback,"upper":fallback},
                router_cost=r._money_bounds((F(cost),F(cost))),router_person_ms=r._rational((F(time),F(time))))


def test_exhaustive_shared_evidence_pair_bounds():
    ids=(None,"a","b")
    subsets=[c for n in range(1,4) for c in combinations(ids,n)]
    intervals=((0,0),(1,1),(0,1))
    n=0
    for expected,left,right,outcomes in product(ids,subsets,subsets,product(intervals,repeat=3)):
        paths={k:dict(success=v,critical=v,non_delivery=v,cost=(10*i,10*i),person_ms=(20*i,20*i))
               for i,(k,v) in enumerate(zip(ids,outcomes))}
        a,b=_row(left,expected,0,3,7),_row(right,expected,1,1,2)
        out=_pair_row(a,b,paths)
        vals=[]
        for l,q,completion in product(left,right,product(*(range(v[0],v[1]+1) for v in outcomes))):
            x,y=completion[ids.index(l)],completion[ids.index(q)]
            vals.append((y-x,int(x==1 and y==0),int(x==0 and y==1),int(x==0 and y==1),
                         int(expected is None and l is None and q is not None),int(l!=q),1,
                         10*(ids.index(l)-ids.index(q))+2,20*(ids.index(l)-ids.index(q))+5))
        for i,k in enumerate(METRICS):
            assert out[k]=={"lower":min(v[i] for v in vals),"upper":max(v[i] for v in vals)}
        assert _pair(out["paired_variable_cost_savings"])==(min(v[7] for v in vals),max(v[7] for v in vals))
        assert r._read_time(out["paired_released_person_ms"])==(min(v[8] for v in vals),max(v[8] for v in vals))
        n+=1
    assert n==3969


def test_identical_uncertain_arm_does_not_cancel_separate_router_cost():
    paths={None:dict(success=(0,1),critical=(0,1),non_delivery=(0,1),cost=(0,None),person_ms=(0,None))}
    a,b=_row((None,),None,0,3,7),_row((None,),None,0,1,2)
    b["router_cost"]=r._money_bounds((F(1),None))
    result=_pair_row(a,b,paths)
    for k in ("success_change","new_outcome_regression","new_critical","new_non_delivery"):
        assert result[k]=={"lower":0,"upper":0}
    assert _pair(result["paired_variable_cost_savings"])==(None,F(2))
    assert r._read_time(result["paired_released_person_ms"])==(F(5),F(5))
