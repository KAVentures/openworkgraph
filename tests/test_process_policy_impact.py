"""Policy impacts are structural, exact-revision and scoped to one workflow."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared.process_policy_impact import compare
from shared.process_model import ProcessModel


def model():
    nodes=[{"id":"choose","label":"Choose","kind":"decision"},
           {"id":"accept","label":"Accept"},{"id":"reject","label":"Reject"},
           {"id":"second","label":"Second","kind":"decision"},
           {"id":"finish","label":"Finish"},{"id":"review","label":"Review"}]
    def rule(ident,path,operator,expected):
        return {"id":ident,"text":"Declared "+ident,"user_confirmed":True,
                "predicate":{"conditions":[{"path":path,"operator":operator,"expected":expected}]}}
    rules=[rule("r1","/amount","gte",10),rule("r2","/amount","lt",10),
           rule("r3","/decision","eq","accept"),rule("r4","/decision","eq","reject"),
           rule("unused","/x","in",[True,1,2])]
    transitions=[{"source":"choose","target":"accept","rule_id":"r1"},
                 {"source":"choose","target":"reject","rule_id":"r2"},
                 {"source":"accept","target":"second"},{"source":"reject","target":"second"},
                 {"source":"second","target":"finish","rule_id":"r3"},
                 {"source":"second","target":"review","rule_id":"r4"}]
    def table(ident,activity,rows,required=()):
        return {"id":ident,"decision_activity_id":activity,"hit_policy":"FIRST",
                "effective_from":"2026-01-01","source_basis":{"label":"Policy","document_sha256":"a"*64},
                "user_confirmed":True,"rows":[{"id":"row_"+r,"rule_id":r,"target_activity_id":target}
                                             for r,target in rows],"required_decisions":list(required)}
    return {"goal":"Review policy impact","baseline_path":["Choose"],"baseline_source":"user_declared",
            "entry_activity_id":"choose","terminal_activity_ids":["finish","review"],
            "activities":nodes,"transitions":transitions,"business_rules":rules,
            "decision_tables":[table("first","choose",[("r1","accept"),("r2","reject")]),
                table("next","second",[("r3","finish"),("r4","review")],
                      [{"table_id":"first","fact_path":"/decision"}])]}


def packet(m,rev):
    return {"workflow_id":"workflow1","revision":rev,"content_sha256":str(rev)*64,
            "process_model":ProcessModel.model_validate(m).model_dump()}


def test_rule_change_propagates_through_decision_dependencies_and_graph():
    a=model();b=deepcopy(a)
    b["business_rules"][0]["predicate"]["conditions"][0]["expected"]=20
    b["business_rules"][4]["text"]="Changed unused rule"
    result=compare(packet(a,1),packet(b,2))
    assert {r["rule_id"]:r["changes"] for r in result["rule_changes"]}=={
        "r1":["structured_predicate_changed"],"unused":["text_changed"]}
    for side in ("before_impact","after_impact"):
        impact=result[side]
        assert impact["direct_guarded_edges"]==[["choose","accept"]]
        assert impact["direct_decision_table_ids"]==["first"]
        assert {"table_id":"next","path":["first","next"]} in impact["decision_dependency_witnesses"]
        assert impact["potentially_affected_terminal_ids"]==["finish","review"]
        assert impact["unreferenced_changed_rule_ids"]==["unused"]
    assert not result["policy_equivalence_proven"] and not result["execution_authorized"]


def test_membership_order_duplicate_numeric_representation_and_bool_identity():
    a=model();b=deepcopy(a)
    b["business_rules"][4]["predicate"]["conditions"][0]["expected"]=[2.0,1.0,True,1]
    result=compare(packet(a,1),packet(b,2))
    assert result["rule_changes"][0]["changes"]==["predicate_representation_changed"]
    b["business_rules"][4]["predicate"]["conditions"][0]["expected"]=[2,1]
    assert compare(packet(a,1),packet(b,2))["rule_changes"][0]["changes"]==["structured_predicate_changed"]


def test_first_order_and_effective_date_changes_are_explicit():
    a=model();b=deepcopy(a)
    b["decision_tables"][0]["rows"].reverse()
    b["decision_tables"][0]["effective_from"]="2026-02-01"
    result=compare(packet(a,1),packet(b,2))
    assert result["decision_table_changes"][0]["changes"]==["effective_from","first_row_order_changed"]
    assert result["after_impact"]["direct_decision_table_ids"]==["first"]


def test_unique_row_reorder_not_mislabeled_first_priority():
    a=model();a["decision_tables"][0]["hit_policy"]="UNIQUE"
    b=deepcopy(a);b["decision_tables"][0]["rows"].reverse()
    assert compare(packet(a,1),packet(b,2))["decision_table_changes"][0]["changes"]==["unique_row_order_changed"]


def test_rule_binding_removal_and_new_manual_gate_and_outcome():
    a=model();b=deepcopy(a)
    b["decision_tables"]=[]
    b["transitions"][0]["rule_id"]="r3"
    b["manual_review_conditions"]=[{"activity_id":"accept","kind":"exception_condition",
        "condition_label":"Ask owner","response":"pause_for_review"}]
    b["outcome_expectations"]=[{"terminal_activity_id":"finish","operation":"github_pr_merged","user_confirmed":True}]
    result=compare(packet(a,1),packet(b,2))
    assert result["transition_changes"]==[{"edge":["choose","accept"],"old_exists":True,"new_exists":True,
                                           "old_rule_id":"r1","new_rule_id":"r3"}]
    assert len(result["decision_table_changes"])==2
    assert result["manual_review_changes"]==[{"identity":["accept","exception_condition","ask owner"],"change":"added"}]
    assert result["outcome_expectation_changes"][0]["change"]=="added"


def test_unchanged_model_and_workflow_scope():
    a=packet(model(),1);b=packet(model(),2)
    result=compare(a,b)
    assert result["rule_changes"]==result["decision_table_changes"]==[]
    assert result["after_impact"]["downstream_activity_witnesses"]==[]
    with pytest.raises(ValueError):compare(a,a)
    with pytest.raises(ValueError):compare(a,{**b,"workflow_id":"other"})


def test_http_exact_digest_and_dashboard_scope(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_process_policy_impact import model
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    m=model()
    r=c.post("/v1/process-catalog",json={"title":"Policy impact","expected_revision":0,
        "user_confirmed":True,"process_model":m},headers=h)
    assert r.status_code==200,r.text
    a=r.json();m["business_rules"][0]["predicate"]["conditions"][0]["expected"]=20
    r=c.post("/v1/process-catalog",json={"workflow_id":a["workflow_id"],"title":"Policy impact",
        "expected_revision":a["revision"],"user_confirmed":True,"process_model":m},headers=h)
    assert r.status_code==200,r.text
    b=r.json()
    params={"workflow_id":a["workflow_id"],"before_revision":a["revision"],"after_revision":b["revision"],
        "before_sha256":a["content_sha256"],"after_sha256":b["content_sha256"]}
    url="/v1/process-catalog/policy-impact"
    assert c.get(url,params=params,headers=bearer).status_code in (401,403)
    result=c.get(url,params=params,headers=h)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    assert result.json()["rule_changes"][0]["rule_id"]=="r1"
    assert c.get(url,params={**params,"before_sha256":"0"*64},headers=h).status_code==409
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
