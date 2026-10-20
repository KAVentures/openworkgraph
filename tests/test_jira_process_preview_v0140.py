"""Strict local Jira source-audit event-log preview."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from server.jira_process_preview import jira_audit_process_preview

ROOT = Path(__file__).resolve().parents[1]


def _receipt(*, complete: bool = True) -> dict:
    return {
        "provider": "jira", "availability": "source_reported",
        "audit_available": True, "audit_complete": complete,
        "verified_business_outcome": False,
        "verified_operational_outcome": {"verified": True, "kind": "jira_issue_resolved"},
        "review_audit_events": [
            {"action": "status_transition", "observed_at": "2026-10-20T10:00:00Z",
             "source_event_ref": "opaque01", "acting_actor_ref": "private_actor_ref",
             "from_status_ref": "private_status_ref1"},
            {"action": "assignee_transition", "observed_at": "2026-10-20T10:05:00Z",
             "source_event_ref": "opaque02", "to_actor_ref": "private_actor_ref2"},
            {"action": "status_transition", "observed_at": "2026-10-20T10:06:00Z",
             "source_event_ref": "opaque03"},
            {"action": "status_transition", "observed_at": "2026-10-20T10:06:00Z",
             "source_event_ref": "opaque03"},
            {"action": "ignore_custom_field", "observed_at": "2026-10-20T10:07:00Z",
             "source_event_ref": "opaque04", "value": "PRIVATE CUSTOMER VALUE"},
        ],
    }


def test_authorized_source_audit_projects_one_case_without_identifiers():
    result = jira_audit_process_preview(_receipt())
    assert result["availability"] == "source_audit"
    assert result["source_audit_complete"] is True
    proc = result["process"]
    assert proc["case_count"] == 1
    assert proc["input_rows"] == 3
    assert proc["variant_count"] == 1
    assert proc["source"] == "opted_in_authenticated_jira_single_issue_changelog"
    assert proc["metrics"]["median_inter_event_gap_seconds"] == 180
    assert proc["business_outcomes_verified"] is False
    assert proc["source_user_ids_included"] is False
    assert result["source_case_ids_included"] is False
    assert result["verified_business_outcome"] is False
    rendered = json.dumps(result)
    for forbidden in ("opaque01", "private_actor_ref", "private_status_ref1",
                      "PRIVATE CUSTOMER VALUE"):
        assert forbidden not in rendered
    assert proc["variants"][0]["observed_path"] == [
        "Status transition", "Assignee transition", "Status transition",
    ]


def test_unavailable_or_partial_source_never_claims_completeness():
    assert jira_audit_process_preview(None)["availability"] == "unavailable"
    assert jira_audit_process_preview({"availability": "unknown"})["process"] is None
    no_audit = {**_receipt(), "audit_available": False}
    assert jira_audit_process_preview(no_audit)["availability"] == "source_status_only"
    partial = jira_audit_process_preview(_receipt(complete=False))
    assert partial["source_audit_complete"] is False
    assert partial["process"]["source_event_window_may_be_partial"] is True


def test_ui_requires_user_click_and_does_not_enable_jira_background_reads():
    server = (ROOT / "server/local_source_routes.py").read_text(encoding="utf-8")
    ui = (ROOT / "dashboard/workflow_evidence.js").read_text(encoding="utf-8")
    assert '@router.get("/v1/process-mining/jira-case")' in server
    assert "_require_dashboard(request)" in server
    assert "if confirm_source_read is not True:" in server
    assert "asyncio.to_thread(jira_issue_receipt, resource_ref)" in server
    assert 'data-jira-process' in ui
    assert "button.onclick=async()" in ui
    assert "confirm_source_read:'true'" in ui
    assert "window.__owgAuthReady" in ui


def test_real_api_auth_source_opt_in_and_audit_projection(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, local_source_routes
from server.jira_process_preview import jira_audit_process_preview
ref="owg:r:"+"a"*32
called=[]
def fake_reader(value):
    called.append(value)
    return {
      "provider":"jira","availability":"source_reported",
      "audit_available":True,"audit_complete":True,
      "review_audit_events":[
        {"action":"status_transition","observed_at":"2026-10-20T10:00:00Z","source_event_ref":"a"},
        {"action":"assignee_transition","observed_at":"2026-10-20T10:05:00Z","source_event_ref":"b"},
      ]
    }
local_source_routes.jira_issue_receipt=fake_reader
with TestClient(app) as client:
    url="/v1/process-mining/jira-case"
    assert client.get(url,params={"resource_ref":ref,"confirm_source_read":"true"}).status_code==401
    token=local_auth.ensure_api_token()
    bearer={"Authorization":f"Bearer {token}"}
    assert client.get(url,params={"resource_ref":ref,"confirm_source_read":"true"},
        headers=bearer).status_code==403
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    assert client.get(url,params={"resource_ref":ref},headers=owner).status_code==403
    assert client.get(url,params={"resource_ref":"issue-1","confirm_source_read":"true"},headers=owner).status_code==422
    assert called==[]
    response=client.get(url,params={"resource_ref":ref,"confirm_source_read":"true"},headers=owner)
    assert response.status_code==200,response.text
    assert response.headers.get("cache-control")=="no-store"
    assert response.json()["process"]["input_rows"]==2
    assert called==[ref]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
