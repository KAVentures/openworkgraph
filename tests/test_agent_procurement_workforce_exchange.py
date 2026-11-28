"""Offline exchange validation and conservative cross-site accounting."""
from copy import deepcopy
from fractions import Fraction
from itertools import product
from types import SimpleNamespace

import pytest

from server import agent_procurement_workforce_exchange as exchange
from shared.agent_procurement import digest
from shared.agent_procurement_portfolio import _pair


def amount(a,b):
    def value(x):
        return None if x is None else {"numerator":str(x),"denominator":"1"}
    return {"lower_microunits":value(a),"upper_microunits":value(b)}


def row(name,*,currency="SEK",case=None,source=None,upper=20,eligible=True,incident=False):
    a={"cases":1,"recorded":1,"terminal":1,
       "counts":{k:{"lower":0,"upper":1} for k in exchange.local.METRICS},
       "attribution_counts":{"pass":1,"fail":0,"unknown":0},"variable_cost":amount(10,upper)}
    return {"journal_tag":digest(["journal",name]),"plan_tag":digest(["plan",name]),
        "currency":currency,"cutoff_ms":90,"scheduled_cases":2,"started_cases":2,
        "recorded_cases":2,"credential_attributed":1,"cohort_identity_available":True,
        "case_tags":[digest(["case",case or name,0]),digest(["case",name,1])],
        "source_tags":[digest(["source",source or name,0]),digest(["source",name,1])],
        "arms":{k:deepcopy(a) for k in exchange.local.ARMS},"local_eligible":eligible,
        "rollback_review":incident,"attention":False}


def pinned(alias,rows,*,when=100,unreadable=0):
    p={"format":exchange.FORMAT,"installation_alias":alias,
       "correlation_namespace_sha256":digest("namespace"),"exported_at_ms":when,
       "unreadable_journals":unreadable,"rows":rows}
    return {"packet":p,"expected_packet_sha256":digest(p)}


def request(*packets):
    return {"expected_correlation_namespace_sha256":digest("namespace"),
        "maximum_packet_age_ms":100,"confirmed":True,"packets":list(packets)}


def repin(p):
    p["expected_packet_sha256"]=digest(p["packet"])


def test_separate_currency_and_exact_bounds():
    a,b,c=row("a"),row("b",upper=None),row("c",currency="USD")
    report=exchange.review(request(pinned("site-a",[a]),pinned("site-b",[b,c])),now=120)
    assert report["included_journals"]==3
    assert [g["currency"] for g in report["currency_totals"]]==["SEK","USD"]
    sek=report["currency_totals"][0]
    assert sek["recorded_cases"]==4 and sek["credential_attributed"]==2
    assert _pair(sek["arms"]["incumbent"]["variable_cost"])==(20,None)
    assert _pair(sek["arms"]["incumbent"]["mean_variable_cost"])==(10,None)
    assert not report["source_identity_verified"] and not report["realized_savings_verified"]


def test_symmetric_collision_including_locally_excluded_and_incidents():
    a=row("a",case="shared",eligible=False,incident=True)
    b=row("b",case="shared",source="shared")
    c=row("c",source="shared")
    d=row("d")
    report=exchange.review(request(pinned("site-a",[a]),pinned("site-b",[b,c,d],unreadable=1)),now=120)
    assert report["included_journals"]==1 and report["excluded_journals"]==3
    assert report["journal_count"]==5 and report["totals_may_be_incomplete"]
    assert report["journals"][0]["rollback_review"]
    by_tag={x["journal_tag"]:x for x in report["journals"]}
    assert "case_tag_overlap" in by_tag[b["journal_tag"]]["exclusion_reasons"]
    assert "source_tag_overlap" in by_tag[c["journal_tag"]]["exclusion_reasons"]


@pytest.mark.parametrize("kind",["pin","namespace","alias","stale","future","count","tag","cost","metric","extra","confirmation"])
def test_invalid_packets_refused(kind):
    req=request(pinned("site-a",[row("a")]),pinned("site-b",[row("b")]))
    p=req["packets"][1];r=p["packet"]["rows"][0]
    if kind=="pin":p["expected_packet_sha256"]="f"*64
    elif kind=="namespace":p["packet"]["correlation_namespace_sha256"]="f"*64
    elif kind=="alias":p["packet"]["installation_alias"]="SITE-A"
    elif kind=="stale":p["packet"]["exported_at_ms"]=19
    elif kind=="future":p["packet"]["exported_at_ms"]=121
    elif kind=="count":r["recorded_cases"]=3
    elif kind=="tag":r["case_tags"][1]=r["case_tags"][0]
    elif kind=="cost":r["arms"]["incumbent"]["variable_cost"]=amount(20,10)
    elif kind=="metric":r["arms"]["incumbent"]["counts"]["success"]["upper"]=2
    elif kind=="extra":r["raw_case_input"]="not permitted"
    else:req["confirmed"]=False
    if kind!="pin":repin(p)
    with pytest.raises(ValueError):exchange.review(req,now=120)


def test_tag_domain_separation_and_exact_collision_oracle():
    key=bytes(range(32))
    assert exchange._tag(key,"case","x")!=exchange._tag(key,"source","x")
    assert exchange._tag(key,"case","x")!=exchange._tag(bytes(reversed(key)),"case","x")
    for values in product(range(2),repeat=9):
        rows=[((str(i),str(i)),SimpleNamespace(plan_tag=str(values[3*i]),
              case_tags=[str(values[3*i+1])],source_tags=[str(values[3*i+2])])) for i in range(3)]
        actual=exchange._collisions(rows)
        for i,(scope,_) in enumerate(rows):
            expected={k+"_tag_overlap" for j,k in enumerate(("plan","case","source"))
                      if any(i!=l and values[3*i+j]==values[3*l+j] for l in range(3))}
            assert actual[scope]==expected


def test_export_minimized_and_round_trip(monkeypatch):
    state={"secret":"must-not-export"}
    scope=("private-workflow",1,"private-monitor")
    raw_case=digest("private case")
    raw_source=digest("private source")
    a=row("a")["arms"]["incumbent"]
    monkeypatch.setattr(exchange.local,"read_inventory",lambda:([(scope,state,[])],{},[]))
    monkeypatch.setattr(exchange.local,"_overlaps",lambda records:{scope:set()})
    monkeypatch.setattr(exchange.local,"_tokens",lambda s:({raw_case},{raw_source}))
    monkeypatch.setattr(exchange.local,"_row",lambda *args:{
        "title":"private title","plan_sha256":digest("plan"),"currency":"SEK","as_of_ms":90,
        "scheduled_cases":1,"started_window_cases":1,"recorded_cases":1,
        "credential_authenticated_observations":1,"arms":{
            "incumbent":a,"challenger":{**deepcopy(a),"cases":0,"recorded":0,"terminal":0,
                "counts":{k:{"lower":0,"upper":0} for k in exchange.local.METRICS},
                "attribution_counts":{"pass":0,"fail":0,"unknown":0},"variable_cost":amount(0,0)}},
        "included_in_totals":True,"priority":"monitoring"})
    monkeypatch.setattr(exchange.time,"time",lambda:0.1)
    key=bytes(range(32)).hex()
    result=exchange.export({"installation_alias":"site-a","correlation_key_hex":key,"confirmed":True})
    for private in (key,raw_case,raw_source,"private-workflow","private-monitor","private title","must-not-export"):
        assert private not in str(result)
    req={"expected_correlation_namespace_sha256":result["packet"]["correlation_namespace_sha256"],
         "maximum_packet_age_ms":100,"confirmed":True,
         "packets":[{"expected_packet_sha256":result["packet_sha256"],"packet":result["packet"]}]}
    assert exchange.review(req,now=120)["included_journals"]==1
