"""Authenticated HTTP regression for the claims domain pack.

Runs the actual local FastAPI entry point in an isolated process. A normal API
token must never gain dashboard-only source import / pack administration rights.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_claims_local_dashboard_http_install_report_uninstall(tmp_path):
    code = r"""
import sys
sys.path.insert(0, "tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_insurance_claims import CSV, MAPPING

with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    api = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    import_url = ("/v1/event-logs/import?format=csv&name=claims"
                  "&case_column=case&activity_column=activity&timestamp_column=time")

    refused = client.post(import_url, content=CSV.encode(), headers=api)
    assert refused.status_code in (401, 403), refused.text
    created = client.post(import_url, content=CSV.encode(), headers={
        **owner, "x-owg-local-storage-confirmed": "true"})
    assert created.status_code == 200, created.text
    log_id = created.json()["log_id"]

    catalog = client.get("/v1/event-log-domain-packs", headers=owner)
    assert catalog.status_code == 200, catalog.text
    assert any(p["pack_id"] == "insurance_claims" for p in catalog.json()["packs"])

    url = "/v1/event-logs/" + log_id + "/domain-packs/insurance_claims"
    denied_install = client.post(
        url, json={"version": "1.0.0", "mapping": MAPPING}, headers=api)
    assert denied_install.status_code in (401, 403), denied_install.text

    installed = client.post(
        url, json={"version": "1.0.0", "mapping": MAPPING}, headers=owner)
    assert installed.status_code == 200, installed.text
    assert len(installed.json()["metric_ids"]) == 6

    report = client.get(url + "/report", headers=owner)
    assert report.status_code == 200, report.text
    body = report.json()
    assert report.headers["cache-control"] == "no-store"
    assert len(body["metrics"]) == 6
    assert body["source_system_validated"] is False
    assert body["policy_compliance_verified"] is False
    assert body["execution_authorized"] is False
    assert body["pack"]["definitions_sha256"] == installed.json()["definitions_sha256"]

    denied_report = client.get(url + "/report", headers=api)
    assert denied_report.status_code in (401, 403), denied_report.text
    denied_uninstall = client.delete(url, headers=api)
    assert denied_uninstall.status_code in (401, 403), denied_uninstall.text

    deleted = client.delete(url, headers=owner)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["metrics_removed"] == 6
    assert client.get(url + "/report", headers=owner).status_code == 422
"""
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"
