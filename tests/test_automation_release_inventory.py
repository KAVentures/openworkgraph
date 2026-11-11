"""End-to-end release inventory for customer-run automations (R18)."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_monitoring as monitor
from server import automation_release_inventory as releases


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DATA_DIR", tmp_path)


SPEC = {"format": monitor.SPEC_FORMAT, "to_be": {"steps": [
    {"id": "step_001", "activity": "Receive"},
    {"id": "step_002", "activity": "Review"},
]}}
SHA = monitor.spec_digest(SPEC)
ARTIFACT = "a" * 64


def deployment(*, pinned=True, design=False):
    report = {
        "format": monitor.FORMAT,
        "deployment": {"name": "Refund review", "runtime": "ai-sdk",
                       **({"workflow_spec_sha256": SHA} if pinned else {})},
        "runs": [{
            "run_id": "run1", "started_at": "2026-01-01T01:00:00Z",
            "finished_at": "2026-01-01T01:02:00Z", "status": "succeeded",
            "steps": [{"step_id": "step_001", "activity": "Receive", "status": "completed",
                       "at": "2026-01-01T01:01:00Z"}],
        }],
    }
    ident = monitor.import_report(report)["deployment"]["deployment_id"]
    if design:
        monitor.attach_design(ident, SPEC)
    return ident


def review(ident, stage="planned", **overrides):
    current = releases.detail(ident)
    return {
        "expected_revision": current["revision"],
        "expected_reported_spec_sha256": current["expected_reported_spec_sha256"],
        "expected_design_sha256": current["expected_design_sha256"],
        "stage": stage,
        "release_label": "v1.0.0",
        "artifact_sha256": None,
        "reviewer_note": "Human declaration; no remote write",
        "user_confirmed": True,
        **overrides,
    }


def test_release_history_pins_metadata_without_remote_execution():
    ident = deployment(pinned=True, design=True)
    empty = releases.detail(ident)
    assert empty["revision"] == 0 and empty["current"] is None
    assert empty["runs"] == {"retained": 1, "declaring_any_spec": 1, "declaring_pinned_spec": 1}
    first = releases.record(ident, review(ident))
    assert first["revision"] == 1 and first["current"]["stage"] == "planned"
    second = releases.record(ident, review(ident, "pilot_reported", artifact_sha256=ARTIFACT))
    assert second["revision"] == 2
    assert second["current"]["reported_spec_sha256"] == SHA
    assert second["current"]["design_sha256"] == SHA
    assert second["history"][1]["stage"] == "planned"
    assert all(second[k] is False for k in (
        "remote_deployment_state_verified", "executable_artifact_verified",
        "business_outcome_verified", "remote_business_actions_triggered",
        "rollback_or_remote_pause_performed"))
    production = releases.record(ident, review(ident, "production_reported", artifact_sha256=ARTIFACT))
    assert production["revision"] == 3
    paused = releases.record(ident, review(ident, "paused_reported"))
    assert paused["revision"] == 4 and paused["current"]["stage"] == "paused_reported"
    # A pause here does not contact a remote runner or change its retained runs.
    assert monitor.list_deployments()["deployments"][0]["runs"] == 1


def test_pilot_requires_pinned_report_reviewed_design_and_artifact():
    plain = deployment(pinned=False)
    with pytest.raises(releases.ReleaseReviewError, match="both a reported spec and attached reviewed design"):
        releases.record(plain, review(plain, "pilot_reported", artifact_sha256=ARTIFACT))
    assert releases.detail(plain)["revision"] == 0
    pinned = deployment(pinned=True, design=False)
    with pytest.raises(releases.ReleaseReviewError, match="both a reported spec and attached reviewed design"):
        releases.record(pinned, review(pinned, "production_reported", artifact_sha256=ARTIFACT))
    monitor.attach_design(pinned, SPEC)
    with pytest.raises(releases.ReleaseReviewError, match="artifact SHA-256"):
        releases.record(pinned, review(pinned, "pilot_reported"))
    assert releases.detail(pinned)["revision"] == 0


def test_stale_revision_and_spec_references_are_rejected_without_mutation():
    ident = deployment(pinned=True, design=False)
    original = review(ident)
    # Design was attached after the client reviewed the prior metadata.
    monitor.attach_design(ident, SPEC)
    with pytest.raises(releases.ReleaseReviewError, match="design changed"):
        releases.record(ident, original)
    valid = review(ident)
    releases.record(ident, valid)
    with pytest.raises(releases.ReleaseReviewError, match="inventory changed"):
        releases.record(ident, valid)
    assert releases.detail(ident)["revision"] == 1


@pytest.mark.parametrize("change", [
    {"user_confirmed": False},
    {"stage": "not-a-stage"},
    {"release_label": "../bad"},
    {"artifact_sha256": "bad"},
    {"expected_revision": True},
    {"expected_reported_spec_sha256": "x" * 64},
    {"reviewer_note": "has\\nnewline"},
    {"unexpected": True},
])
def test_bad_or_unconfirmed_reviews_are_not_saved(change):
    ident = deployment()
    with pytest.raises(releases.ReleaseReviewError):
        releases.record(ident, review(ident, **change))
    assert releases.detail(ident)["revision"] == 0


def test_retirement_is_terminal_and_deletion_removes_all_history():
    ident = deployment()
    releases.record(ident, review(ident, "retired_reported"))
    with pytest.raises(releases.ReleaseReviewError, match="retired deployment"):
        releases.record(ident, review(ident, "planned"))
    assert monitor.delete_deployment(ident) == {"deleted": ident}
    with pytest.raises(monitor.MonitoringError, match="unknown deployment"):
        releases.detail(ident)
    conn = monitor._connect()
    try:
        assert conn.execute("SELECT count(*) FROM deployment_release_reviews WHERE deployment_id=?", [ident]).fetchone()[0] == 0
    finally:
        conn.close()


def test_history_capacity_fails_closed():
    ident = deployment()
    for n in range(releases.MAX_REVISIONS):
        result = releases.record(ident, review(ident, release_label=f"v{n}"))
    assert result["revision"] == releases.MAX_REVISIONS
    assert len(result["history"]) == releases.MAX_REVISIONS
    with pytest.raises(releases.ReleaseReviewError, match="history is full"):
        releases.record(ident, review(ident, release_label="overflow"))
    assert releases.detail(ident)["revision"] == releases.MAX_REVISIONS


def test_http_routes_are_dashboard_only_and_reject_duplicate_keys(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import automation_monitoring as monitoring
from server import automation_release_inventory as releases
from test_automation_release_inventory import deployment, review
with TestClient(app) as client:
    dashboard={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    ident=deployment()
    path="/v1/automation-monitoring/deployments/"+ident+"/release"
    assert client.get(path,headers=bearer).status_code==403
    assert client.post(path,headers=bearer,json=review(ident)).status_code==403
    r=client.get(path,headers=dashboard)
    assert r.status_code==200 and r.headers["cache-control"]=="no-store"
    assert client.post(path,headers=dashboard,data='{}').status_code==415
    assert client.post(path,headers={**dashboard,"Content-Type":"application/json"},
      content='{"stage":"planned","stage":"shadow_reported"}').status_code==422
    write=client.post(path,headers=dashboard,json=review(ident))
    assert write.status_code==200,write.text
    assert write.json()["revision"]==1
    assert client.post(path,headers=dashboard,json=review(ident,expected_revision=0)).status_code==409
    assert client.get(path,headers=dashboard).json()["history"][0]["stage"]=="planned"
    assert client.post("/v1/automation-monitoring/deployments/"+ident+"/delete",headers=dashboard).status_code==200
    assert client.get(path,headers=dashboard).status_code==422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + "\\n" + result.stderr
