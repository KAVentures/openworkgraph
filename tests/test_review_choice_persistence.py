"""Durable human review is authenticated, optimistic and never an execution grant."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import review_choice_routes as routes


def test_review_lifecycle_conflict_stale_and_denial(monkeypatch, tmp_path):
    monkeypatch.setattr(routes.pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    state = {"revision": 2}
    def journey(idea_id):
        return {
            "idea": {"idea_id": idea_id, "revision": state["revision"], "state": "approved"},
            "process_status": {"status": "current", "process_reference": {}},
            "projects": [{"project_id": "p1", "idea_revision": state["revision"],
                          "idea_revision_is_current": True, "spec_sha256": "a"*64,
                          "archive_sha256": "b"*64, "reported_specifications": []}],
            "projects_truncated": False, "dataset_origins": [],
            "dataset_origin_check_available": True,
        }
    monkeypatch.setattr(routes.opportunity_journey, "journey", journey)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    root = "/v1/review-choice-workbench/idea"
    headers = {"Authorization": "OWG-Session valid"}
    snapshot = client.get(root, headers=headers).json()
    basis = {"project_id": "p1", "project_set_sha256": snapshot["project_set_sha256"],
             "idea_revision": 2, "idea_state": "approved", "spec_sha256": "a"*64,
             "archive_sha256": "b"*64, "process_status": "current",
             "process_reference": {}, "dataset_origins": [],
             "dataset_origin_check_available": True,
             "blockers": snapshot["projects"][0]["blockers"]}
    body = {"basis": basis, "expected_review_revision": 0,
            "decision": "draft", "reviewer_notes": "Needs evidence"}
    assert client.post(root+"/review", json=body).status_code == 403
    saved = client.post(root+"/review", json=body, headers=headers)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["execution_authorized"] is False
    assert client.post(root+"/review", json=body, headers=headers).status_code == 409
    body["expected_review_revision"] = 1
    body["decision"] = "approved"
    assert client.post(root+"/review", json=body, headers=headers).status_code == 409
    body["decision"] = "revoked"
    assert client.post(root+"/review", json=body, headers=headers).status_code == 200
    record = client.get(root+"/review/p1", headers=headers).json()
    assert record["decision"] == "revoked"
    assert record["revision"] == 2
    assert record["execution_authorized"] is False
    state["revision"] = 3
    body["expected_review_revision"] = 2
    assert client.post(root+"/review", json=body, headers=headers).status_code == 409
    assert client.get(root+"/review/p1").status_code == 403


def test_revocation_recovery_when_opportunity_is_no_longer_inspectable(monkeypatch, tmp_path):
    """Stored review revisions remain accessible after the source disappears."""
    monkeypatch.setattr(routes.pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    def unavailable(_idea_id):
        raise routes.pipeline.PipelineError("unknown idea")
    monkeypatch.setattr(routes.opportunity_journey, "journey", unavailable)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    root = "/v1/review-choice-workbench/removed/review/p1"
    headers = {"Authorization": "OWG-Session valid"}
    with routes.pipeline._LOCK, routes.pipeline.closing(routes.pipeline._connect()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        conn.execute(
            "INSERT INTO project_human_reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("removed", "p1", 4, "approved", "a"*64, "previous approval", "2026-11-09T00:00:00Z"))
        conn.commit()
    assert client.get(root).status_code == 403
    record = client.get(root, headers=headers)
    assert record.status_code == 200
    assert record.json()["revision"] == 4
    assert record.json()["decision"] == "approved"
    assert client.get("/v1/review-choice-workbench/removed", headers=headers).status_code == 404
    assert client.post(root+"/revoke", json={"expected_review_revision": 4}).status_code == 403
    revoked = client.post(root+"/revoke", json={"expected_review_revision": 4}, headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["decision"] == "revoked"
    assert client.get(root, headers=headers).json()["revision"] == 5


def test_reviewed_handoff_requires_matching_approved_revision_and_revocation(monkeypatch, tmp_path):
    """Actual authenticated navigation gate rejects drafts, stale bases and revocation."""
    import hashlib
    import json
    from fastapi.responses import JSONResponse

    monkeypatch.setattr(routes.pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    basis = {"project_id": "p1", "project_set_sha256": "c"*64,
             "idea_revision": 2, "idea_state": "approved", "spec_sha256": "a"*64,
             "archive_sha256": "b"*64, "process_status": "current",
             "process_reference": {}, "dataset_origins": [],
             "dataset_origin_check_available": True, "blockers": []}
    snapshot = {
        "idea": {"idea_id": "idea", "revision": 2, "state": "approved"},
        "process_status": "current", "process_reference": {},
        "dataset_origins": [], "dataset_origin_check_available": True,
        "project_set_sha256": "c"*64, "projects_truncated": False,
        "projects": [{"project_id": "p1", "idea_revision": 2,
                      "spec_sha256": "a"*64, "archive_sha256": "b"*64,
                      "blockers": []}],
    }
    monkeypatch.setattr(routes, "choices", lambda request, idea_id: JSONResponse(snapshot))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    root = "/v1/review-choice-workbench/idea"
    headers = {"Authorization": "OWG-Session valid"}
    gate = root + "/reviewed-handoff-preflight"
    assert client.post(gate, json=basis).status_code == 403
    assert client.post(gate, json=basis, headers=headers).status_code == 409
    draft = client.post(root+"/review", json={
        "basis": basis, "expected_review_revision": 0,
        "decision": "draft"}, headers=headers)
    assert draft.status_code == 200
    assert client.post(gate, json=basis, headers=headers).status_code == 409
    approved = client.post(root+"/review", json={
        "basis": basis, "expected_review_revision": 1,
        "decision": "approved"}, headers=headers)
    assert approved.status_code == 200
    passed = client.post(gate, json=basis, headers=headers)
    assert passed.status_code == 200
    assert passed.json()["human_review_approved"] is True
    assert passed.json()["execution_authorized"] is False
    changed = dict(basis, project_set_sha256="d"*64)
    assert client.post(gate, json=changed, headers=headers).status_code == 409
    revoked = client.post(root+"/review/p1/revoke", json={
        "expected_review_revision": 2}, headers=headers)
    assert revoked.status_code == 200
    assert client.post(gate, json=basis, headers=headers).status_code == 409
