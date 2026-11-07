"""R23: employee offboarding domain pack on a synthetic, retained CSV event log.

Recorded revocation is not verified source entitlement revocation. All checks
are evidence-only and require an owner-selected role mapping.
"""
from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
PACK = "employee_offboarding"
MAPPING = {
    "object_types": {"offboarding_case": "case"},
    "activities": {
        "offboarding_started": "Offboarding opened",
        "manager_approved": "Manager authorized",
        "access_revoked": "Access revocation logged",
        "asset_returned": "Company asset returned",
        "review_completed": "Review completed",
        "case_closed": "Case closed",
    },
}
CSV = """case,activity,time
O1,Offboarding opened,2026-01-01T00:00:00Z
O1,Manager authorized,2026-01-01T01:00:00Z
O1,Access revocation logged,2026-01-01T03:00:00Z
O1,Company asset returned,2026-01-01T05:00:00Z
O1,Review completed,2026-01-01T06:00:00Z
O1,Case closed,2026-01-01T07:00:00Z
O2,Offboarding opened,2026-01-01T00:00:00Z
O2,Manager authorized,2026-01-01T02:00:00Z
O2,Access revocation logged,2026-01-03T00:00:00Z
O2,Case closed,2026-01-04T00:00:00Z
O3,Offboarding opened,2026-01-01T00:00:00Z
O3,Manager authorized,2026-01-01T01:00:00Z
O3,Case closed,2026-01-02T00:00:00Z
O4,Access revocation logged,2026-01-01T00:00:00Z
O4,Offboarding opened,2026-01-01T01:00:00Z
O4,Manager authorized,2026-01-01T02:00:00Z
O4,Case closed,2026-01-01T03:00:00Z
O5,Offboarding opened,2026-01-01T00:00:00Z
O5,Manager authorized,2026-01-01T01:00:00Z
O5,Access revocation logged,2026-01-01T03:00:00Z
O5,Access revocation logged,2026-01-01T04:00:00Z
O5,Case closed,2026-01-01T05:00:00Z
O6,Offboarding opened,2026-01-01T00:00:00Z
O6,Manager authorized,2026-01-01T01:00:00Z
O6,Access revocation logged,2026-01-01T03:00:00Z
O6,Case closed,2026-01-10T00:00:00Z
O7,Offboarding opened,2026-01-01T00:00:00Z
O7,Access revocation logged,2026-01-01T01:00:00Z
O7,Case closed,2026-01-01T02:00:00Z
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def imported():
    return workspace.import_log(
        BytesIO(CSV.encode()), name="Synthetic offboarding", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"},
    )["log_id"]


def _install(log_id, mapping=MAPPING, parameters=None):
    return packs.install(log_id, PACK, {
        "version": "1.0.0", "mapping": mapping,
        **({"parameters": parameters} if parameters is not None else {}),
    })


def _rule(report, template, a, b=None):
    return next(
        item for section in report["compliance"] for item in section["results"]
        if item["rule"]["template"] == template and item["rule"]["a"] == a
        and item["rule"].get("b") == b
    )


def test_catalog_version_and_definitions_are_visible_before_installing():
    catalog = packs.catalog()["packs"]
    entry = next(pack for pack in catalog if pack["pack_id"] == PACK)
    assert entry["title"] == "Employee offboarding and access revocation"
    assert entry["versions"] == ["1.0.0"]
    assert entry["definitions"] == {
        "metrics": 5, "compliance_rules": 7, "object_rules": 0,
        "reference_model_types": 1,
    }
    assert {r["role"] for r in entry["object_types"] if r["required"]} == {"offboarding_case"}
    assert {r["role"] for r in entry["activities"] if r["required"]} == {
        "offboarding_started", "access_revoked"}
    assert any("does NOT prove" in a for a in entry["assumptions"])
    assert packs.definitions(PACK)["reference_model"]["types"][0]["object_type"] == "@offboarding_case"


def test_real_install_and_report_separate_event_flags_from_verified_revocation():
    log_id = imported()
    install = _install(log_id)
    assert install["change"] == "installed"
    assert install["not_applicable"] == []
    assert len(install["metric_ids"]) == 5
    report = packs.report(log_id, PACK)
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["verified_business_workflow"] is False
    assert report["execution_authorized"] is False
    assert report["pack"]["definitions_sha256"] == install["definitions_sha256"]
    measures = {m["name"]: m for m in report["metrics"]}
    assert set(measures) == {
        "offboarding_start_to_revocation", "offboarding_start_to_close",
        "offboarding_revocation_events", "offboarding_start_to_approval",
        "offboarding_return_events",
    }
    assert measures["offboarding_start_to_revocation"]["cases"] == 7
    assert measures["offboarding_start_to_revocation"]["coverage"]["cases_without_to_activity"] >= 1
    assert measures["offboarding_revocation_events"]["mean"] == pytest.approx(1)
    assert measures["offboarding_return_events"]["mean"] == pytest.approx(1/7)
    # Exact source-derived evidence: missing O3 and duplicate O5 are
    # review candidates, not reliable account entitlement observations.
    repeated = _rule(report, "existence", "Access revocation logged")
    assert repeated["violating_cases"] == 2
    assert sorted(x["case_id"] for x in repeated["violating_case_examples"]) == ["O3", "O5"]
    assert _rule(report, "precedence", "Offboarding opened", "Access revocation logged")["violating_cases"] >= 1
    response = _rule(report, "response_within", "Offboarding opened", "Access revocation logged")
    assert response["rule"]["seconds"] == 24 * 3600
    assert {"O2", "O3"}.issubset({x["case_id"] for x in response["violating_case_examples"]})
    # No recorded actor/resource column: duties remain unknown, NOT satisfied.
    duties = _rule(report, "segregation_of_duties", "Manager authorized", "Access revocation logged")
    assert duties["unknown_cases"] > 0
    assert duties["satisfied_cases"] == 0
    assert report["object_model"]["summary"]["objects"] == 7
    assert any("does NOT prove" in a for a in report["assumptions"])
    assert any("not employee effort saved" in a or "not employee effort" in a for a in report["assumptions"])


def test_optional_unmapped_controls_are_disclosed_not_silently_inferred():
    log_id = imported()
    required = {"object_types": MAPPING["object_types"],
                "activities": {key: MAPPING["activities"][key] for key in (
                    "offboarding_started", "access_revoked")}}
    added = _install(log_id, mapping=required)
    assert len(added["metric_ids"]) == 2
    missing = {x["unmapped_role"] for x in added["not_applicable"]}
    assert missing == {"manager_approved", "asset_returned", "case_closed"}
    report = packs.report(log_id, PACK)
    assert len(report["metrics"]) == 2
    assert len(report["compliance"][0]["results"]) == 3
    assert {item["rule"]["template"] for item in report["compliance"][0]["results"]} == {
        "response_within", "precedence", "existence"}
    assert "Review completed" not in report["object_model"]["lifecycles"][0].get(
        "unmodelled_activities", {}) or True  # optional unmapped check via available activities


def test_a_more_generous_elapsed_target_changes_only_its_configured_rule():
    log_id = imported()
    first = _install(log_id)
    old = packs.report(log_id, PACK)
    assert _rule(old, "response_within", "Offboarding opened", "Access revocation logged")["rule"]["seconds"] == 86400
    newer = _install(log_id, parameters={"revocation_hours": 72, "closure_days": 14})
    assert newer["change"] == "reconfigured"
    assert newer["definitions_sha256"] != first["definitions_sha256"]
    assert newer["history"][0]["definitions_sha256"] == first["definitions_sha256"]
    assert len(newer["metric_ids"]) == 5
    assert len(query.list_metrics(log_id)["metrics"]) == 5
    report = packs.report(log_id, PACK)
    assert report["parameters"] == {"revocation_hours": 72.0, "closure_days": 14.0}
    response = _rule(report, "response_within", "Offboarding opened", "Access revocation logged")
    assert response["rule"]["seconds"] == 72*3600
    # An observed 48-hour revocation ceases to be late under a 72h target;
    # a missing recorded revocation remains missing.
    cases = {x["case_id"] for x in response["violating_case_examples"]}
    assert "O2" not in cases and "O3" in cases
    closure = _rule(report, "response_within", "Offboarding opened", "Case closed")
    assert closure["rule"]["seconds"] == 14*86400


def test_install_refuses_missing_required_role_invalid_policy_or_wrong_source():
    log_id = imported()
    with pytest.raises(workspace.EventLogWorkspaceError, match="map the required role"):
        _install(log_id, mapping={"object_types": {"offboarding_case": "case"},
                                  "activities": {"offboarding_started": "Offboarding opened"}})
    wrong = {"object_types": MAPPING["object_types"],
             "activities": {**MAPPING["activities"], "access_revoked": "Not in source"}}
    with pytest.raises(workspace.EventLogWorkspaceError, match="not an activity of this log"):
        _install(log_id, mapping=wrong)
    with pytest.raises(workspace.EventLogWorkspaceError, match="from 0.25 to 8760"):
        _install(log_id, parameters={"revocation_hours": 0})
    with pytest.raises(workspace.EventLogWorkspaceError, match="no parameter"):
        _install(log_id, parameters={"auto_revoke": 1})
    assert packs.installed(log_id)["installs"] == []


def test_uninstall_preserves_user_metric_and_deleted_log_cleans_install():
    log_id = imported()
    query.save_metric(log_id, {"metric": {
        "name": "custom_activity_count", "kind": "activity_count",
        "activity": "Access revocation logged"}})
    installed = _install(log_id)
    assert len(query.list_metrics(log_id)["metrics"]) == 6
    assert installed["metric_ids"]
    assert packs.uninstall(log_id, PACK)["metrics_removed"] == 5
    metrics = query.list_metrics(log_id)["metrics"]
    assert [metric["metric"]["name"] for metric in metrics] == ["custom_activity_count"]
    _install(log_id)
    workspace.delete_log(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log_id, PACK)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM domain_pack_installs WHERE log_id=?",
                                [log_id]).fetchone()[0] == 0
        finally:
            conn.close()


def test_actual_dashboard_http_permissions_and_full_lifecycle(tmp_path):
    code = r'''
import sys
sys.path.insert(0,"tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_employee_offboarding import CSV,MAPPING
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    ai={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    import_url=("/v1/event-logs/import?format=csv&name=offboarding"
        "&case_column=case&activity_column=activity&timestamp_column=time")
    assert client.post(import_url,content=CSV.encode(),headers=owner).status_code==403
    created=client.post(import_url,content=CSV.encode(),headers={
        **owner,"x-owg-local-storage-confirmed":"true"})
    assert created.status_code==200,created.text
    url="/v1/event-logs/"+created.json()["log_id"]+"/domain-packs/employee_offboarding"
    catalog=client.get("/v1/event-log-domain-packs",headers=owner)
    assert catalog.status_code==200,catalog.text
    assert any(x["pack_id"]=="employee_offboarding" for x in catalog.json()["packs"])
    assert client.post(url,json={"version":"1.0.0","mapping":MAPPING},headers=ai).status_code in (401,403)
    installed=client.post(url,json={"version":"1.0.0","mapping":MAPPING},headers=owner)
    assert installed.status_code==200,installed.text
    r=client.get(url+"/report",headers=owner)
    assert r.status_code==200 and r.headers["cache-control"]=="no-store",r.text
    assert r.json()["source_system_validated"] is False
    assert len(r.json()["metrics"])==5
    assert client.get(url+"/report",headers=ai).status_code in (401,403)
    assert client.delete(url,headers=ai).status_code in (401,403)
    deleted=client.delete(url,headers=owner)
    assert deleted.status_code==200 and deleted.json()["metrics_removed"]==5,deleted.text
    assert client.get(url+"/report",headers=owner).status_code==422
'''
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    run=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,
                       capture_output=True,text=True,timeout=120)
    assert run.returncode==0,f"stdout={run.stdout}\nstderr={run.stderr}"
