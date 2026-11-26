"""R18: local cross-deployment release evidence with exact run attribution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_deployment_portfolio as portfolio
from server import automation_monitoring as monitor
from server import automation_release_inventory as releases
from test_automation_release_inventory import deployment, review, SHA, ARTIFACT


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DATA_DIR", tmp_path)


def test_empty_inventory_and_window_validation():
    result = portfolio.portfolio()
    assert result["deployment_count"] == 0
    assert result["deployments"] == []
    assert result["remote_deployment_state_verified"] is False
    assert result["execution_authorized"] is False
    for invalid in (0, 366, True, 1.5, "30"):
        with pytest.raises(monitor.MonitoringError):
            portfolio.portfolio(recent_days=invalid)


def test_exact_partitions_and_recent_counts():
    ident = deployment(pinned=True, design=True)
    releases.record(ident, review(ident, "production_reported", artifact_sha256=ARTIFACT))
    other = deployment(pinned=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with monitor._LOCK:
        conn = monitor._connect()
        try:
            # These synthetic rows exercise legacy unknown hashes, historical
            # claims for another version and out-of-window timestamps.
            conn.execute("UPDATE runs SET started_at=? WHERE deployment_id=?",
                         [now - timedelta(days=60), ident])
            for rid, stamp, digest in (
                ("same", now-timedelta(days=1), SHA),
                ("other", now-timedelta(days=2), "b"*64),
                ("unknown", now-timedelta(days=3), None),
                ("future", now+timedelta(days=1), SHA),
            ):
                conn.execute("""INSERT INTO runs
                    (deployment_id,run_id,started_at,finished_at,status,
                     outcome_reported_verified,outcome_source,reported_spec_sha256)
                    VALUES (?,?,?,NULL,'running',NULL,'',?)""",
                    [ident, rid, stamp, digest])
        finally:
            conn.close()
    result = portfolio.portfolio(recent_days=7)
    by_id = {r["deployment_id"]: r for r in result["deployments"]}
    row = by_id[ident]
    assert row["current_declaration"]["stage"] == "production_reported"
    assert row["declaration_pins_match_current_metadata"] is True
    assert row["runs"] == {"retained": 5, "claiming_current_spec": 3,
                           "claiming_other_spec": 1, "without_comparable_spec": 1}
    assert row["recent_runs"] == {"retained": 3, "claiming_current_spec": 1,
                                  "claiming_other_spec": 1, "without_comparable_spec": 1}
    assert "recent_runs_claiming_other_spec" in row["review_flags"]
    assert "recent_runs_without_comparable_spec" in row["review_flags"]
    assert by_id[other]["runs"]["without_comparable_spec"] == 1
    assert "no_release_declaration" in by_id[other]["review_flags"]
    assert result["declaration_stage_counts"] == {"production_reported": 1, "undeclared": 1}
    assert result["deployments_with_review_flags"] == 2
    assert "reviewer_note" not in row["current_declaration"]
    assert "run_id" not in row["runs"]
    assert result["executable_artifact_verified"] is False


def test_declaration_drift_and_deletion_reflected_without_mutations():
    ident = deployment(pinned=True, design=False)
    releases.record(ident, review(ident))
    assert "release_pins_missing_or_changed" in portfolio.portfolio()["deployments"][0]["review_flags"]
    from test_automation_release_inventory import SPEC
    monitor.attach_design(ident, SPEC)
    row = portfolio.portfolio()["deployments"][0]
    assert not row["declaration_pins_match_current_metadata"]
    assert row["current_declaration"]["design_sha256"] is None
    assert row["design_sha256"] == SHA
    assert releases.detail(ident)["revision"] == 1
    monitor.delete_deployment(ident)
    assert portfolio.portfolio()["deployment_count"] == 0


def test_dashboard_only_http(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = r"""
import sys
sys.path.insert(0,"tests")
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_automation_release_inventory import deployment
with TestClient(app) as client:
    owner = {"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer = {"Authorization":"Bearer "+local_auth.ensure_api_token()}
    ident = deployment()
    url = "/v1/automation-monitoring/portfolio"
    assert client.get(url,headers=bearer).status_code == 403
    response = client.get(url+"?recent_days=7",headers=owner)
    assert response.status_code == 200,response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["deployments"][0]["deployment_id"] == ident
    assert response.json()["recent_window"]["days"] == 7
    assert client.get(url+"?recent_days=0",headers=owner).status_code == 422
    assert client.post("/v1/automation-monitoring/deployments/"+ident+"/delete",headers=owner).status_code == 200
    assert client.get(url,headers=owner).json()["deployment_count"] == 0
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
