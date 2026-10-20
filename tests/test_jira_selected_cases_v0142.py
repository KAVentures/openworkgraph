"""Opt-in, tenant-scoped multi-case Jira audit previews, no implicit joins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from server.jira_batch_process_preview import jira_selected_cases_process

ROOT=Path(__file__).resolve().parents[1]


def receipt(case: int, *, partial: bool = False):
    return {
        "provider": "jira",
        "availability": "source_reported",
        "audit_available": True,
        "audit_complete": not partial,
        "review_audit_events": [
            {"action": "status_transition",
             "observed_at": f"2026-10-20T10:0{case}:00Z",
             "source_event_ref": f"opaque-{case}-1",
             "from_actor_ref": "private employee",
             "issue_key": f"PRIVATE-{case}"},
            {"action": "assignee_transition",
             "observed_at": f"2026-10-20T10:0{case}:30Z",
             "source_event_ref": f"opaque-{case}-2",
             "to_actor_ref": "private receiver",
             "issue_key": f"PRIVATE-{case}"},
            {"action": "status_transition",
             "observed_at": f"2026-10-20T10:0{case}:45Z",
             "source_event_ref": f"opaque-{case}-3"},
        ],
    }


def test_two_identically_shaped_jira_issues_remain_two_distinct_cases():
    result=jira_selected_cases_process([receipt(1),receipt(2)])
    assert result["requested_record_count"]==2
    assert result["audited_record_count"]==2
    assert result["all_selected_source_audits_complete"] is True
    assert result["cross_case_identity_inferred"] is False
    process=result["process"]
    assert process["case_count"]==2
    assert process["variant_count"]==1
    assert process["variants"][0]["case_count"]==2
    assert process["input_rows"]==6
    assert process["source_identity_comparability_verified"] is False
    assert process["cross_case_links_inferred"] is False
    assert process["business_outcomes_verified"] is False
    encoded=json.dumps(result)
    for forbidden in ("PRIVATE-1", "private employee", "private receiver",
                      "opaque-1-1", "opaque-2-3"):
        assert forbidden not in encoded


def test_partial_and_failed_source_reads_are_not_silent():
    out=jira_selected_cases_process([receipt(1,partial=True),None,receipt(2)])
    assert out["requested_record_count"]==3
    assert out["audited_record_count"]==2
    assert out["partial_audit_record_count"]==1
    assert out["unavailable_or_empty_record_count"]==1
    assert out["all_selected_source_audits_complete"] is False
    assert out["process"]["case_count"]==2
    assert out["process"]["source_event_window_may_be_partial"] is True


def test_unavailable_case_without_source_audit_does_not_imply_no_work():
    out=jira_selected_cases_process([None, {"provider":"jira","availability":"unavailable"}])
    assert out["availability"]=="no_eligible_source_audits"
    assert out["process"] is None
    assert out["unavailable_or_empty_record_count"]==2


def test_ui_and_route_require_intentional_selection():
    source=(ROOT/"server/local_source_routes.py").read_text(encoding="utf-8")
    ui=(ROOT/"dashboard/workflow_evidence.js").read_text(encoding="utf-8")
    assert '@router.post("/v1/process-mining/jira-selected-cases")' in source
    assert "if len(body) + len(chunk) > 2048" in source
    assert 'confirm_source_reads' in source
    assert '_require_dashboard(request)' in source
    assert 'jira_selected_cases_process(receipts)' in source
    assert 'data-jira-batch-select' in ui
    assert "Compare selected Jira source audits" in ui
    assert "compare.onclick=async()" in ui
    assert "window.__owgAuthReady" in ui
    assert "body:JSON.stringify({resource_refs:refs,confirm_source_reads:true})" in ui


def test_real_http_requires_owner_session_explicit_2_to_8_refs_and_no_store(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,local_source_routes
import json
ref1="owg:r:"+"a"*32
ref2="owg:r:"+"b"*32
called=[]
def fake_read(value):
    called.append(value)
    return {
      "provider":"jira","availability":"source_reported",
      "audit_available":True,"audit_complete":True,
      "review_audit_events":[
        {"action":"status_transition","observed_at":"2026-10-20T10:00:00Z",
         "source_event_ref":"event1-"+value[-1]},
        {"action":"assignee_transition","observed_at":"2026-10-20T10:01:00Z",
         "source_event_ref":"event2-"+value[-1]},
      ],
    }
local_source_routes.jira_issue_receipt=fake_read
with TestClient(app) as client:
    url="/v1/process-mining/jira-selected-cases"
    body={"resource_refs":[ref1,ref2],"confirm_source_reads":True}
    assert client.post(url,json=body).status_code==401
    api={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.post(url,json=body,headers=api).status_code==403
    auth={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    assert client.post(url,json={**body,"confirm_source_reads":False},
        headers=auth).status_code==403
    assert client.post(url,json={"resource_refs":[ref1],"confirm_source_reads":True},
        headers=auth).status_code==422
    assert client.post(url,json={"resource_refs":[ref1,ref1],"confirm_source_reads":True},
        headers=auth).status_code==422
    assert client.post(url,json={"resource_refs":[ref1,"PRIVATE-123"],"confirm_source_reads":True},
        headers=auth).status_code==422
    assert client.post(url,content=b"x"*2049,headers={**auth,"Content-Type":"application/json"}).status_code==413
    assert called==[]
    good=client.post(url,json=body,headers=auth)
    assert good.status_code==200,good.text
    assert good.headers.get("cache-control")=="no-store"
    assert good.json()["process"]["case_count"]==2
    assert called==[ref1,ref2]
    assert ref1 not in good.text and ref2 not in good.text
'''
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    run=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,
                       capture_output=True,text=True,timeout=90)
    assert run.returncode==0,f"stdout={run.stdout}\nstderr={run.stderr}"
