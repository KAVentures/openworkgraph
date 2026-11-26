"""Paired actual decision evaluation on reviewed identical cases."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared.process_model import ProcessModel
from shared.process_policy_regression import compare


def graph(threshold=100):
    return {"goal":"Review an authorized purchase","baseline_source":"user_declared",
        "baseline_path":["Review","Accept"],"baseline_coverage":"user_declared_design",
        "activities":[{"id":"choose","label":"Review","kind":"decision"},
                      {"id":"accept","label":"Accept"},{"id":"escalate","label":"Escalate"}],
        "transitions":[{"source":"choose","target":"accept","rule_id":"small"},
                       {"source":"choose","target":"escalate","rule_id":"large"}],
        "entry_activity_id":"choose","terminal_activity_ids":["accept","escalate"],
        "business_rules":[{"id":"small","text":"Small purchase","user_confirmed":True,
             "predicate":{"conditions":[{"path":"/amount","operator":"lt","expected":threshold}]}},
            {"id":"large","text":"Large purchase","user_confirmed":True,
             "predicate":{"conditions":[{"path":"/amount","operator":"gte","expected":threshold}]}}],
        "decision_tables":[{"id":"purchase","decision_activity_id":"choose",
            "effective_from":"2026-01-01","effective_until":"2027-01-01",
            "source_basis":{"label":"Approved policy reference","document_sha256":"a"*64},
            "rows":[{"id":"low","rule_id":"small","target_activity_id":"accept"},
                    {"id":"high","rule_id":"large","target_activity_id":"escalate"}],
            "user_confirmed":True}]}


def packet(model,revision):
    return {"workflow_id":"purchase_process","revision":revision,
        "content_sha256":str(revision)*64,"process_model":ProcessModel.model_validate(model).model_dump()}


def selected(target):
    return {"status":"unique_match","target_activity_id":target}


def case(n,amount,before,after=None,date="2026-10-26",table="purchase"):
    facts={} if amount is None else {"amount":amount}
    facts["unused"]="SYNTHETIC_PRIVATE_MARKER"
    return {"case_id":f"case_{n}","as_of_date":date,"facts":facts,
        "expectations":[{"table_id":table,"before":before,"after":after or before}]}


def suite(*cases):
    return {"reviewed_by_user":True,"cases":list(cases)}


def test_paired_classification_and_no_fact_values():
    a,b=packet(graph(100),1),packet(graph(200),2)
    tests=suite(case(1,50,selected("accept")),
        case(2,150,selected("escalate")),
        case(3,150,selected("accept")),
        case(4,150,selected("escalate"),selected("accept")),
        case(5,None,{"status":"unknown"}),
        case(6,250,selected("accept")),
        case(7,50,{"status":"expired"},date="2027-01-01"))
    result=compare(a,b,tests)
    assert result["classification_counts"]=={"stable_pass":3,"regression":1,"improvement":1,
        "intended_change_matches":1,"persistent_mismatch":1}
    assert result["before_matches"]==5 and result["after_matches"]==5
    assert result["reviewed_expectations"]==7
    assert result["per_table"][0]["changed_observed_behavior"]==3
    assert result["unreviewed_table_case_pairs"]==0
    assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(result)
    assert not result["facts_retained"] and not result["execution_authorized"]
    assert result==compare(a,b,tests)
    changed=deepcopy(tests);changed["cases"][0]["facts"]["unused"]="another"
    assert result["suite_sha256"]!=compare(a,b,changed)["suite_sha256"]


def test_removed_table_is_explicit_not_automatic_pass():
    old=graph();new=deepcopy(old);new["decision_tables"]=[]
    result=compare(packet(old,1),packet(new,2),suite(
        case(1,50,selected("accept"),{"status":"table_missing"})))
    row=result["cases"][0]["checks"][0]
    assert row["classification"]=="intended_change_matches"
    assert row["table_identity_discontinuous"]
    assert row["after"]=={"status":"table_missing","target_activity_id":None}
    with pytest.raises(ValueError,match="missing table"):
        compare(packet(old,1),packet(new,2),suite(case(1,50,selected("accept"))))


def test_changed_expectations_are_not_labeled_regression_or_improvement():
    a,b=packet(graph(100),1),packet(graph(200),2)
    result=compare(a,b,suite(
        case(1,50,selected("accept"),selected("escalate")),
        case(2,50,selected("escalate"),selected("accept")),
        case(3,50,selected("escalate"),{"status":"gap"})))
    assert result["classification_counts"]=={"changed_expectation_after_mismatch":1,
        "changed_expectation_after_matches":1,"changed_expectations_both_mismatch":1}


@pytest.mark.parametrize("modify",[
    lambda v:v.update(reviewed_by_user=False),
    lambda v:v.update(reviewed_by_user=1),
    lambda v:v["cases"].append(deepcopy(v["cases"][0])),
    lambda v:v["cases"][0].update(case_id="native-record-id"),
    lambda v:v["cases"][0].update(as_of_date="2026-02-30"),
    lambda v:v["cases"][0]["expectations"][0].update(table_id="nonexistent"),
    lambda v:v["cases"][0]["expectations"][0].update(after=selected("nonexistent")),
    lambda v:v["cases"][0]["expectations"][0].update(after={"status":"unknown","target_activity_id":"accept"}),
    lambda v:v["cases"][0]["expectations"][0].update(after={"status":"first_match"}),
])
def test_invalid_review_inputs(modify):
    value=suite(case(1,50,selected("accept")));modify(value)
    with pytest.raises(ValueError):
        compare(packet(graph(),1),packet(graph(),2),value)


def test_scope_and_direction():
    a,b=packet(graph(),1),packet(graph(),2)
    value=suite(case(1,50,selected("accept")))
    for left,right in [(a,a),(b,a),(a,{**b,"workflow_id":"different"})]:
        with pytest.raises(ValueError,match="ordered revisions"):
            compare(left,right,value)


def test_http_auth_review_digest_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_process_policy_regression import graph,suite,case,selected
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"X-OWG-Case-Facts-Authorized":"true"}
    r=c.post("/v1/process-catalog",json={"title":"Regression","user_confirmed":True,"process_model":graph()},headers=h)
    assert r.status_code==200,r.text
    a=r.json()
    r=c.post("/v1/process-catalog",json={"workflow_id":a["workflow_id"],"title":"Regression",
        "expected_revision":a["revision"],"user_confirmed":True,"process_model":graph(200)},headers=h)
    assert r.status_code==200,r.text
    b=r.json()
    params={"workflow_id":a["workflow_id"],"before_revision":a["revision"],"after_revision":b["revision"],
        "before_sha256":a["content_sha256"],"after_sha256":b["content_sha256"]}
    url="/v1/process-catalog/policy-regression"
    value=suite(case(1,150,selected("escalate")))
    assert c.post(url,params=params,json=value,headers=bearer).status_code in (401,403)
    assert c.post(url,params=params,json=value,headers=h).status_code==403
    authorized={**h,"X-OWG-Case-Facts-Authorized":"true"}
    r=c.post(url,params=params,json=value,headers=authorized)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["classification_counts"]=={"regression":1}
    assert c.post(url,params={**params,"before_sha256":"0"*64},json=value,headers=authorized).status_code==409
    assert c.post(url,params=params,content=b'{"reviewed_by_user":true,"reviewed_by_user":false}',
        headers={**authorized,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    p=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr
