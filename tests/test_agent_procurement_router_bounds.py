"""Independent enumeration oracle for every small routing policy and prediction state."""
from fractions import Fraction as F
from itertools import product
from types import SimpleNamespace

from shared.agent_procurement_router_rehearsal import _slot, _path, _money_bounds, _read_time, METRICS, ZERO
from shared.agent_procurement_portfolio import _pair


def test_exhaustive_routing_bounds():
    cats=["normal","exception","missing_facts"]
    choices=[None,"a","b"]
    paths={
        None:dict(cost=(100,100),saving=(0,0),capacity=(0,0),success=(1,1),regression=(0,0),critical=(0,0),non_delivery=(0,0)),
        "a":dict(cost=(20,20),saving=(80,80),capacity=(50,50),success=(1,1),regression=(0,0),critical=(0,0),non_delivery=(0,0)),
        "b":dict(cost=(150,150),saving=(-50,-50),capacity=(-10,-10),success=(0,0),regression=(1,1),critical=(1,1),non_delivery=(0,0))}
    statuses=["classified","timeout","abstain","transport_failed","invalid_output","withdrawn","not_recorded"]
    cases=0
    for mapping in product(choices,repeat=3):
        assigns=dict(zip(cats,mapping))
        for actual,status,known_cost,known_time in product(cats,statuses,[None,0,3],[None,0,36000]):
            for predicted in cats if status=="classified" else [None]:
                pred=None if status=="not_recorded" else SimpleNamespace(status=status,predicted_category=predicted,
                    direct_cost_microunits=known_cost,human_person_ms=known_time,elapsed_ms=1)
                result=_slot({},actual,pred,assigns,paths,100)
                unresolved=status in ("not_recorded","withdrawn")
                if unresolved:
                    possible=[(c,assigns[c],0) for c in cats]+[(None,None,1)]
                elif status=="classified":
                    possible=[(predicted,assigns[predicted],0)]
                else:
                    possible=[(None,None,1)]
                vals=[]
                for pred_cat,destination,fallback in possible:
                    p=paths[destination]
                    vals.append(dict(accuracy=int(pred_cat==actual),agreement=int(destination==assigns[actual]),
                        manual_leakage=int(assigns[actual] is None and destination is not None),
                        fallback=fallback,ai_assigned=int(destination is not None),
                        **{k:p[k][0] for k in ("success","regression","critical","non_delivery")}))
                for k in METRICS:
                    assert result[k]=={"lower":min(v[k] for v in vals),"upper":max(v[k] for v in vals)}
                direct=0 if pred is None else known_cost or 0
                time=0 if pred is None else known_time or 0
                overhead=F(direct)+F(time*100,3600000)
                p=[paths[d] for _,d,_ in possible]
                unknown=unresolved or known_cost is None or known_time is None
                assert _pair(result["routed_cost"])==(min(x["cost"][0] for x in p)+overhead,
                    None if unknown else max(x["cost"][1] for x in p)+overhead)
                assert _pair(result["paired_savings"])==(None if unknown else min(x["saving"][0] for x in p)-overhead,
                    max(x["saving"][1] for x in p)-overhead)
                assert _read_time(result["released_person_ms"])==(
                    None if unresolved or known_time is None else min(x["capacity"][0] for x in p)-time,
                    max(x["capacity"][1] for x in p)-time)
                oracle=paths[assigns[actual]]["cost"][0]
                deltas=[x["cost"][0]-oracle for x in p]
                assert _pair(result["cost_difference_from_ideal_path"])==(min(deltas),max(deltas))
                assert _pair(result["cost_difference_from_ideal_including_router"])==(
                    min(deltas)+overhead,None if unknown else max(deltas)+overhead)
                cases+=1
    assert cases==6561


def test_unknown_manual_and_candidate_path():
    row={"manual_cost":_money_bounds((F(0),None)),"candidate_cost":_money_bounds((F(5),None)),
         "paired_savings":_money_bounds((None,None)),"manual_declared_outcome":None,"candidate_declared_outcome":None,
         "declared_outcome_comparison":{"regression":{"lower":0,"upper":1}},
         "manual_person_ms":{"lower":0,"upper":None},"candidate_person_ms":{"lower":2,"upper":None},
         "shadow_critical":None,"shadow_non_delivery":None}
    manual=_path(row,manual=True);alternative=_path(row)
    assert manual["regression"]==(0,0) and manual["saving"]==ZERO and manual["capacity"]==ZERO
    assert alternative["regression"]==(0,1) and alternative["capacity"]==(None,None)
    assignments={"normal":"a","exception":None,"missing_facts":None}
    p=SimpleNamespace(status="classified",predicted_category="normal",direct_cost_microunits=0,human_person_ms=0,elapsed_ms=1)
    result=_slot({},"normal",p,assignments,{None:manual,"a":alternative},100)
    assert _pair(result["cost_difference_from_ideal_path"])==ZERO
    assert _pair(result["routed_cost"])==(5,None)
