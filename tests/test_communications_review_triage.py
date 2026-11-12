"""R22 consented communications active-review ordering; sealed holdout stays excluded."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import communications_review as review
from server import communications_review_triage as triage
from shared import communications_mining as mining
from shared import communications_learning as learning

TAXONOMY = {"format": mining.FORMAT, "intents": [
    {"name": "Refund", "terms": ["refund"]},
    {"name": "Cancel", "terms": ["cancel"]},
]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)


def enabled():
    return review.configure({"enabled": True, "acknowledgement_version": review.ACK_VERSION,
                             "retention_days": 30})


def seed(monkeypatch):
    enabled()
    options = iter(("train", "holdout", "train", "train"))
    monkeypatch.setattr(review, "_split", lambda _id: next(options))
    review.add_examples([
        {"text": "Please refund this item"},
        {"text": "Please cancel my plan"},
        {"text": "refund and cancel"},
        {"text": "Please check case status"},
    ])
    return review.listing()["examples"]


def test_consent_required_and_disabled_examples_never_reappearing(monkeypatch):
    with pytest.raises(review.ReviewError, match="turn on review examples"):
        triage.queue(TAXONOMY)
    seed(monkeypatch)
    assert triage.queue(TAXONOMY)["total"] == 3
    review.configure({"enabled": False})
    with pytest.raises(review.ReviewError, match="turn on review examples"):
        triage.queue(TAXONOMY)
    enabled()
    assert triage.queue(TAXONOMY)["total"] == 0


def test_ranked_keyword_examples_and_sealed_unlabelled_holdout(monkeypatch):
    examples = seed(monkeypatch)
    output = triage.queue(TAXONOMY)
    assert output["selected_split"] == "train"
    assert output["unlabelled_holdout_excluded"] == 1
    assert output["total"] == output["total_eligible"] == 3
    assert output["learned_predictions_used"] is False
    assert output["model_status"] == "absent"
    assert [e["priority"] for e in output["examples"]] == [
        "keyword_ambiguous", "keyword_unclassified", "routine"]
    assert {e["example_id"] for e in output["examples"]}.isdisjoint(
        {e["example_id"] for e in examples if e["split"] == "holdout"})
    assert output["source_systems_queried"] is False
    assert output["external_transmission"] is False
    assert output["labels_auto_applied"] is False
    assert output["model_retrained"] is False
    # Running the suggestions again does not update labels or use held-out text.
    assert triage.queue(TAXONOMY) == output
    assert all(row["labels"] is None for row in review.listing()["examples"])


def test_human_review_removes_labelled_example_and_pagination_is_bounded(monkeypatch):
    seed(monkeypatch)
    first = triage.queue(TAXONOMY, limit=1)
    assert len(first["examples"]) == 1
    assert first["total"] == 3
    review.label({"example_id": first["examples"][0]["example_id"], "labels": ["Refund"]},
                 ["Refund", "Cancel"])
    result = triage.queue(TAXONOMY, limit=1, offset=1)
    assert result["total"] == 2 and len(result["examples"]) == 1
    assert first["examples"][0]["example_id"] not in {
        e["example_id"] for e in triage.queue(TAXONOMY)["examples"]}
    for kwargs in ({"limit": 0}, {"limit": 51}, {"limit": True},
                   {"offset": -1}, {"offset": 2001}, {"offset": False}):
        with pytest.raises(review.ReviewError):
            triage.queue(TAXONOMY, **kwargs)


def test_current_local_model_suggestions_do_not_become_labels(monkeypatch):
    seed(monkeypatch)
    monkeypatch.setattr(review, "_load_model", lambda: {"model_sha256": "synthetic"})
    monkeypatch.setattr(review, "_freshness", lambda *_args: (True, None))
    def predict(_model):
        def run(text):
            if "refund and cancel" in text:
                return {"Refund": 0.5, "Cancel": 0.5}
            if "refund" in text:
                return {"Refund": 0.05, "Cancel": 0.95}
            return {"Refund": 0.05, "Cancel": 0.05}
        return run
    monkeypatch.setattr(learning, "predictor", predict)
    result = triage.queue(TAXONOMY)
    assert result["model_status"] == "current"
    assert result["learned_predictions_used"] is True
    assert [row["priority"] for row in result["examples"]][:2] == [
        "model_keyword_disagreement", "model_uncertain"]
    assert result["examples"][0]["keyword_intent"] == "Refund"
    assert result["examples"][0]["learned_primary_intent"] == "Cancel"
    assert all(row["labels"] is None for row in result["examples"])


def test_stale_model_falls_back_to_keywords_without_using_predictions(monkeypatch):
    seed(monkeypatch)
    monkeypatch.setattr(review, "_load_model", lambda: {"old": True})
    monkeypatch.setattr(review, "_freshness", lambda *_args: (False, "labelled_examples_changed_or_expired"))
    def must_not_predict(_):
        raise AssertionError("stale model must never be loaded for inference")
    monkeypatch.setattr(learning, "predictor", must_not_predict)
    result = triage.queue(TAXONOMY)
    assert result["model_status"] == "stale"
    assert result["model_stale_reason"] == "labelled_examples_changed_or_expired"
    assert result["learned_predictions_used"] is False
    assert [row["priority"] for row in result["examples"]] == [
        "keyword_ambiguous", "keyword_unclassified", "routine"]


def test_local_http_route_requires_dashboard_session_and_opt_in(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import communications_review as review
from server import communications_review_triage as triage
route='/v1/communications/review/triage'
with TestClient(app) as client:
    assert client.get(route).status_code == 403
    token=local_auth.ensure_api_token()
    assert client.get(route,headers={'Authorization':'Bearer '+token}).status_code in (401,403)
    session=local_auth.create_dashboard_session()
    owner={'Authorization':'OWG-Session '+session}
    assert client.get(route,headers=owner).status_code == 409
    review.configure({'enabled':True,'acknowledgement_version':review.ACK_VERSION,'retention_days':30})
    review.add_examples([{'text':'Need a refund'}])
    response=client.get(route+'?limit=1&offset=0',headers=owner)
    assert response.status_code == 200,response.text
    assert response.headers['cache-control'] == 'no-store'
    body=response.json()
    assert body['format'] == triage.FORMAT
    assert body['labels_auto_applied'] is False
    assert body['review_texts_are_existing_redacted_opt_in_examples'] is True
    assert client.get(route+'?limit=10000',headers=owner).status_code == 422
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    run = subprocess.run([sys.executable, "-c", code], env=env,
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"
