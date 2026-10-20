"""Local human-reviewed Open Work queue: explicit save, TTL, isolation and revoke."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess
import sys

import pytest

from server import open_work_reviews as review

REF = "owg:w:" + "a" * 32
NOW = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


@pytest.fixture
def local_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    monkeypatch.setattr(review, "read_policy",
                        lambda: {"human_retention": {"mode": "forever"}})
    return tmp_path


def test_no_queue_by_default_and_only_explicit_source_confirmed_save(local_auth):
    assert review.list_review_intentions(now=NOW)["items"] == []
    with pytest.raises(ValueError):
        review.save_review_intention(REF, "assess_follow_up",
            provider_candidate_currently_open=False, now=NOW)
    assert not (local_auth / ".open_work_review_intentions.json").exists()
    item = review.save_review_intention(REF, "assess_follow_up",
        provider_candidate_currently_open=True, now=NOW)
    assert item["agent_execution_authorized"] is False
    assert item["business_obligation_verified"] is False
    assert item["current_source_state"] == "unknown_until_explicit_recheck"
    queue = review.list_review_intentions(now=NOW + timedelta(days=1))
    assert len(queue["items"]) == 1
    assert queue["source_read_performed"] is False
    assert queue["not_synced"] is True
    if os.name == "posix":
        assert oct((local_auth / ".open_work_review_intentions.json").stat().st_mode & 0o777) == "0o600"
    assert REF in str(queue)
    for private in ("in_1234567890ABCDEF", "acct_1234567890ABCDEF", "email", "amount_due"):
        assert private not in (local_auth / ".open_work_review_intentions.json").read_text()


def test_expiry_is_strict_without_renewal(local_auth):
    review.save_review_intention(REF, "verify_payment_status",
        provider_candidate_currently_open=True, now=NOW)
    assert review.list_review_intentions(now=NOW + timedelta(days=7, seconds=1))["items"] == []
    assert review.list_review_intentions(now=NOW + timedelta(days=8))["items"] == []


@pytest.mark.parametrize("invalid", [
    ("in_1234567890ABCDEF", "assess_follow_up"),
    ("owg:w:" + "A" * 32, "assess_follow_up"),
    (REF, "send_customer_email"),
    (REF, "automate_refunds"),
])
def test_invalid_reviews_never_saved(local_auth, invalid):
    with pytest.raises(ValueError):
        review.save_review_intention(invalid[0], invalid[1],
            provider_candidate_currently_open=True, now=NOW)
    assert review.list_review_intentions(now=NOW)["items"] == []


def test_history_retention_is_upper_bound_not_ignored(local_auth, monkeypatch):
    saved = review.save_review_intention(
        REF, "assess_follow_up", provider_candidate_currently_open=True, now=NOW)
    assert saved["expires_at"].startswith("2026-10-27")
    monkeypatch.setattr(review, "read_policy",
                        lambda: {"human_retention": {"mode": "days", "days": 1}})
    assert review.list_review_intentions(now=NOW + timedelta(days=2))["items"] == []


def test_ephemeral_history_forbids_durable_reviews_and_scrubs_previous(local_auth, monkeypatch):
    review.save_review_intention(REF, "assess_follow_up",
        provider_candidate_currently_open=True, now=NOW)
    file = local_auth / ".open_work_review_intentions.json"
    assert file.exists()
    monkeypatch.setattr(review, "read_policy",
                        lambda: {"human_retention": {"mode": "ephemeral"}})
    assert review.list_review_intentions(now=NOW)["items"] == []
    assert not file.exists()
    with pytest.raises(ValueError, match="session-only"):
        review.save_review_intention(REF, "assess_follow_up",
            provider_candidate_currently_open=True, now=NOW)


def test_delete_or_disconnect_removes_only_review_intentions(local_auth):
    review.save_review_intention(REF, "assess_follow_up",
        provider_candidate_currently_open=True, now=NOW)
    assert review.remove_review_intention(REF, now=NOW)
    assert not review.remove_review_intention(REF, now=NOW)
    review.save_review_intention(REF, "assess_follow_up",
        provider_candidate_currently_open=True, now=NOW)
    unrelated = local_auth / ".other-private-owg-setting"
    unrelated.write_text("untouched")
    review.clear_review_intentions()
    assert review.list_review_intentions(now=NOW)["items"] == []
    assert unrelated.read_text() == "untouched"


def test_untrusted_permission_or_symlink_fails_closed(local_auth):
    path = local_auth / ".open_work_review_intentions.json"
    path.write_text('{"version":1,"items":[]}')
    if os.name == "posix":
        path.chmod(0o644)
        assert review.list_review_intentions(now=NOW)["items"] == []
    path.unlink()
    other = local_auth / "other.json"
    other.write_text("safe")
    try:
        path.symlink_to(other)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported")
    with pytest.raises(OSError):
        review.save_review_intention(REF, "assess_follow_up",
            provider_candidate_currently_open=True, now=NOW)
    assert other.read_text() == "safe"


def test_authenticated_local_routes_validate_against_current_provider(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
import server.secure_app
from server.secure_app import app
from server.local_auth import create_dashboard_session, ensure_api_token
from server import local_source_routes as routes

ref = "owg:w:" + "a"*32
calls = []
def scanner(**kwargs):
    calls.append(kwargs)
    return {"availability": "source_reported", "candidates": [
        {"work_item_ref":ref,"source_status":"open"}
    ], "source_coverage_complete":True}
routes.stripe_open_work_preview = scanner
routes.stripe_source_status = lambda: {"configured": True}
client = TestClient(app)
url = "/v1/open-work/reviews"
bearer = {"Authorization": "Bearer " + ensure_api_token()}
owner = {"Authorization": "OWG-Session " + create_dashboard_session()}
for action in ["get", "post"]:
    request = getattr(client, action)(url, headers=bearer, **(
        {"json":{"work_item_ref":ref,"review_intent":"assess_follow_up",
                 "confirm_scan":True,"confirm_save":True}}
        if action == "post" else {}))
    assert request.status_code == 403, request.text
assert calls == []

unconfirmed = client.post(url, headers=owner, json={
    "work_item_ref":ref,"review_intent":"assess_follow_up",
    "confirm_scan":True,"confirm_save":False})
assert unconfirmed.status_code == 403 and calls == []

saved = client.post(url, headers=owner, json={
    "work_item_ref":ref,"review_intent":"assess_follow_up",
    "confirm_scan":True,"confirm_save":True})
assert saved.status_code == 200, saved.text
assert saved.json()["work_item_ref"] == ref
assert len(calls) == 1 and calls[0]["explicit_scan_authorized"] is True

listed = client.get(url, headers=owner)
assert listed.status_code == 200 and len(listed.json()["items"]) == 1
assert listed.json()["source_read_performed"] is False

routes.stripe_open_work_preview = lambda **kw: {
    "availability": "source_reported", "candidates":[],
    "source_coverage_complete":True}
rejected = client.post(url, headers=owner, json={
    "work_item_ref":"owg:w:" + "b"*32,"review_intent":"assess_follow_up",
    "confirm_scan":True,"confirm_save":True})
assert rejected.status_code == 409
assert len(client.get(url, headers=owner).json()["items"]) == 1

removed = client.delete(url + "/" + ref, headers=owner)
assert removed.status_code == 200 and removed.json()["removed"] is True
assert client.get(url, headers=owner).json()["items"] == []
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
        text=True, cwd=Path(__file__).resolve().parents[1], env=env,
        timeout=80 if os.name == "nt" else 50)
    assert r.returncode == 0, r.stderr[-3000:]


def test_history_policy_session_only_change_clears_queue_immediately(tmp_path):
    script = r"""
from datetime import datetime, timezone
import server.history_routes as history
from server import open_work_reviews as reviews
from server.history_routes import RetentionChoice

reviews.read_policy = lambda: {"human_retention": {"mode": "forever"}}
ref = "owg:w:" + "a"*32
reviews.save_review_intention(ref, "assess_follow_up",
    provider_candidate_currently_open=True)
path = reviews._path()
assert path.exists()
history.update_retention = lambda **kw: {
    "human_retention": {"mode": "ephemeral"},
    "agent_retention": {"mode": "ephemeral"},
    "onboarding_complete": True}
history.cleanup_expired_history = lambda **kw: {}
result = history.set_history_policy(RetentionChoice(
    human_mode="ephemeral", agent_mode="ephemeral",
    onboarding_complete=True))
assert result["status"] == "saved"
assert not path.exists()
"""
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_AUTH_DIR"] = str(tmp_path / "auth")
    env["WORKFLOW_OBSERVER_DATA"] = str(tmp_path / "data")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], env=env,
        timeout=80 if os.name == "nt" else 50,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
