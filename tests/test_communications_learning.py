"""Learned multi-label intents from consented, retention-bounded review examples (#370 R22)."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from shared import communications_learning as learning
from shared.communications_mining import Message, analyze

ROOT = Path(__file__).resolve().parents[1]
PHRASES = {
    "Refund request": {"en": ["I want a refund for my order", "please give me my money back", "can I get reimbursed"],
                       "sv": ["jag vill ha återbetalning", "ge mig pengarna tillbaka tack", "kan jag få ersättning"],
                       "de": ["ich möchte eine Rückerstattung", "bitte Geld zurück", "Erstattung für meine Bestellung"]},
    "Order or delivery status": {"en": ["where is my parcel", "the delivery has not arrived", "tracking shows nothing"],
                                 "sv": ["var är mitt paket", "leveransen har inte kommit", "spårningen visar inget"],
                                 "de": ["wo ist mein Paket", "die Lieferung ist nicht angekommen", "Sendungsverfolgung leer"]},
    "Account access": {"en": ["I cannot log in", "my password reset fails", "locked out of my account"],
                       "sv": ["jag kan inte logga in", "lösenordet fungerar inte", "utelåst från kontot"],
                       "de": ["ich kann mich nicht anmelden", "Passwort zurücksetzen geht nicht", "Konto gesperrt"]},
}
FILLER = {"en": ["hello", "thanks", "regards", "today", "again"], "sv": ["hej", "tack", "hälsningar", "idag", "igen"],
          "de": ["hallo", "danke", "grüße", "heute", "nochmal"]}


def corpus(n, seed=1):
    r = random.Random(seed)
    out = []
    for i in range(n):
        lang = r.choice(sorted(FILLER))
        labels = r.sample(sorted(PHRASES), r.choice([0, 1, 1, 1, 2]))
        parts = [r.choice(PHRASES[x][lang]) for x in labels] + r.sample(FILLER[lang], 2)
        r.shuffle(parts)
        out.append({"example_id": f"e{seed}_{i}", "text": " ".join(parts), "labels": labels, "language": lang})
    return out


def test_model_learns_multilingual_multi_label_intents_and_reports_holdout_quality():
    model = learning.train(corpus(360), sorted(PHRASES))
    report = learning.evaluate(model, corpus(150, seed=2))
    assert report["languages"] == ["de", "en", "sv"] and report["supported_intent_count"] == 3
    assert report["macro_f1_supported_intents"] >= 0.9
    for item in report["intents"]:
        assert item["sufficient_support"] and item["expected_calibration_error"] < 0.15
        assert set(item["by_language"]) == {"de", "en", "sv"}
        assert item["precision_interval"][0] <= item["precision"] <= item["precision_interval"][1]
    predict = learning.predictor(json.loads(json.dumps(model)))
    both = learning.decide(predict("var är mitt paket och jag vill ha återbetalning"))
    assert both["Order or delivery status"] == "present" and both["Refund request"] == "present"
    assert learning.primary_label(both) == "multiple intents"
    assert learning.primary_label(learning.decide(predict("hello thanks regards"))) == "unclassified"
    assert report["verified_reasons_for_contact"] is False


def test_training_is_deterministic_and_skips_intents_without_enough_examples():
    examples = corpus(120)
    assert learning.train(examples, sorted(PHRASES)) == learning.train(examples, sorted(PHRASES))
    model = learning.train(examples, sorted(PHRASES) + ["Never labelled"])
    assert model["skipped_intents"] == [{"intent": "Never labelled", "positive_examples": 0,
                                         "reason": "insufficient_examples"}]
    with pytest.raises(learning.LearningError):
        learning.train(examples[:5], sorted(PHRASES))
    with pytest.raises(learning.LearningError):
        learning.train(examples, ["Never labelled"])


def test_uncertain_band_and_primary_labels():
    assert learning.decide({"a": 0.9, "b": 0.5, "c": 0.1}) == {"a": "present", "b": "uncertain", "c": "absent"}
    assert learning.primary_label({"a": "absent", "b": "uncertain"}) == "uncertain"
    assert learning.primary_label({"a": "present", "b": "absent"}) == "a"


def test_learned_analysis_is_multi_label_aggregate_and_samples_the_least_certain():
    model = learning.train(corpus(360), sorted(PHRASES))
    messages = [Message(None, "var är mitt paket och jag vill ha återbetalning"), Message(None, "I cannot log in"),
                Message(None, "hello thanks regards"), Message(None, "Here is your refund", direction="outbound")]
    report = analyze(messages, learned=learning.predictor(model), review_sample=2)
    counts = {i["intent"]: i["messages"] for i in report["intents"]}
    assert counts == {"multiple intents": 1, "Account access": 1, "unclassified": 1, "outbound reply": 1}
    learned = {i["intent"]: i["present_messages"] for i in report["learned_intents"]}
    assert learned == {"Account access": 1, "Order or delivery status": 1, "Refund request": 1}
    assert report["intents_are_keyword_matches_not_verified_reasons"] is False
    assert report["intents_are_model_estimates_not_verified_reasons"] is True
    assert len(report["_review_candidates"]) == 2 and "Here is your refund" not in report["_review_candidates"]
    keyword = analyze(messages)
    assert "learned_intents" not in keyword and keyword["_review_candidates"] == []


@pytest.fixture
def store(tmp_path, monkeypatch):
    from server import communications_review as review
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    return review


def _on(review, days=30):
    return review.configure({"enabled": True, "acknowledgement_version": review.ACK_VERSION, "retention_days": days})


def test_review_examples_need_consent_are_redacted_and_deleted_when_turned_off(store, tmp_path):
    with pytest.raises(store.ReviewError):
        store.add_examples([{"text": "hello"}])
    with pytest.raises(store.ReviewError, match="acknowledge"):
        store.configure({"enabled": True, "retention_days": 30})
    _on(store)
    added = store.add_examples([{"text": "Mail a.b@example.com or +46 70 123 45 67, card 4111 1111 1111 1111 order 12345",
                                 "language": "en"}, {"text": "   "}])
    assert added == {"added": 1, "skipped_duplicates_or_empty": 1, "texts_redacted": True}
    [kept] = store.listing()["examples"]
    assert "example.com" not in kept["text"] and "4111" not in kept["text"] and "123 45 67" not in kept["text"]
    assert kept["labels"] is None and kept["split"] in {"train", "holdout"}
    if os.name == "posix":
        assert (tmp_path / "communications_review.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(store.ReviewError):
        store.label({"example_id": kept["example_id"], "labels": ["Not an intent"]}, ["Refund request"])
    store.label({"example_id": kept["example_id"], "labels": [], "language": "sv"}, ["Refund request"])
    assert store.listing("labelled")["examples"][0]["labels"] == []
    off = store.configure({"enabled": False})
    assert off["example_count"] == 0 and off["model_trained"] is False
    assert not (tmp_path / "communications_intent_model.json").exists()


def test_retention_expires_examples_and_shorter_retention_applies_to_kept_ones(store):
    _on(store, days=30)
    store.add_examples([{"text": "first message"}])
    _on(store, days=1)
    [kept] = store.listing()["examples"]
    assert kept["expires_at"] <= (datetime.now(timezone.utc) + timedelta(days=1, minutes=1)).isoformat()
    data = json.loads(store._path().read_text())
    data["examples"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store._path().write_text(json.dumps(data))
    assert store.listing()["total"] == 0
    with pytest.raises(store.ReviewError):
        store.configure({"enabled": True, "acknowledgement_version": store.ACK_VERSION, "retention_days": 91})


def test_training_uses_only_the_training_split_and_evaluates_on_holdout(store, monkeypatch):
    _on(store)
    examples = corpus(400)
    store.add_examples([{"text": e["text"], "language": e["language"]} for e in examples[:200]])
    store.add_examples([{"text": e["text"], "language": e["language"]} for e in examples[200:]])
    by_text = {e["text"]: e for e in examples}
    for kept in store.listing(limit=200)["examples"] + store.listing(limit=200, offset=200)["examples"]:
        store.label({"example_id": kept["example_id"], "labels": by_text[kept["text"]]["labels"]}, sorted(PHRASES))
    seen = []
    real = learning.train
    monkeypatch.setattr(store.learning, "train", lambda rows, intents: seen.extend(rows) or real(rows, intents))
    status = store.train_model(sorted(PHRASES))
    assert status["trained"] and all(r["split"] == "train" for r in seen)
    assert status["evaluation"]["holdout_examples"] == sum(e["split"] == "holdout" for e in json.loads(
        store._path().read_text())["examples"]) > 0
    predict, reference = store.learned_classifier()
    assert reference["model_sha256"] == status["model_sha256"]
    assert store.delete_model() == {"deleted": True} and store.learned_classifier() is None


def test_real_http_routes_consent_learned_analysis_and_no_text_in_reports(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
import os
EXAMPLES = json.loads(open(os.environ["OWG_TEST_EXAMPLES"], encoding="utf-8").read())
rows = ["ticket,text"] + [f"T{i},{e['text']}" for i, e in enumerate(EXAMPLES[:40])]
body = "\n".join(rows).encode()
query = "/v1/communications/analyze?format=csv&text_columns=text&thread_column=ticket"
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    confirmed = {**owner, "x-owg-local-processing-confirmed": "true"}
    assert client.get("/v1/communications/review/settings", headers=bearer).status_code == 403
    assert client.post(query + "&classifier=learned", content=body, headers=confirmed).status_code == 409
    assert client.post(query + "&review_sample=10", content=body, headers=confirmed).status_code == 403
    sample = {**confirmed, "x-owg-review-examples-confirmed": "true"}
    assert client.post(query + "&review_sample=10", content=body, headers=sample).status_code == 409
    settings = client.get("/v1/communications/review/settings", headers=owner).json()
    on = client.put("/v1/communications/review/settings", headers=owner, json={
        "enabled": True, "acknowledgement_version": settings["acknowledgement_version"], "retention_days": 7})
    assert on.status_code == 200 and on.json()["enabled"] is True
    sampled = client.post(query + "&review_sample=10", content=body, headers=sample)
    assert sampled.status_code == 200 and sampled.json()["review_examples"]["added"] == 10, sampled.text
    assert "_review_candidates" not in sampled.text and EXAMPLES[0]["text"] not in sampled.text
    assert client.post("/v1/communications/review/examples", headers=owner,
                       json={"examples": [{"text": "x"}]}).status_code == 403
    add = client.post("/v1/communications/review/examples", headers={**owner, "x-owg-review-examples-confirmed": "true"},
                      json={"examples": [{"text": e["text"], "language": e["language"]} for e in EXAMPLES[:200]]})
    assert add.status_code == 200
    add = client.post("/v1/communications/review/examples", headers={**owner, "x-owg-review-examples-confirmed": "true"},
                      json={"examples": [{"text": e["text"], "language": e["language"]} for e in EXAMPLES[200:]]})
    by_text = {e["text"]: e["labels"] for e in EXAMPLES}
    offset = 0
    while True:
        page = client.get(f"/v1/communications/review/examples?limit=200&offset={offset}", headers=owner).json()
        for item in page["examples"]:
            labels = by_text.get(item["text"], [])
            r = client.post("/v1/communications/review/label", headers=owner,
                            json={"example_id": item["example_id"], "labels": labels})
            assert r.status_code == 200, r.text
        offset += 200
        if offset >= page["total"]:
            break
    trained = client.post("/v1/communications/review/train", headers=owner)
    assert trained.status_code == 200 and trained.json()["trained"], trained.text
    assert trained.json()["evaluation"]["holdout_examples"] > 0
    learned = client.post(query + "&classifier=learned", content=body, headers=confirmed)
    assert learned.status_code == 200, learned.text
    data = learned.json()
    assert data["learned_intents"] and data["model_reference"]["model_sha256"] == trained.json()["model_sha256"]
    assert EXAMPLES[0]["text"] not in learned.text
    # A later human correction invalidates the earlier held-out score and
    # learned classification; the keyword path remains available.
    [first] = client.get("/v1/communications/review/examples?status=labelled&limit=1",
                         headers=owner).json()["examples"]
    correction = client.post("/v1/communications/review/label", headers=owner, json={
        "example_id": first["example_id"], "labels": first["labels"],
        "language": "sv" if first["language"] != "sv" else "en"})
    assert correction.status_code == 200, correction.text
    freshness = client.get("/v1/communications/review/model", headers=owner)
    assert freshness.status_code == 200, freshness.text
    assert freshness.json()["trained"] and not freshness.json()["usable"]
    assert not freshness.json()["evaluation_current"]
    stale = client.post(query + "&classifier=learned", content=body, headers=confirmed)
    assert stale.status_code == 409 and "stale" in stale.text
    assert client.post(query + "&classifier=keyword", content=body, headers=confirmed).status_code == 200
    off = client.put("/v1/communications/review/settings", headers=owner, json={"enabled": False})
    assert off.json()["example_count"] == 0 and off.json()["model_trained"] is False
    assert client.post(query + "&classifier=learned", content=body, headers=confirmed).status_code == 409
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    data = [e for e in corpus(420) if "," not in e["text"]]
    # The examples go in a file: Windows caps a command line at 32,767 characters.
    examples = tmp_path / "examples.json"
    examples.write_text(json.dumps(data), encoding="utf-8")
    env["OWG_TEST_EXAMPLES"] = str(examples)
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
