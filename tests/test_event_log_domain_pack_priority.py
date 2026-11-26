from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from server import event_log_domain_pack_priority as priority
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from order_to_cash_fixture import order_to_cash, MAPPING, TYPES


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def installed(*, missing_resource=False):
    doc = order_to_cash()
    if missing_resource:
        # OCEL imports preserve org:resource. Remove the resource from the
        # selected credit approval, not from an unrelated or nonactivated case.
        event = next(e for e in doc["events"] if e["id"] == "so1-credit")
        event["attributes"] = [a for a in event["attributes"] if a["name"] != "org:resource"]
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()),
                               name="priority", source_format="ocel")["log_id"]
    spec = {"version":"1.0.0","mapping":deepcopy(MAPPING)}
    saved = packs.install(log,"order_to_cash",spec)
    return log,spec,saved


def request(**changes):
    return {"case_type":TYPES["invoice"],"weights":[{"rule_index":4,"weight":5},{"rule_index":5,"weight":2}],
            "minimum_confirmed":7,"include_uncertain":True,"limit":1,**changes}


def test_installed_invoice_worklist_exact_points_and_no_write():
    log,_,saved=installed()
    result=priority.review(log,"order_to_cash",request())
    assert result["cases_evaluated"]==20
    assert result["confirmed_threshold_cases"]==1
    assert result["uncertain_threshold_cases"]==0
    assert result["below_threshold_cases"]==19
    assert result["maximum_score"]==7
    assert result["cases"]==[{"case_id":"bd9","lower":7,"upper":7,"threshold_status":"confirmed",
                              "violated_rule_indices":[4,5],"unknown_rule_indices":[]}]
    assert result["next_after"] is None
    assert not result["configuration_modified"]
    assert packs.installed(log)["installs"][0]["installation_token"]==saved["installation_token"]


def test_catalog_and_mixed_case_types_refused():
    log,_,_=installed()
    groups=priority.available(log,"order_to_cash")["groups"]
    assert {g["case_type"] for g in groups}=={TYPES["order"],TYPES["invoice"],TYPES["item"]}
    with pytest.raises(workspace.EventLogWorkspaceError,match="one exact case type"):
        priority.review(log,"order_to_cash",request(weights=[{"rule_index":1,"weight":7}]))


def test_exact_ordering_and_uncertainty_oracle():
    conn=duckdb.connect()
    try:
        conn.execute("CREATE TABLE priority_cases(case_id VARCHAR,lo BIGINT,hi BIGINT,violations BIGINT,unknowns BIGINT)")
        conn.executemany("INSERT INTO priority_cases VALUES (?,?,?,?,?)",[
            ("a",7,7,3,0),("b",5,7,1,2),("c",5,7,1,2),("d",5,5,1,0),
            ("e",0,7,0,3),("f",2,2,2,0),("g",0,0,0,0)])
        basis={"rules":[{"rule_index":4},{"rule_index":5}],"minimum_confirmed":5,"include_uncertain":True}
        all_ids=[];after=None
        while True:
            result=priority._page(conn,basis,after,1)
            assert result["cases_evaluated"]==7
            assert result["confirmed_threshold_cases"]==4
            assert result["uncertain_threshold_cases"]==1
            assert result["below_threshold_cases"]==2
            assert result["matching_cases"]==5
            all_ids.extend(c["case_id"] for c in result["cases"])
            after=result["next_after"]
            if after is None:break
        assert all_ids==["a","b","c","d","e"]
        certain=priority._page(conn,{**basis,"include_uncertain":False},None,100)
        assert [x["case_id"] for x in certain["cases"]]==["a","b","c","d"]
        assert certain["uncertain_threshold_cases"]==1
    finally:
        conn.close()


def test_pagination_refuses_changed_installation_and_selection():
    log,spec,_=installed()
    req=request(minimum_confirmed=1)
    result=priority.review(log,"order_to_cash",req)
    continuation={**req,"expected_basis_sha256":result["basis_sha256"],
                  "after":{"case_id":"bd9","lower":7,"upper":7}}
    assert priority.review(log,"order_to_cash",continuation)["cases"]==[]
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.review(log,"order_to_cash",{**continuation,"minimum_confirmed":2})
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.review(log,"order_to_cash",continuation)


@pytest.mark.parametrize("changes",[
    {"weights":[]},{"weights":[{"rule_index":4,"weight":True}]},
    {"weights":[{"rule_index":4,"weight":101}]},
    {"weights":[{"rule_index":4,"weight":4},{"rule_index":4,"weight":4}]},
    {"minimum_confirmed":8},{"include_uncertain":1},{"limit":0},
    {"after":{"lower":1,"upper":2,"case_id":"x"}}
])
def test_strict_invalid_request(changes):
    with pytest.raises(workspace.EventLogWorkspaceError):
        priority.review("unused","order_to_cash",request(**changes))


def test_complete_work_budgets(monkeypatch):
    log,_,_=installed()
    monkeypatch.setattr(priority,"MAX_OUTCOMES",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="outcome budget"):
        priority.review(log,"order_to_cash",request())
    monkeypatch.setattr(priority,"MAX_SOURCE_ROWS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="source-row budget"):
        priority.review(log,"order_to_cash",request())


def test_complete_export_exact_and_pinned(monkeypatch):
    log,spec,_=installed()
    req=request(minimum_confirmed=1)
    first=priority.review(log,"order_to_cash",req)
    export={k:v for k,v in req.items() if k!="limit"}
    export["expected_basis_sha256"]=first["basis_sha256"]
    result=priority.export(log,"order_to_cash",export)
    assert result["complete_export"] is True
    assert result["basis_sha256"]==first["basis_sha256"]
    assert result["cases"]==first["cases"]
    assert len(result["cases"])==result["matching_cases"]
    assert result["next_after"] is None
    assert first["complete_export"] is False
    monkeypatch.setattr(priority,"MAX_EXPORT_CASES",0)
    with pytest.raises(workspace.EventLogWorkspaceError,match="Complete export exceeds"):
        priority.export(log,"order_to_cash",export)
    monkeypatch.setattr(priority,"MAX_EXPORT_CASES",5000)
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.export(log,"order_to_cash",export)


def test_complete_export_requires_pin():
    log,_,_=installed()
    req={k:v for k,v in request().items() if k!="limit"}
    with pytest.raises(workspace.EventLogWorkspaceError,match="exact reviewed selection"):
        priority.export(log,"order_to_cash",req)


def test_complete_export_empty_population():
    log,_,_=installed()
    req=request(case_type=TYPES["order"],weights=[{"rule_index":0,"weight":1}],
                minimum_confirmed=1)
    first=priority.review(log,"order_to_cash",req)
    result=priority.export(log,"order_to_cash",{
        **{k:v for k,v in req.items() if k!="limit"},"expected_basis_sha256":first["basis_sha256"]})
    assert result["complete_export"] is True
    assert result["matching_cases"]==0 and result["cases"]==[] and result["next_after"] is None


def test_case_inspection_matches_reviewed_scores_and_details():
    log,spec,_=installed()
    req=request(minimum_confirmed=1)
    first=priority.review(log,"order_to_cash",req)
    selection={k:v for k,v in req.items() if k!="limit"}
    body={**selection,"expected_basis_sha256":first["basis_sha256"],"case_id":"bd9"}
    detail=priority.inspect_case(log,"order_to_cash",body)
    assert detail["lower"]==detail["upper"]==7
    assert detail["projected_events"]==1
    assert detail["threshold_status"]=="confirmed"
    assert [r["status"] for r in detail["outcomes"]]==["violated","violated"]
    assert sum(r["confirmed_points"] for r in detail["outcomes"])==detail["lower"]
    assert sum(r["possible_points"] for r in detail["outcomes"])==detail["upper"]
    assert "no later step" in detail["outcomes"][0]["detail"]
    assert detail["outcomes"][1]["detail"]=="0 occurrences"
    assert not detail["resource_names_returned"]
    with pytest.raises(workspace.EventLogWorkspaceError,match="outside"):
        priority.inspect_case(log,"order_to_cash",{**body,"case_id":"bd0"})
    with pytest.raises(workspace.EventLogWorkspaceError,match="not present"):
        priority.inspect_case(log,"order_to_cash",{**body,"case_id":"so9"})
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        priority.inspect_case(log,"order_to_cash",body)


def test_case_inspection_unknown_never_claims_same_resource():
    log,_,_=installed(missing_resource=True)
    req=request(case_type=TYPES["order"],weights=[{"rule_index":2,"weight":9}],
                minimum_confirmed=1,include_uncertain=True)
    first=priority.review(log,"order_to_cash",req)
    detail=priority.inspect_case(log,"order_to_cash",{
        **{k:v for k,v in req.items() if k!="limit"},
        "expected_basis_sha256":first["basis_sha256"],"case_id":"so1"})
    assert detail["lower"]==0 and detail["upper"]==9
    assert detail["threshold_status"]=="uncertain"
    assert detail["outcomes"][0]["status"]=="unknown"
    assert "Insufficient recorded evidence" in detail["outcomes"][0]["detail"]


def test_workbenches_share_nonblocking_execution_guard():
    from server import event_log_domain_pack_benchmark as benchmark
    from server import event_log_domain_pack_overlap as overlap
    from server import event_log_domain_pack_deadlines as deadlines
    assert priority._GUARD is overlap._GUARD is deadlines._GUARD is benchmark._GUARD
    log,_,_=installed()
    with benchmark._GUARD:
        with pytest.raises(workspace.EventLogWorkspaceError,match="Another domain pack analysis"):
            priority.review(log,"order_to_cash",request())


def test_dashboard_only_priority_http_contract(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_domain_packs as packs
from test_event_log_domain_pack_priority import installed, request
log,spec,_=installed()
url=f"/v1/event-logs/{log}/domain-packs/order_to_cash/priority"
req=request()
with TestClient(app) as c:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert c.get(url,headers=bearer).status_code in (401,403)
    catalog=c.get(url,headers=owner)
    assert catalog.status_code==200,catalog.text
    assert catalog.headers["cache-control"]=="no-store"
    assert c.post(url,json=req,headers=bearer).status_code in (401,403)
    r=c.post(url,json=req,headers=owner)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    page=r.json()
    reviewed={**{k:v for k,v in req.items() if k!="limit"},
              "expected_basis_sha256":page["basis_sha256"]}
    for suffix,body in (("/export",reviewed),("/case",{**reviewed,"case_id":"bd9"})):
        assert c.post(url+suffix,json=body,headers=bearer).status_code in (401,403)
        assert c.post(url+suffix,json=body).status_code in (401,403)
        r=c.post(url+suffix,json=body,headers=owner)
        assert r.status_code==200,r.text
        assert r.headers["cache-control"]=="no-store"
        assert r.json()["basis_sha256"]==page["basis_sha256"]
        assert c.post(url+suffix,json={**body,"expected_basis_sha256":"0"*64},headers=owner).status_code==422
        assert c.post(url+suffix,json={**body,"unexpected":True},headers=owner).status_code==422
        headers={**owner,"Content-Type":"application/json"}
        assert c.post(url+suffix,content=b'{"case_id":"a","case_id":"b"}',headers=headers).status_code==422
        assert c.post(url+suffix,content=b"{}"+b" "*9000,headers=headers).status_code==413
    detail=c.post(url+"/case",json={**reviewed,"case_id":"bd9"},headers=owner).json()
    assert detail["lower"]==detail["upper"]==7
    assert detail["resource_names_returned"] is False
    exported=c.post(url+"/export",json=reviewed,headers=owner).json()
    assert exported["complete_export"] is True
    assert exported["cases"]==page["cases"]
    packs.install(log,"order_to_cash",{**spec,"parameters":{"payment_terms_days":20}})
    assert c.post(url+"/export",json=reviewed,headers=owner).status_code==422
    assert c.post(url+"/case",json={**reviewed,"case_id":"bd9"},headers=owner).status_code==422
"""
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json"),
         "PYTHONPATH":str(root/"tests")+os.pathsep+str(root)}
    result=subprocess.run([sys.executable,"-c",code],cwd=root,env=env,
                          capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
