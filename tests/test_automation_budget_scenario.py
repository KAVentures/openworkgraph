"""Independent subset oracle, exact cost arithmetic, and real pipeline snapshots."""
from itertools import combinations
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_pipeline as p
from server import automation_budget_scenario as s


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(p,"DATA_DIR",tmp_path)


def idea(title,amount=None,deps=(),currency="SEK",basis="estimate"):
    i=p.create({"title":title})
    return p.plan(i["idea_id"],{"expected_revision":i["revision"],"depends_on":[d["idea_id"] for d in deps],
        "budget":None if amount is None else {"amount":amount,"currency":currency,"basis":basis}})


def request(items,limit=100,max_selected=18,required=(),**overrides):
    return {"snapshot_sha256":s.inventory()["snapshot_sha256"],"currency":"SEK",
            "budget_basis":"estimate","budget_limit":limit,"max_selected":max_selected,
            "candidates":[{"idea_id":i["idea_id"],"revision":i["revision"],"priority":priority,
                           "required":i["idea_id"] in required} for i,priority in items],**overrides}


def test_exact_optimum_and_alternatives_against_independent_oracle():
    a=idea("Prerequisite",30);b=idea("Dependent",40,[a]);c=idea("Independent",50)
    d=idea("Small",20);e=idea("Second dependent",15,[b])
    items=[(a,5),(b,80),(c,65),(d,30),(e,55)]
    spec=request(items,limit=90,max_selected=3)
    result=s.solve(spec)
    oracle=[]
    for n in range(4):
        for subset in combinations(items,n):
            ids={i["idea_id"] for i,_ in subset}
            if any(not set(i["planning"]["depends_on"])<=ids for i,_ in subset):
                continue
            cost=sum(Decimal(str(i["planning"]["budget"]["amount"])) for i,_ in subset)
            if cost>90:
                continue
            oracle.append((-sum(points for _,points in subset),cost,n,tuple(sorted(ids))))
    oracle.sort()
    assert result["subsets_considered"]==32 and result["feasible_subsets"]==len(oracle)
    assert [(-a["priority_points"],Decimal(a["budget_used"]),a["selected_count"],tuple(a["selected_idea_ids"]))
            for a in result["alternatives"]]==oracle[:5]
    assert sum(result["rejected_by_first_reason"].values())+result["feasible_subsets"]==32
    assert not result["modeled_benefits_summed"] and not result["execution_authorized"]
    assert s.inventory()["snapshot_sha256"]==spec["snapshot_sha256"]


def test_missing_prerequisite_and_transitive_blocker_are_disclosed():
    a=idea("Unselected",20);b=idea("Blocked",10,[a]);c=idea("Also blocked",10,[b])
    result=s.solve(request([(b,50),(c,100)],required=[c["idea_id"]]))
    assert result["status"]=="no_feasible_portfolio"
    by_id={i["idea_id"]:i for i in result["candidates"]}
    assert by_id[b["idea_id"]]["blockers"][0]["code"]=="unfinished_dependency_not_selected"
    assert by_id[c["idea_id"]]["blockers"][0]["code"]=="blocked_selected_prerequisite"
    assert set(result["required_closure_ids"])=={b["idea_id"],c["idea_id"]}


def test_currency_basis_and_missing_budget_never_become_free_candidates():
    a=idea("Different currency",0,currency="USD");b=idea("Other basis",0,basis="approved_budget")
    c=idea("Missing");d=idea("Valid",0)
    result=s.solve(request([(a,100),(b,100),(c,100),(d,1)],limit=0))
    assert result["alternatives"][0]["selected_idea_ids"]==[d["idea_id"]]
    assert result["feasible_subsets"]==2
    assert {i["title"]:i["blockers"][0]["code"] for i in result["candidates"] if i["blockers"]}=={
        "Different currency":"different_currency_or_basis","Other basis":"different_currency_or_basis",
        "Missing":"missing_budget"}


def test_required_closure_subtotal_never_adds_incompatible_currencies_or_bases():
    usd=idea("USD prerequisite",200,currency="USD")
    other=idea("Other basis",300,basis="approved_budget")
    good=idea("Comparable",10,[usd,other])
    result=s.solve(request([(usd,1),(other,1),(good,20)],required=[good["idea_id"]]))
    assert result["status"]=="no_feasible_portfolio"
    assert Decimal(result["required_closure_known_budget"])==Decimal("10")
    assert result["required_closure_cost_complete"] is False


def test_decimal_budget_exact_required_and_count_constraints():
    a=idea("A",0.1);b=idea("B",0.2)
    result=s.solve(request([(a,20),(b,20)],limit=0.3,required=[a["idea_id"],b["idea_id"]]))
    assert result["feasible_subsets"]==1
    assert Decimal(result["alternatives"][0]["budget_used"])==Decimal("0.3")
    assert Decimal(result["alternatives"][0]["budget_remaining"])==0
    result=s.solve(request([(a,20),(b,20)],limit=0.3,max_selected=1,required=[a["idea_id"],b["idea_id"]]))
    assert result["status"]=="no_feasible_portfolio"


def test_implemented_prerequisite_does_not_consume_scenario_budget():
    a=idea("Done",1000)
    a=p.transition(a["idea_id"],{"to":"assessing","expected_revision":a["revision"]})
    a=p.assess(a["idea_id"],{"expected_revision":a["revision"],"ease":{k:5 for k in p.EASE_QUESTIONS},
        "cases_per_month":100,"minutes_per_case":10,"eligible_share":1})
    a=p.transition(a["idea_id"],{"to":"approved","expected_revision":a["revision"]})
    a=p.transition(a["idea_id"],{"to":"implemented","expected_revision":a["revision"]})
    b=idea("Next",10,[a])
    result=s.solve(request([(b,100)],limit=10))
    assert result["alternatives"][0]["selected_idea_ids"]==[b["idea_id"]]


def test_snapshot_and_revision_changes_refused():
    a=idea("A",1);spec=request([(a,10)])
    p.plan(a["idea_id"],{"expected_revision":a["revision"],"budget":{"amount":2,"currency":"SEK"}})
    with pytest.raises(p.PipelineError,match="changed"):
        s.solve(spec)
    spec["snapshot_sha256"]=s.inventory()["snapshot_sha256"]
    with pytest.raises(p.PipelineError,match="changed"):
        s.solve(spec)


@pytest.mark.parametrize("bad",[
    {"budget_limit":True},{"budget_limit":-1},{"currency":"sek"},{"max_selected":19},
    {"candidates":[]},{"unexpected":1},
])
def test_bad_requests(bad):
    a=idea("A",1)
    with pytest.raises(p.PipelineError):
        s.solve(request([(a,10)],**bad))


def test_duplicate_candidates_and_boolean_priority():
    a=idea("A",1)
    with pytest.raises(p.PipelineError,match="distinct"):
        s.solve(request([(a,1),(a,2)]))
    spec=request([(a,10)])
    spec["candidates"][0]["priority"]=True
    with pytest.raises(p.PipelineError):
        s.solve(spec)


def test_real_http_routes_dashboard_auth_and_no_store(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_budget_scenario import idea,request
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    a=idea("A",10);b=idea("B",20,[a])
    url="/v1/automation-pipeline/budget-scenario"
    assert client.get(url+"/inventory",headers=bearer).status_code in (401,403)
    assert client.post(url,json=request([(a,10)]),headers=bearer).status_code in (401,403)
    inv=client.get(url+"/inventory",headers=h)
    assert inv.status_code==200 and inv.headers["cache-control"]=="no-store"
    result=client.post(url,json=request([(a,10),(b,100)],limit=30),headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    assert result.json()["alternatives"][0]["priority_points"]==110
    assert client.post(url,content=b'{"max_selected":1,"max_selected":2}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
