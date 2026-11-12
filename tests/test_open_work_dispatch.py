"""Owner-offered Open Work hand-off to connected agents."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from server import open_work_dispatch as dispatch
from server import open_work_reviews as reviews

REF = "owg:w:" + "a" * 32
OTHER = "owg:w:" + "b" * 32


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(reviews, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(reviews, "_allowed_days", lambda: 7)
    reviews.save_review_intention(REF, "assess_follow_up", provider_candidate_currently_open=True)
    return tmp_path


def test_dispatch_is_off_by_default_and_only_queued_reviews_can_be_offered():
    assert dispatch.status()["enabled"] is False
    with pytest.raises(dispatch.DispatchError, match="turn on"):
        dispatch.offer(REF)
    dispatch.set_enabled(True)
    with pytest.raises(dispatch.DispatchError, match="review queue"):
        dispatch.offer(OTHER)
    offer = dispatch.offer(REF, owner_note="Check the overdue invoice")
    assert offer["status"] == "offered" and offer["agent_execution_authorized"] is False


def test_claim_lease_is_exclusive_and_report_is_unverified():
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    claim = dispatch.claim(REF, "codex agent")
    assert claim["status"] == "claimed" and len(claim["lease_token"]) >= 24
    with pytest.raises(dispatch.DispatchError, match="already claimed"):
        dispatch.claim(REF, "other agent")
    with pytest.raises(dispatch.DispatchError, match="lease token"):
        dispatch.report(REF, "wrong-token", "attempted")
    reported = dispatch.report(REF, claim["lease_token"], "attempted", note="Sent a reminder draft",
                               operation_receipt_ref="owg:op:" + "c" * 32)
    assert reported["status"] == "reported"
    assert reported["last_report"]["verified"] is False and reported["agent_report_is_unverified"] is True
    with pytest.raises(dispatch.DispatchError, match="awaiting owner"):
        dispatch.claim(REF, "codex agent")
    assert dispatch.acknowledge(REF, reoffer=True)["status"] == "offered"
    assert dispatch.acknowledge(REF, reoffer=False) is None
    assert dispatch.status()["offers"] == []


def test_decline_returns_case_and_release_frees_it():
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    first = dispatch.claim(REF, "agent one")
    assert dispatch.report(REF, first["lease_token"], "declined")["status"] == "offered"
    second = dispatch.claim(REF, "agent two")
    assert dispatch.release(REF, second["lease_token"])["status"] == "offered"


def test_expired_lease_returns_case_to_pool(monkeypatch):
    dispatch.set_enabled(True, lease_minutes=5)
    dispatch.offer(REF)
    dispatch.claim(REF, "slow agent")
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    monkeypatch.setattr(dispatch, "_now", lambda: later)
    offers = dispatch.status()["offers"]
    assert offers[0]["status"] == "offered" and offers[0]["claimed_by"] is None
    assert offers[0]["history"][-1]["event"] == "lease_expired"


def test_offer_ends_with_its_review_and_disabling_withdraws_everything():
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    reviews.remove_review_intention(REF)
    assert dispatch.status()["offers"] == []
    reviews.save_review_intention(REF, "assess_follow_up", provider_candidate_currently_open=True)
    dispatch.offer(REF)
    dispatch.set_enabled(False)
    assert dispatch.status()["offers"] == []


@pytest.mark.parametrize("kwargs", [{"owner_note": "x" * 281}, {"owner_note": "bad\x00"}, {"days": 0}, {"days": 8}])
def test_offer_input_is_bounded(kwargs):
    dispatch.set_enabled(True)
    with pytest.raises(dispatch.DispatchError):
        dispatch.offer(REF, **kwargs)


def test_report_and_agent_inputs_are_validated():
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    with pytest.raises(dispatch.DispatchError):
        dispatch.claim(REF, "")
    token = dispatch.claim(REF, "agent")["lease_token"]
    for bad in ({"outcome": "done"}, {"outcome": "attempted", "operation_receipt_ref": "INV-123"},
                {"outcome": "attempted", "note": "y" * 281}):
        with pytest.raises(dispatch.DispatchError):
            dispatch.report(REF, token, bad.pop("outcome"), **bad)


def test_queue_file_is_private_and_owner_history_is_hidden_from_agents(isolated):
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    if os.name == "posix":
        assert oct((isolated / ".open_work_dispatch.json").stat().st_mode & 0o777) == "0o600"
    assert "history" not in dispatch.status(owner=False)["offers"][0]
    assert "history" in dispatch.status(owner=True)["offers"][0]


def test_dashboard_and_mcp_wiring():
    root = Path(__file__).resolve().parents[1]
    script = (root / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")
    assert 'id="pvDispatchEnabled"' in script and "/v1/open-work/dispatch/offers" in script
    for name in ("compact_stdio.py", "compact_http_app.py"):
        assert "register_dispatch_tools(mcp, _secure_runtime)" in (root / "mcp_server" / name).read_text()
    history = (root / "server" / "history_routes.py").read_text(encoding="utf-8")
    assert "clear_dispatch_offers()" in history


def test_rest_routes_split_owner_and_agent_authority(tmp_path):
    import subprocess
    import sys
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, open_work_reviews as reviews
reviews._allowed_days = lambda: 7
ref = "owg:w:" + "a" * 32
reviews.save_review_intention(ref, "assess_follow_up", provider_candidate_currently_open=True)
owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
agent = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
with TestClient(app) as client:
    assert client.get("/v1/open-work/dispatch").status_code == 401
    assert client.get("/v1/open-work/dispatch", headers=agent).status_code == 403
    assert client.post("/v1/open-work/dispatch/settings", json={"enabled": True}, headers=agent).status_code == 403
    assert client.post("/v1/open-work/dispatch/settings", json={"enabled": True}, headers=owner).status_code == 200
    assert client.post("/v1/open-work/dispatch/offers", json={"work_item_ref": ref}, headers=owner).status_code == 403
    assert client.post("/v1/open-work/dispatch/offers", json={"work_item_ref": ref, "confirm_offer": True},
                       headers=owner).status_code == 200
    listed = client.get("/v1/open-work/dispatch/agent", headers=agent).json()
    assert [o["work_item_ref"] for o in listed["offers"]] == [ref]
    claim = client.post("/v1/open-work/dispatch/agent/claim", json={"work_item_ref": ref, "agent": "a"}, headers=agent)
    assert claim.status_code == 200, claim.text
    again = client.post("/v1/open-work/dispatch/agent/claim", json={"work_item_ref": ref, "agent": "b"}, headers=agent)
    assert again.status_code == 409
    report = client.post("/v1/open-work/dispatch/agent/report", headers=agent, json={
        "work_item_ref": ref, "lease_token": claim.json()["lease_token"], "outcome": "needs_human"})
    assert report.status_code == 200 and report.json()["status"] == "reported"
    assert client.post("/v1/open-work/dispatch/agent/claim", content=b"x" * 3000,
                       headers={**agent, "Content-Type": "application/json"}).status_code == 413
    for invalid in (
        b'{"work_item_ref":"one","work_item_ref":"two","agent":"a"}',
        b'{"work_item_ref":"one","agent":NaN}',
        b'{"work_item_ref":"one","agent":"a"} trailing',
    ):
        result = client.post("/v1/open-work/dispatch/agent/claim", content=invalid,
                             headers={**agent, "Content-Type": "application/json"})
        assert result.status_code == 422, result.text
    assert client.post("/v1/open-work/dispatch/agent/claim", content=b'{}',
                       headers={**agent, "Content-Type": "text/plain"}).status_code == 415
    # An empty JSON body still reaches route-level semantic validation, not parser rejection.
    empty = client.post("/v1/open-work/dispatch/agent/claim", content=b"",
                        headers={**agent, "Content-Type": "application/json"})
    assert empty.status_code != 422, empty.text

'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / "data"), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / "auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / "config.json"))
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
