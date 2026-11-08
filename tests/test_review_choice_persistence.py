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
