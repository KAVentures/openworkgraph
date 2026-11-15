"""Regression guard for the first authenticated human-review journey.

The review read API intentionally returns revision 0 / decision 'unreviewed'
when no durable review exists. The browser must accept this state so that the
operator can create the very first draft or approval. This source-contract
check complements (and does not replace) API-backed browser acceptance.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_first_review_state_is_supported_by_api_and_browser():
    server = (ROOT / "server" / "review_choice_routes.py").read_text()
    browser = (ROOT / "dashboard" / "review_choice_workbench.js").read_text()
    assert '"decision": row[1] if row else "unreviewed"' in server
    assert '"revision": row[0] if row else 0' in server
    assert "!['unreviewed','draft','approved','revoked'].includes(state.decision)" in browser
    assert "reviewRevision=state.revision" in browser
    assert "reviewTrusted=true" in browser


def test_inflight_review_save_does_not_trust_unsaved_notes():
    """Guard the async approval boundary against edited notes being mislabeled saved."""
    browser = (ROOT / "dashboard" / "review_choice_workbench.js").read_text()
    assert "const submittedNotes=notes.value,submittedNotesEpoch=notesEpoch" in browser
    assert "reviewer_notes:submittedNotes" in browser
    assert "savedNotes=submittedNotes" in browser
    assert "reviewTrusted=notesEpoch===submittedNotesEpoch&&notes.value===submittedNotes" in browser
    assert "notes.value===savedNotes" in browser


def test_first_review_live_http_revision_zero_and_conflict_recovery(monkeypatch, tmp_path):
    """Real mounted FastAPI routes and SQLite, not fabricated browser responses."""
    import sqlite3
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import review_choice_routes as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    db = tmp_path / "first-review.sqlite3"
    monkeypatch.setattr(routes.pipeline, "_connect", lambda: sqlite3.connect(db))
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "owner")
    evidence = {"digest": "b" * 64}

    def journey(idea_id):
        return {
            "idea": {"idea_id": idea_id, "revision": 3, "state": "approved"},
            "process_status": {"status": "current", "process_reference": {}},
            "projects": [{
                "project_id": "p1", "idea_revision": 3,
                "idea_revision_is_current": True,
                "spec_sha256": evidence["digest"], "archive_sha256": "c" * 64,
                "reported_specifications": [{"status": "matching_reported_specification"}],
            }],
            "projects_truncated": False, "dataset_origins": [{"status": "current",
                "source_content_sha256": "e" * 64, "selection_sha256": "f" * 64}],
            "dataset_origin_check_available": True,
        }

    monkeypatch.setattr(routes.opportunity_journey, "journey", journey)
    root = "/v1/review-choice-workbench/idea-1"
    headers = {"Authorization": "OWG-Session owner"}
    review_url = root + "/review/p1"
    assert client.get(review_url).status_code == 403
    initial = client.get(review_url, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["decision"] == "unreviewed"
    assert initial.json()["revision"] == 0
    assert initial.json()["execution_authorized"] is False

    snapshot = client.get(root, headers=headers).json()
    project = snapshot["projects"][0]
    basis = {
        "project_id": "p1", "project_set_sha256": snapshot["project_set_sha256"],
        "idea_revision": 3, "idea_state": "approved",
        "spec_sha256": project["spec_sha256"], "archive_sha256": project["archive_sha256"],
        "process_status": snapshot["process_status"],
        "process_reference": snapshot["process_reference"],
        "dataset_origins": snapshot["dataset_origins"],
        "dataset_origin_check_available": snapshot["dataset_origin_check_available"],
        "blockers": project["blockers"],
    }
    assert basis["blockers"] == []  # Approval requires a genuinely unblocked basis.
    payload = {"basis": basis, "expected_review_revision": 0,
               "decision": "approved", "reviewer_notes": "first approval"}
    assert client.post(root + "/review", json=payload).status_code == 403
    evidence["digest"] = "d" * 64
    assert client.post(root + "/review", json=payload, headers=headers).status_code == 409
    assert client.get(review_url, headers=headers).json()["revision"] == 0
    evidence["digest"] = "b" * 64
    approved = client.post(root + "/review", json=payload, headers=headers)
    assert approved.status_code == 200
    assert approved.json()["revision"] == 1
    assert approved.json()["decision"] == "approved"
    assert approved.json()["execution_authorized"] is False
    assert client.post(root + "/review", json=payload, headers=headers).status_code == 409
    persisted = client.get(review_url, headers=headers).json()
    assert persisted["revision"] == 1
    assert persisted["reviewer_notes"] == "first approval"

    # A revoked local session must not read or mutate an approved review.
    # Restore the session only to inspect the persisted revision afterward.
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: False)
    assert client.get(review_url, headers=headers).status_code == 403
    assert client.post(root + "/review", json=payload, headers=headers).status_code == 403
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "owner")
    after_revocation = client.get(review_url, headers=headers).json()
    assert after_revocation["revision"] == 1
    assert after_revocation["decision"] == "approved"
    assert after_revocation["execution_authorized"] is False
