"""One saved process change reviewed through all four integrated capabilities."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

from shared.process_change_review import review
from test_process_policy_regression import graph,packet,case,selected,suite


def test_change_review_reports_actual_regression_and_affected_coverage():
    before,after=packet(graph(100),1),packet(graph(200),2)
    result=review(before,after,suite(case(1,150,selected("escalate"))))
    change=result["change_review"]
    assert change["assessment"]=="review_findings"
    assert change["structural_after"]["complete"]
    assert change["structural_after"]["completion_reachable"]
    assert change["affected_decision_coverage"]==[{"table_id":"purchase",
        "affected_before":True,"affected_after":True,"reviewed_case_count":1,
        "unreviewed_case_count":0,"after_matches":0,"after_mismatches":1}]
    assert change["unreviewed_affected_table_case_pairs"]==0
    assert not change["execution_authorized"] and not change["independent_approval"]
    assert result["classification_counts"]=={"regression":1}
    assert review(before,after,suite(case(1,150,selected("escalate"),selected("accept"))))["change_review"]["assessment"]=="no_listed_findings_in_bounded_checks"


def test_removed_decision_table_requires_explicit_expectation():
    before=graph()
    after=deepcopy(before)
    after["decision_tables"]=[]
    result=review(packet(before,1),packet(after,2),suite(
        case(1,50,selected("accept"),{"status":"table_missing"})))
    row=result["change_review"]["affected_decision_coverage"][0]
    assert row["affected_before"] and not row["affected_after"]
    assert row["after_matches"]==1


def test_integrated_http_path_checks_same_revision_and_all_endpoints(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_process_policy_regression import graph,suite,case,selected
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),
       "X-OWG-Case-Facts-Authorized":"true"}
    r=c.post("/v1/process-catalog",json={"title":"Integrated review","user_confirmed":True,"process_model":graph()},headers=h)
    assert r.status_code==200,r.text
    a=r.json()
    r=c.post("/v1/process-catalog",json={"workflow_id":a["workflow_id"],"title":"Integrated review",
        "expected_revision":a["revision"],"user_confirmed":True,"process_model":graph(200)},headers=h)
    assert r.status_code==200,r.text
    b=r.json()
    ref={k:b[k] for k in ("workflow_id","revision","content_sha256")}
    base="/v1/process-catalog/"
    structural=c.get(base+"token-assurance",params=ref,headers=h)
    assert structural.status_code==200,structural.text
    assert structural.json()["reference"]==ref and structural.json()["complete"]
    suggested=c.get(base+"decision-domain/suggestions",params=ref,headers=h)
    assert suggested.status_code==200,suggested.text
    domain=c.post(base+"decision-domain",params=ref,headers=h,json={"as_of_date":"2026-10-26",
       "dimensions":[{"path":"/amount","choices":[{"value":50},{"value":150},{"value":250}]}]})
    assert domain.status_code==200,domain.text
    assert domain.json()["enumerated_combinations"]==3
    pins={"workflow_id":a["workflow_id"],"before_revision":a["revision"],"after_revision":b["revision"],
        "before_sha256":a["content_sha256"],"after_sha256":b["content_sha256"]}
    policy=c.get(base+"policy-impact",params=pins,headers=h)
    assert policy.status_code==200,policy.text
    assert len(policy.json()["rule_changes"])==2
    value=suite(case(1,150,selected("escalate"),selected("accept")))
    combined=c.post(base+"policy-regression",params={**pins,"include_change_review":True},json=value,headers=h)
    assert combined.status_code==200,combined.text
    result=combined.json()
    assert result["change_review"]["potential_policy_impact"]==policy.json()
    assert result["change_review"]["structural_after"]["reference"]==ref
    assert result["classification_counts"]=={"intended_change_matches":1}
    assert result["change_review"]["assessment"]=="no_listed_findings_in_bounded_checks"
    assert combined.headers["cache-control"]=="no-store"
    assert c.post(base+"policy-regression",params={**pins,"include_change_review":True,"after_sha256":"0"*64},json=value,headers=h).status_code==409
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    p=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr
