"""Synthetic agent-plan preflight; no source writes, false outcome claims or AI calls."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server.agent_plan_preflight import AgentPreflightError, preflight_agent_plan

ROOT=Path(__file__).resolve().parents[1]


def sample_document():
    steps=[{
        "id":f"step_{i:03d}", "tool":None,
        "tool_binding_confirmed":False,
        "action_permission_granted":False,
        "activity":name,
    } for i,name in enumerate(("Receive","Review","Close"),1)]
    return {
        "format":"openworkgraph.agent-plan-preflight.v1",
        "workflow":{
            "format":"openworkgraph.agent-starter.v1",
            "review_status":"human_reviewed_design_not_authorized_execution",
            "security":{
                "live_actions_possible":False,
                "runtime_tool_permissions_granted":False,
                "source_records_included":False,
            },
            "to_be":{
                "steps":steps,
                "user_proposed_not_production_process":True,
                "manual_review_conditions":[],
                "automatic_decision_routing_enabled":False,
            },
            "goal":{"text":"PRIVATE CUSTOMER DATA NOT FOR OUTPUT"},
        },
        "candidate":{
            "format":"openworkgraph.agent-candidate-plan.v1",
            "intent":"shadow_only",
            "exception_handling":"human_escalation",
            "proposed_actions":[{
                "step_id":f"step_{i:03d}",
                "action_kind":"read_only" if i==1 else "draft_only",
                "source_state_checked":True,
                "outcome_evidence_mode":"independent_source_readback",
            } for i in range(1,4)],
        },
        "simulated_cases":[{
            "scenario":"HappyPath",
            "mock_steps":[{
                "step_id":f"step_{i:03d}",
                "mock_result":"mock_success",
            } for i in range(1,4)],
            "claimed_outcome":"unknown",
        }]
    }


def test_clean_synthetic_flow_cannot_ever_grant_deployment_or_outcomes():
    result=preflight_agent_plan(sample_document())
    assert result["structural_review_passed"] is True
    assert result["plan_blockers"]==[]
    assert result["synthetic_cases_with_findings"]==0
    assert result["target_step_count"]==3
    for key in ("business_outcomes_verified","source_systems_contacted",
                "tool_calls_executed","model_invoked","authorization_granted",
                "deployment_ready","observed_outcome_uplift_measured",
                "live_vs_baseline_comparison_performed","persisted"):
        assert result[key] is False
    assert "PRIVATE CUSTOMER DATA" not in json.dumps(result)


def test_missed_steps_unknown_sources_and_false_success_claim_are_flagged():
    example=sample_document()
    example["simulated_cases"][0]["mock_steps"]=[
        {"step_id":"step_002","mock_result":"mock_failure"},
        {"step_id":"step_001","mock_result":"mock_success"},
        {"step_id":"step_001","mock_result":"mock_unknown"},
    ]
    example["simulated_cases"][0]["claimed_outcome"]="claimed_success"
    result=preflight_agent_plan(example)
    issues=set(result["synthetic_cases"][0]["issues"])
    assert {"duplicate_simulated_step","simulated_step_failed",
            "simulated_step_order_deviation","simulated_step_unknown",
            "simulated_incomplete_path","claimed_outcome_not_independently_verified"}.issubset(issues)
    assert result["structural_review_passed"] is False
    assert result["synthetic_cases"][0]["real_business_outcome_verified"] is False


def test_write_intent_missing_receipts_and_inferred_permissions_block():
    example=sample_document()
    example["candidate"]["intent"]="live"
    example["candidate"]["exception_handling"]="not_configured"
    example["candidate"]["proposed_actions"][0].update({
        "action_kind":"write",
        "outcome_evidence_mode":"mock_only",
        "source_state_checked":False,
    })
    result=preflight_agent_plan(example)
    assert set(result["plan_blockers"]) >= {
        "live_execution_requested","write_action_has_no_live_authorization",
        "source_state_not_checked","write_without_source_readback_plan",
        "unverified_exception_handling",
    }
    assert result["authorization_granted"] is False


def test_human_review_conditions_cannot_be_auto_handled():
    x=sample_document()
    x["workflow"]["to_be"]["manual_review_conditions"]=[{
        "at_step_id":"step_002",
        "executable":False,
        "requires_independent_policy_review":True,
    }]
    x["candidate"]["exception_handling"]="mock_only"
    result=preflight_agent_plan(x)
    assert "declared_review_conditions_not_handled" in result["plan_blockers"]
    assert result["deployment_ready"] is False


@pytest.mark.parametrize("change", [
    lambda d:d.update(format="unrecognized"),
    lambda d:d.update(extra_key="PRIVATE"),
    lambda d:d["workflow"]["security"].update(live_actions_possible=True),
    lambda d:d["workflow"]["to_be"]["steps"][0].update(action_permission_granted=True),
    lambda d:d["workflow"]["to_be"]["steps"][0].update(tool="jira-update"),
    lambda d:d["candidate"].update(intent=[]),
    lambda d:d["candidate"].update(exception_handling={}),
    lambda d:d["candidate"]["proposed_actions"][0].update(action_kind=[]),
    lambda d:d["candidate"]["proposed_actions"][0].update(outcome_evidence_mode=[]),
    lambda d:d["candidate"]["proposed_actions"][0].update(source_state_checked="yes"),
    lambda d:d["simulated_cases"][0].update(claimed_outcome=[]),
    lambda d:d["simulated_cases"][0]["mock_steps"][0].update(mock_result=[]),
    lambda d:d.update(simulated_cases=[]),
    lambda d:d.update(simulated_cases=[d["simulated_cases"][0]]*25),
])
def test_malformed_or_unsafe_untrusted_inputs_rejected_without_exceptions(change):
    data=sample_document()
    change(data)
    with pytest.raises(AgentPreflightError):
        preflight_agent_plan(data)


def test_auth_and_size_bounds_for_local_endpoint(tmp_path):
    program=r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc={
  "format":"openworkgraph.agent-plan-preflight.v1",
  "workflow":{
    "format":"openworkgraph.agent-starter.v1",
    "review_status":"human_reviewed_design_not_authorized_execution",
    "security":{"live_actions_possible":False,
                "runtime_tool_permissions_granted":False,
                "source_records_included":False},
    "to_be":{"steps":[{"id":"step_001","tool":None,
                       "tool_binding_confirmed":False,"action_permission_granted":False}],
             "user_proposed_not_production_process":True,
             "manual_review_conditions":[]}},
  "candidate":{
    "format":"openworkgraph.agent-candidate-plan.v1","intent":"shadow_only",
    "exception_handling":"human_escalation",
    "proposed_actions":[{"step_id":"step_001","action_kind":"manual_review",
                         "source_state_checked":False,"outcome_evidence_mode":"none"}]},
  "simulated_cases":[{"scenario":"Baseline",
    "mock_steps":[{"step_id":"step_001","mock_result":"mock_unknown"}],
    "claimed_outcome":"unknown"}]
}
with TestClient(app) as client:
    url="/v1/process-design/preflight"
    assert client.post(url,json=doc).status_code==401
    api={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.post(url,json=doc,headers=api).status_code==403
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    okay=client.post(url,json=doc,headers=owner)
    assert okay.status_code==200,okay.text
    assert okay.headers.get("cache-control")=="no-store"
    assert okay.json()["tool_calls_executed"] is False
    assert okay.json()["structural_review_passed"] is False
    assert client.post(url,content=b"x"*(65536+1),
        headers={**owner,"Content-Type":"application/json"}).status_code==413
    assert client.post(url,content=b"{bad",
        headers={**owner,"Content-Type":"application/json"}).status_code==422
    assert client.post(url,json=doc,
        headers={**owner,"Content-Type":"text/plain"}).status_code==415
'''
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    run=subprocess.run([sys.executable,"-c",program],cwd=ROOT,env=env,
                       capture_output=True,text=True,timeout=90)
    assert run.returncode==0,f"stdout={run.stdout}\nstderr={run.stderr}"


def test_optional_ui_is_not_background_agent_executor():
    routes=(ROOT/"server/workflow_evidence_routes.py").read_text(encoding="utf-8")
    html=(ROOT/"dashboard/index.html").read_text(encoding="utf-8")
    js=(ROOT/"dashboard/agent_preflight.js").read_text(encoding="utf-8")
    ci=(ROOT/".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert '<script src="/agent-preflight.js"></script>' in html
    assert '@app.get("/agent-preflight.js")' in routes
    assert '@app.post("/v1/process-design/preflight")' in routes
    assert "dashboard_session_valid" in routes
    assert "node --check dashboard/agent_preflight.js" in ci
    assert "run.addEventListener('click'" in js
    assert "if(!await window.__owgAuthReady)" in js
    assert "cache:'no-store'" in js
    assert "Download structural preflight report" in js
    assert "openworkgraph-agent-preflight-report.json" in js
    for forbidden in ("sendBeacon", "localStorage", "indexedDB", "eval(", "new Function("):
        assert forbidden not in js
