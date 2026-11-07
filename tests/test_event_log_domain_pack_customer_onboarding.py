"""Customer onboarding pack end-to-end through the real saved event-log workspace (#370 R23)."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace


PACK = "customer_onboarding"
MAPPING = {
    "object_types": {"onboarding_case": "case"},
    "activities": {
        "case_opened": "Opened", "handoff_received": "Handoff",
        "requirements_verified": "Prerequisites checked",
        "access_provisioned": "Access provisioned", "activation_confirmed": "Activated",
        "rework_requested": "Rework requested", "case_closed": "Closed",
    },
}
CSV = """case,activity,time
C1,Opened,2026-01-01T00:00:00Z
C1,Handoff,2026-01-01T00:30:00Z
C1,Prerequisites checked,2026-01-01T01:00:00Z
C1,Access provisioned,2026-01-01T03:00:00Z
C1,Activated,2026-01-02T01:00:00Z
C1,Closed,2026-01-02T03:00:00Z
C2,Opened,2026-01-01T00:00:00Z
C2,Access provisioned,2026-01-04T00:00:00Z
C2,Closed,2026-01-05T00:00:00Z
C2,Activated,2026-01-20T00:00:00Z
C2,Activated,2026-01-21T00:00:00Z
C3,Opened,2026-01-01T00:00:00Z
C3,Rework requested,2026-01-01T08:00:00Z
C3,Access provisioned,2026-01-02T00:00:00Z
"""


@pytest.fixture(autouse=True)
def local_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def imported():
    return workspace.import_log(
        BytesIO(CSV.encode()), name="Synthetic onboarding", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"},
    )["log_id"]


def rule(report, template, a, b=None):
    return next(
        r for section in report["compliance"] for r in section["results"]
        if r["rule"]["template"] == template and r["rule"]["a"] == a
        and r["rule"].get("b") == b
    )


def test_catalog_install_and_real_report_on_synthetic_selected_cases():
    entry = next(x for x in packs.catalog()["packs"] if x["pack_id"] == PACK)
    assert entry["title"] == "Customer onboarding and activation"
    assert entry["latest_version"] == "1.0.0"
    assert entry["definitions"] == {
        "metrics": 4, "compliance_rules": 6, "object_rules": 0, "reference_model_types": 1,
    }
    log = imported()
    installed = packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING})
    assert installed["not_applicable"] == [] and len(installed["metric_ids"]) == 4
    report = packs.report(log, PACK)
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics["onboarding_open_to_provision"]["median"] == 24 * 3600
    assert metrics["onboarding_provision_to_activation"]["coverage"]["cases_without_to_activity"] == 1
    assert metrics["onboarding_rework_events"]["mean"] == pytest.approx(1 / 3)
    assert rule(report, "response_within", "Opened", "Access provisioned")["violating_cases"] == 1
    assert rule(report, "response_within", "Access provisioned", "Activated")["violating_cases"] == 2
    assert rule(report, "precedence", "Activated", "Closed")["violating_cases"] == 1
    repeats = rule(report, "existence", "Activated")
    assert repeats["rule"]["min"] == 0 and repeats["rule"]["max"] == 1
    assert repeats["violating_case_examples"] == [{"case_id": "C2", "detail": "2 occurrences"}]
    assert any("not employee effort" in s for s in report["assumptions"])
    assert any("not validated contractual" in s for s in report["assumptions"])


def test_missing_optional_roles_do_not_silently_apply_checks():
    log = imported()
    only_required = {"object_types": MAPPING["object_types"],
                     "activities": {k: MAPPING["activities"][k]
                                    for k in ("case_opened", "access_provisioned", "activation_confirmed")}}
    installed = packs.install(
        log, PACK, {"version": "1.0.0", "mapping": only_required,
                    "parameters": {"provision_hours": 96, "activation_days": 30}},
    )
    assert len(installed["metric_ids"]) == 3
    assert {x["unmapped_role"] for x in installed["not_applicable"]} == {
        "requirements_verified", "rework_requested", "handoff_received", "case_closed",
    }
    report = packs.report(log, PACK)
    assert report["parameters"] == {"provision_hours": 96.0, "activation_days": 30.0}
    assert rule(report, "response_within", "Opened", "Access provisioned")["violating_cases"] == 0
    # A missing activation still appears as a response-rule deviation, not a verified SLA breach.
    assert rule(report, "response_within", "Access provisioned", "Activated")["violating_cases"] == 1
    assert all(r["rule"]["template"] != "precedence" or r["rule"]["b"] != "Closed"
               for group in report["compliance"] for r in group["results"])
    assert report["object_rules"] is None


def test_reconfigure_and_uninstall_preserve_only_pack_owned_metrics():
    log = imported()
    first = packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING})
    second = packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING,
                                       "parameters": {"provision_hours": 72}})
    assert second["change"] == "reconfigured"
    assert second["history"][0]["definitions_sha256"] == first["definitions_sha256"]
    assert second["definitions_sha256"] != first["definitions_sha256"]
    assert packs.installed(log)["installs"][0]["pack_id"] == PACK
    assert packs.uninstall(log, PACK)["metrics_removed"] == 4
    assert not packs.installed(log)["installs"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="install this domain pack"):
        packs.report(log, PACK)


def test_bad_mapping_and_target_fail_closed():
    log = imported()
    with pytest.raises(workspace.EventLogWorkspaceError, match="map the required role"):
        packs.install(log, PACK, {"version": "1.0.0",
                                  "mapping": {"object_types": {"onboarding_case": "case"},
                                              "activities": {"case_opened": "Opened"}}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="is not an activity"):
        packs.install(log, PACK, {"version": "1.0.0",
                                  "mapping": {"object_types": {"onboarding_case": "case"},
                                              "activities": {**MAPPING["activities"],
                                                             "activation_confirmed": "Not in log"}}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="from 0.25 to 8760"):
        packs.install(log, PACK, {"version": "1.0.0", "mapping": MAPPING,
                                  "parameters": {"provision_hours": 0.1}})


def test_customer_onboarding_through_authenticated_local_http_routes(tmp_path):
    """Exercise the real import/catalog/install/report/delete journey and permission boundary."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    code = r'''
import sys
sys.path.insert(0, "tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_customer_onboarding import CSV, MAPPING
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/event-logs/import?format=csv&name=onboarding&case_column=case&activity_column=activity&timestamp_column=time"
    assert client.post(path, content=CSV.encode(), headers=owner).status_code == 403
    create = client.post(path, content=CSV.encode(), headers={**owner, "x-owg-local-storage-confirmed": "true"})
    assert create.status_code == 200, create.text
    log = "/v1/event-logs/" + create.json()["log_id"]
    catalog = client.get("/v1/event-log-domain-packs", headers=owner)
    assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
    assert any(p["pack_id"] == "customer_onboarding" for p in catalog.json()["packs"])
    assert client.post(log + "/domain-packs/customer_onboarding",
                       json={"version":"1.0.0","mapping":MAPPING}, headers=bearer).status_code in (401, 403)
    install = client.post(log + "/domain-packs/customer_onboarding",
                          json={"version":"1.0.0","mapping":MAPPING}, headers=owner)
    assert install.status_code == 200, install.text
    report = client.get(log + "/domain-packs/customer_onboarding/report", headers=owner)
    assert report.status_code == 200 and report.headers["cache-control"] == "no-store", report.text
    assert len(report.json()["metrics"]) == 4
    assert report.json()["source_system_validated"] is False
    assert client.get(log + "/domain-packs/customer_onboarding/report", headers=bearer).status_code in (401, 403)
    assert client.delete(log + "/domain-packs/customer_onboarding", headers=owner).json()["metrics_removed"] == 4
    assert client.get(log + "/domain-packs/customer_onboarding/report", headers=owner).status_code == 422
'''
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "local_data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "local_auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    run = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"
