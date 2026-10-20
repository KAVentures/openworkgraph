"""Explicit read-only source recheck does not equate absent invoice with payment."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_saved_case_recheck_fails_closed_without_claiming_completion(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
import server.secure_app
from server.secure_app import app
from server.local_auth import create_dashboard_session, ensure_api_token
from server import local_source_routes as routes
from server import open_work_reviews as reviews

ref = "owg:w:" + "a"*32
reviews.save_review_intention(
    ref, "assess_follow_up", provider_candidate_currently_open=True)
c = TestClient(app)
owner = {"Authorization": "OWG-Session " + create_dashboard_session()}
bearer = {"Authorization": "Bearer " + ensure_api_token()}
calls = []

def source(**kwargs):
    calls.append(kwargs)
    return {"availability": "source_reported",
            "verified_at": "2026-10-20T09:00:00+00:00",
            "source_coverage_complete": True,
            "candidates": [{"work_item_ref": ref, "source_status": "open"}]}
routes.stripe_open_work_preview = source
url = "/v1/open-work/reviews/recheck"

assert c.post(url, headers=bearer, json={"confirm_scan":True}).status_code == 403
assert c.post(url, headers=owner, json={"confirm_scan":False}).status_code == 403
assert calls == []

same = c.post(url, headers=owner, json={"confirm_scan": True})
assert same.status_code == 200, same.text
assert same.json()["status_by_work_item"][ref] == "source_reported_open"
assert same.json()["business_objective_verified"] is False
assert same.json()["agent_caused_outcome_verified"] is False
assert len(calls) == 1
assert calls[0]["max_pages"] == 3

routes.stripe_open_work_preview = lambda **kwargs: {
    "availability": "source_reported", "verified_at": "2026-10-20T09:10:00+00:00",
    "source_coverage_complete": True, "candidates": []}
missing = c.post(url, headers=owner, json={"confirm_scan": True})
assert missing.status_code == 200, missing.text
assert missing.json()["status_by_work_item"][ref] == (
    "not_in_complete_open_inventory_outcome_unknown")
assert missing.json()["source_outcome_verified"] is False

routes.stripe_open_work_preview = lambda **kwargs: {
    "availability": "source_reported", "source_coverage_complete": False,
    "candidates": []}
partial = c.post(url, headers=owner, json={"confirm_scan": True})
assert partial.json()["status_by_work_item"][ref] == "unknown_incomplete_inventory"

routes.stripe_open_work_preview = lambda **kwargs: {"availability": "unknown"}
outage = c.post(url, headers=owner, json={"confirm_scan": True})
assert outage.json()["availability"] == "unknown"
assert outage.json()["status_by_work_item"][ref] == "unknown_source_unavailable"

assert len(c.get("/v1/open-work/reviews", headers=owner).json()["items"]) == 1
assert c.delete("/v1/open-work/reviews/" + ref, headers=owner).json()["removed"]
routes.stripe_open_work_preview = lambda **kwargs: (_ for _ in ()).throw(
    AssertionError("empty queue must not scan provider"))
empty = c.post(url, headers=owner, json={"confirm_scan": True})
assert empty.status_code == 200
assert empty.json()["provider_read_performed"] is False
'''
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True,
        timeout=90 if os.name == "nt" else 60,
    )
    assert result.returncode == 0, result.stderr[-3000:]


def test_explicit_recheck_never_automatically_runs_in_dashboard():
    script = (Path(__file__).resolve().parents[1] / "dashboard" / "basic_mode.js").read_text()
    assert "pvOpenWorkReviewRecheck" in script
    assert "recheckReviews.onclick" in script
    assert "send('POST', {confirm_scan: true})" in script
    assert "no longer in open inventory; outcome unverified" in script
