"""Discovering candidate Declare-style rules from a saved event log."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_compliance as compliance
from server import event_log_rule_discovery as discovery
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 3, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _cases():
    """40 purchase cases: always Create→Approve→Pay, Receive in most; 2 pay before approval; Cancel excludes Pay."""
    cases = {}
    for i in range(40):
        steps = ["Create PO", "Approve PO", "Receive", "Pay"]
        if i % 10 == 9:
            steps = ["Create PO", "Approve PO", "Cancel"]
        elif i in (3, 17):
            steps = ["Create PO", "Pay", "Approve PO", "Receive"]
        elif i % 4 == 1:
            steps = ["Create PO", "Approve PO", "Pay"]
        cases[f"c{i:02d}"] = steps
    return cases


def _log(cases=None):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time"])
    for n, (case, steps) in enumerate((cases or _cases()).items()):
        for k, activity in enumerate(steps):
            writer.writerow([case, activity, (START + timedelta(days=n, hours=k)).isoformat()])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="p2p", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def _by_description(result):
    return {c["description"]: c for c in result["candidates"]}


def test_frequent_regularities_are_found_with_support_and_confidence():
    log = _log()
    found = _by_description(discovery.discover(log, {"min_confidence": 0.9}))
    assert found["Create PO occurs in every case"]["confidence"] == 1.0
    assert found["every Pay is preceded by Create PO"]["violating_cases"] == 0
    approve = found["every Pay is preceded by Approve PO"]
    assert approve["violating_cases"] == 2 and approve["activated_cases"] == 36
    assert approve["confidence_ci95"][0] < approve["confidence"] < approve["confidence_ci95"][1]
    assert "Cancel and Pay never occur in the same case" in found
    assert found["Approve PO occurs at most once"]["confidence"] == 1.0
    # Too few cases for a 0.99 bar.
    strict = _by_description(discovery.discover(log, {"min_confidence": 0.99}))
    assert "every Pay is preceded by Approve PO" not in strict


def test_candidates_agree_exactly_with_the_compliance_checker():
    log = _log()
    result = discovery.discover(log, {"min_confidence": 0.9})
    assert result["rule_set"]["rules"] and len(result["rule_set"]["rules"]) <= compliance.MAX_RULES
    checked = compliance.check(log, result["rule_set"])["results"]
    normal = lambda rule: json.dumps(compliance.parse({"rules": [rule]}).rules[0].model_dump(), sort_keys=True)
    by_rule = {normal(c["rule"]): c for c in result["candidates"]}
    for summary in checked:
        candidate = by_rule[json.dumps(summary["rule"], sort_keys=True)]
        assert summary["violating_cases"] == candidate["violating_cases"], summary["description"]
        assert summary["activated_cases"] == candidate["activated_cases"], summary["description"]


def test_settings_templates_limits_and_cohorts():
    log = _log()
    only = discovery.discover(log, {"templates": ["precedence"], "max_candidates": 3})
    assert {c["rule"]["template"] for c in only["candidates"]} == {"precedence"}
    assert len(only["candidates"]) == 3 and only["candidates_truncated"] is True
    capped = discovery.discover(log, {"max_activities": 2})
    assert capped["activities_considered"] == 2 and capped["activities_truncated"] is True
    for bad in ({"min_confidence": 0.2}, {"templates": ["chain_response"]}, {"surprise": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            discovery.discover(log, bad)
    assert "not policy" in only["interpretation"]


def test_route_requires_the_dashboard(tmp_path):
    code = r'''
import csv, io
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-01T0{k}:00:00Z" for i in range(12) for k, a in enumerate(["A", "B", "C"])]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    log_id = imported.json()["log_id"]
    path = f"/v1/event-logs/{log_id}/compliance/discover"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={"min_confidence": 0.9}, headers=owner)
    assert found.status_code == 200, found.text
    assert any(c["description"] == "every B is preceded by A" for c in found.json()["candidates"])
    assert client.post(path, json={"min_confidence": 2}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
