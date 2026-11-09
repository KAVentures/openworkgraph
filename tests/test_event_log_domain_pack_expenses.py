"""Expense pack: hand-specified payable-claim exceptions, no payment execution."""
from io import BytesIO
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace

PACK = "expense_reimbursement"
MAPPING = {"object_types": {"claim": "case"}, "activities": {
    "submitted": "Submitted", "approved": "Approved", "paid": "Paid",
    "receipt_checked": "Receipts checked", "returned": "Returned", "closed": "Closed"}}
CSV = """case,activity,time
normal,Submitted,2026-01-01T00:00:00Z
normal,Receipts checked,2026-01-01T01:00:00Z
normal,Approved,2026-01-02T00:00:00Z
normal,Paid,2026-01-03T00:00:00Z
normal,Closed,2026-01-04T00:00:00Z
late,Submitted,2026-01-01T00:00:00Z
late,Returned,2026-01-02T00:00:00Z
late,Receipts checked,2026-01-03T00:00:00Z
late,Approved,2026-01-10T00:00:00Z
late,Paid,2026-02-01T00:00:00Z
missing,Submitted,2026-01-01T00:00:00Z
missing,Approved,2026-01-02T00:00:00Z
missing,Closed,2026-01-03T00:00:00Z
repeat,Submitted,2026-01-01T00:00:00Z
repeat,Approved,2026-01-02T00:00:00Z
repeat,Paid,2026-01-03T00:00:00Z
repeat,Paid,2026-01-04T00:00:00Z
out_of_order,Submitted,2026-01-01T00:00:00Z
out_of_order,Paid,2026-01-02T00:00:00Z
out_of_order,Approved,2026-01-03T00:00:00Z
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def imported():
    return workspace.import_log(BytesIO(CSV.encode()), name="Synthetic payable expenses", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def install(log_id, mapping=None, parameters=None):
    return packs.install(log_id, PACK, {"version": "1.0.0", "mapping": mapping or MAPPING,
                                       "parameters": parameters or {}})


def rule(report, template, a, b=None):
    return next(r for section in report["compliance"] for r in section["results"]
                if r["rule"]["template"] == template and r["rule"]["a"] == a
                and r["rule"].get("b") == b)


def cases(result):
    return {c["case_id"] for c in result["violating_case_examples"]}


def test_catalog_and_exact_evidence_checks():
    entry = next(p for p in packs.catalog()["packs"] if p["pack_id"] == PACK)
    assert entry["definitions"] == {"metrics": 5, "compliance_rules": 8,
                                     "object_rules": 0, "reference_model_types": 1}
    log_id = imported()
    saved = install(log_id)
    report = packs.report(log_id, PACK)
    for flag in ("source_system_validated", "policy_compliance_verified", "verified_business_workflow", "execution_authorized"):
        assert report[flag] is False
    assert report["pack"]["definitions_sha256"] == saved["definitions_sha256"]
    assert cases(rule(report, "existence", "Paid")) == {"missing", "repeat"}
    assert cases(rule(report, "precedence", "Approved", "Paid")) == {"out_of_order"}
    assert cases(rule(report, "precedence", "Paid", "Closed")) == {"missing"}
    assert cases(rule(report, "response_within", "Submitted", "Approved")) == {"late"}
    assert {"late", "missing"} <= cases(rule(report, "response_within", "Approved", "Paid"))
    assert cases(rule(report, "precedence", "Receipts checked", "Approved")) == {"missing", "repeat", "out_of_order"}
    duties = rule(report, "segregation_of_duties", "Submitted", "Approved")
    assert duties["unknown_cases"] == 5 and duties["satisfied_cases"] == 0
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics["expense_payment_records"]["mean"] == pytest.approx(1)
    assert metrics["expense_return_records"]["mean"] == pytest.approx(0.2)
    assert metrics["expense_approval_to_payment"]["coverage"]["cases_without_to_activity"] == 1


def test_optional_controls_and_reconfiguration_preserve_owned_metrics():
    log_id = imported()
    mapping = copy.deepcopy(MAPPING)
    for role in ("returned", "receipt_checked", "closed"):
        del mapping["activities"][role]
    saved = install(log_id, mapping)
    assert len(saved["metric_ids"]) == 4
    assert {r["unmapped_role"] for r in saved["not_applicable"]} == {"returned", "receipt_checked", "closed"}
    query.save_metric(log_id, {"metric": {"name": "manual_count", "kind": "activity_count", "activity": "Paid"}})
    updated = install(log_id, parameters={"approval_days": 14, "payment_days": 30})
    assert updated["definitions_sha256"] != saved["definitions_sha256"]
    assert updated["history"][0]["definitions_sha256"] == saved["definitions_sha256"]
    report = packs.report(log_id, PACK)
    assert "late" not in cases(rule(report, "response_within", "Approved", "Paid"))
    assert rule(report, "response_within", "Approved", "Paid")["rule"]["seconds"] == 30 * 86400
    assert packs.uninstall(log_id, PACK)["metrics_removed"] == 5
    assert [m["metric"]["name"] for m in query.list_metrics(log_id)["metrics"]] == ["manual_count"]
    install(log_id)
    workspace.delete_log(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log_id, PACK)


@pytest.mark.parametrize("parameters", [{"payment_days": 0}, {"approval_days": 366},
    {"payment_days": float("nan")}, {"payment_days": float("inf")}, {"auto_pay": 1}])
def test_invalid_targets_do_not_install(parameters):
    log_id = imported()
    with pytest.raises(workspace.EventLogWorkspaceError):
        install(log_id, parameters=parameters)
    assert packs.installed(log_id)["installs"] == []


def test_mapping_requires_known_roles_and_labels():
    log_id = imported()
    for role, label in (("approved", None), ("paid", "Not in source")):
        mapping = copy.deepcopy(MAPPING)
        if label is None:
            del mapping["activities"][role]
        else:
            mapping["activities"][role] = label
        with pytest.raises(workspace.EventLogWorkspaceError):
            install(log_id, mapping)
    assert packs.installed(log_id)["installs"] == []


def test_actual_dashboard_http_permissions_and_full_lifecycle(tmp_path):
    code = r'''
import sys
sys.path.insert(0,"tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_expenses import CSV,MAPPING
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    ai={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    import_url=("/v1/event-logs/import?format=csv&name=expenses"
        "&case_column=case&activity_column=activity&timestamp_column=time")
    assert client.post(import_url,content=CSV.encode(),headers=owner).status_code==403
    created=client.post(import_url,content=CSV.encode(),headers={
        **owner,"x-owg-local-storage-confirmed":"true"})
    assert created.status_code==200,created.text
    url="/v1/event-logs/"+created.json()["log_id"]+"/domain-packs/expense_reimbursement"
    catalog=client.get("/v1/event-log-domain-packs",headers=owner)
    assert catalog.status_code==200,catalog.text
    assert any(x["pack_id"]=="expense_reimbursement" for x in catalog.json()["packs"])
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
    run=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).resolve().parents[1],env=env,
                       capture_output=True,text=True,timeout=120)
    assert run.returncode==0,f"stdout={run.stdout}\nstderr={run.stderr}"
