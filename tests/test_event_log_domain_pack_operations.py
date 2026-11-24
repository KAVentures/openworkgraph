"""End-to-end synthetic saved-log coverage for the R23 operations suite."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace


# Independent examples of the intended primary sequences; do not generate
# expected rules from the implementation under test.
SCENARIOS = [
    ("manufacturing_quality", "nonconformance",
     ("reported", "investigated", "disposition", "closed"),
     ("contained", "corrective_action", "verified", "reopened"),
     ("reported", "contained", "investigated", "disposition",
      "corrective_action", "verified", "closed"),
     "investigation_hours", 96),
    ("supplier_qualification", "qualification",
     ("requested", "checked", "decision"),
     ("documents_requested", "documents_received", "activated", "reopened"),
     ("requested", "documents_requested", "documents_received", "checked",
      "decision", "activated"),
     "check_days", 90),
    ("financial_close", "close_task",
     ("assigned", "prepared", "reviewed", "closed"),
     ("adjustment", "reopened"),
     ("assigned", "prepared", "adjustment", "reviewed", "closed"),
     "preparation_hours", 96),
]


def scenario_data(scenario):
    pack, case, required, optional, sequence, *_ = scenario
    mapping = {"object_types": {case: "case"},
               "activities": {role: role for role in required + optional}}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = ["case,activity,time"]

    def add(case_id, steps, gap):
        for i, step in enumerate(steps):
            rows.append(f"{case_id},{step},{(start + gap * i).isoformat()}")

    add("normal", sequence, timedelta(hours=1))
    add("slow", required, timedelta(days=40))
    add("pending", required[:-1], timedelta(hours=1))
    add("repeat", required + (required[-1], "reopened"), timedelta(hours=1))
    add("out_of_order", (required[0], required[2], required[1]) + required[3:],
        timedelta(hours=1))
    return ("\n".join(rows) + "\n").encode(), mapping


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(scenario):
    data, mapping = scenario_data(scenario)
    log = workspace.import_log(
        BytesIO(data), name=scenario[0], source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time"},
    )["log_id"]
    return log, mapping


def _install(log, pack, mapping, parameters=None):
    return packs.install(log, pack, {
        "version": "1.0.0", "mapping": copy.deepcopy(mapping),
        "parameters": parameters or {},
    })


def _rule(report, template, a, b=None):
    return next(row for section in report["compliance"] for row in section["results"]
                if row["rule"]["template"] == template and row["rule"]["a"] == a
                and row["rule"].get("b") == b)


def _cases(rule):
    return {row["case_id"] for row in rule["violating_case_examples"]}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_discover_install_report_and_uninstall(scenario):
    pack, _, required, optional, _, target, new_target = scenario
    catalog = next(p for p in packs.catalog()["packs"] if p["pack_id"] == pack)
    assert catalog["versions"] == ["1.0.0"]
    assert catalog["definitions"]["metrics"] == 6
    assert {r["role"] for r in catalog["activities"] if r["required"]} == set(required)
    assert {r["role"] for r in catalog["activities"] if not r["required"]} == set(optional)
    log, mapping = _log(scenario)
    query.save_metric(log, {"metric": {
        "name": "owner_metric", "kind": "activity_count", "activity": required[0]}})
    saved = _install(log, pack, mapping)
    assert saved["change"] == "installed"
    assert not saved["not_applicable"]
    assert len(saved["metric_ids"]) == 6
    report = packs.report(log, pack)
    assert len(report["metrics"]) == 6
    assert report["object_model"] is not None
    assert report["pack"]["definitions_sha256"] == saved["definitions_sha256"]
    for key in ("source_system_validated", "policy_compliance_verified",
                "execution_authorized", "verified_business_workflow"):
        assert report[key] is False
    assert {"repeat", "pending"} == _cases(_rule(report, "existence", required[-1]))
    assert "slow" in _cases(_rule(report, "response_within", required[0], required[1]))
    assert "out_of_order" in _cases(_rule(report, "precedence", required[1], required[2]))
    assert _cases(_rule(report, "existence", "reopened")) == {"repeat"}
    updated = _install(log, pack, mapping, {target: new_target})
    assert updated["definitions_sha256"] != saved["definitions_sha256"]
    unit = 3600 if target.endswith("_hours") else 86400
    assert _rule(packs.report(log, pack), "response_within",
                 required[0], required[1])["rule"]["seconds"] == new_target * unit
    assert packs.uninstall(log, pack)["metrics_removed"] == 6
    assert [m["metric"]["name"] for m in query.list_metrics(log)["metrics"]] == ["owner_metric"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log, pack)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_optional_coverage_and_invalid_install_is_atomic(scenario):
    pack, _, required, optional, _, target, _ = scenario
    log, mapping = _log(scenario)
    for role in optional:
        mapping["activities"].pop(role)
    saved = _install(log, pack, mapping)
    assert saved["not_applicable"]
    assert {row["unmapped_role"] for row in saved["not_applicable"]} == set(optional)
    assert len(saved["metric_ids"]) < 6
    report = packs.report(log, pack)
    assert report["source_system_validated"] is False
    assert len(report["metrics"]) == len(saved["metric_ids"])
    digest = saved["definitions_sha256"]
    for invalid in (0, 100000, float("nan"), float("inf")):
        with pytest.raises(workspace.EventLogWorkspaceError):
            _install(log, pack, mapping, {target: invalid})
    for value in (None, "not a recorded activity"):
        bad = copy.deepcopy(mapping)
        if value is None:
            bad["activities"].pop(required[0])
        else:
            bad["activities"][required[0]] = value
        with pytest.raises(workspace.EventLogWorkspaceError):
            _install(log, pack, bad)
    assert packs.report(log, pack)["pack"]["definitions_sha256"] == digest


def test_all_operations_packs_use_existing_authenticated_http_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = r"""
import sys
sys.path.insert(0, "tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_operations import SCENARIOS, scenario_data

with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    api = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    catalog = client.get("/v1/event-log-domain-packs", headers=owner)
    assert catalog.status_code == 200, catalog.text
    ids = {p["pack_id"] for p in catalog.json()["packs"]}
    for scenario in SCENARIOS:
        pack = scenario[0]
        assert pack in ids
        data, mapping = scenario_data(scenario)
        import_url = ("/v1/event-logs/import?format=csv&name=" + pack
                      + "&case_column=case&activity_column=activity&timestamp_column=time")
        created = client.post(import_url, content=data, headers={
            **owner, "x-owg-local-storage-confirmed": "true"})
        assert created.status_code == 200, created.text
        log_id = created.json()["log_id"]
        url = "/v1/event-logs/" + log_id + "/domain-packs/" + pack
        spec = {"version": "1.0.0", "mapping": mapping}
        assert client.post(url, json=spec, headers=api).status_code in (401, 403)
        installed = client.post(url, json=spec, headers=owner)
        assert installed.status_code == 200, installed.text
        report = client.get(url + "/report", headers=owner)
        assert report.status_code == 200, report.text
        assert report.headers["cache-control"] == "no-store"
        assert report.json()["execution_authorized"] is False
        assert report.json()["pack"]["definitions_sha256"] == installed.json()["definitions_sha256"]
        assert len(report.json()["metrics"]) == 6
        assert client.get(url + "/report", headers=api).status_code in (401, 403)
        assert client.delete(url, headers=api).status_code in (401, 403)
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
    run = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"
