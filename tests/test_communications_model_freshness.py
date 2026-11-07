"""R22: a learned communications model is usable only with its reviewed training/holdout basis."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from server import communications_review as review


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    review.configure({"enabled": True, "acknowledgement_version": review.ACK_VERSION,
                      "retention_days": 30})
    examples = []
    for i in range(60):
        suffix = chr(97 + i // 26) + chr(97 + i % 26)
        intent = "Refund" if i % 2 == 0 else "Account access"
        text = ("Please refund money back for purchase " if i % 2 == 0
                else "Need password help to login account ") + suffix
        examples.append((text, intent))
    review.add_examples([{"text": text, "language": "en"} for text, _ in examples])
    by_text = dict(examples)
    for example in review.listing(limit=200)["examples"]:
        review.label({"example_id": example["example_id"],
                      "labels": [by_text[example["text"]]]}, ["Refund", "Account access"])
    status = review.train_model(["Refund", "Account access"])
    assert status["trained"] and status["usable"] and status["evaluation_current"]
    return review


def test_unchanged_training_basis_is_usable_and_unlabelled_addition_does_not_invalidate(prepared):
    store = prepared
    before = store.model_status(["Refund", "Account access"])
    assert before["trained"] and before["usable"] and before["stale"] is False
    assert store.learned_classifier(["Refund", "Account access"]) is not None
    store.add_examples([{"text": "Unlabelled new message here"}])
    assert store.model_status(["Refund", "Account access"])["usable"]
    assert store.learned_classifier() is not None
    # A repeat save of the exact same labels and language must also be harmless.
    example = store.listing(status="labelled", limit=1)["examples"][0]
    store.label({"example_id": example["example_id"],
                 "labels": example["labels"], "language": example["language"]},
                ["Refund", "Account access"])
    assert store.model_status()["evaluation_current"]


def test_changed_holdout_label_invalidates_quality_and_classifier(prepared):
    store = prepared
    holdout = next(x for x in store.listing(status="labelled", limit=200)["examples"]
                   if x["split"] == "holdout")
    old = store.model_status()
    store.label({"example_id": holdout["example_id"], "labels": [],
                 "language": holdout["language"]}, ["Refund", "Account access"])
    state = store.model_status()
    assert state["trained"] and not state["usable"]
    assert state["stale_reason"] == "labelled_examples_changed_or_expired"
    assert state["evaluation_current"] is False
    assert state["evaluation"] is None, "stale quality scores must not be returned"
    assert state["model_sha256"] == old["model_sha256"]
    with pytest.raises(store.ReviewError, match="stale"):
        store.learned_classifier(["Refund", "Account access"])
    retrained = store.train_model(["Refund", "Account access"])
    assert retrained["usable"] and retrained["evaluation_current"]
    assert store.learned_classifier() is not None


def test_training_row_deletion_and_language_correction_both_require_retraining(prepared):
    store = prepared
    training = next(x for x in store.listing(status="labelled", limit=200)["examples"]
                    if x["split"] == "train")
    store.label({"example_id": training["example_id"], "labels": training["labels"],
                 "language": "sv"}, ["Refund", "Account access"])
    assert not store.model_status()["usable"]
    store.train_model(["Refund", "Account access"])
    store.delete([training["example_id"]])
    assert not store.model_status()["usable"]


def test_expiration_and_taxonomy_changes_mark_previous_scores_stale(prepared):
    store = prepared
    original = store.model_status()
    assert store.model_status(["Refund", "Changed access"])["stale_reason"] == "intent_taxonomy_changed"
    with pytest.raises(store.ReviewError, match="stale"):
        store.learned_classifier(["Refund", "Changed access"])
    assert store.model_status(["Refund", "Account access"])["usable"]
    data = json.loads(store._path().read_text(encoding="utf-8"))
    data["examples"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
    store._path().write_text(json.dumps(data), encoding="utf-8")
    assert store.model_status()["stale_reason"] == "labelled_examples_changed_or_expired"
    assert store.model_status()["model_sha256"] == original["model_sha256"]


def test_legacy_model_without_basis_fails_closed(prepared):
    store = prepared
    file = store._model_path()
    payload = json.loads(file.read_text(encoding="utf-8"))
    del payload["training_basis_sha256"]
    file.write_text(json.dumps(payload), encoding="utf-8")
    assert store.model_status()["stale_reason"] == "legacy_model_has_no_training_basis"
    with pytest.raises(store.ReviewError, match="stale"):
        store.learned_classifier()


def test_changed_review_during_training_does_not_commit_incorrect_evaluation(prepared, monkeypatch):
    store = prepared
    before = store.model_status()["model_sha256"]
    original_train = store.learning.train
    changed = store.listing(status="labelled", limit=1)["examples"][0]
    def mutation_during_training(rows, intents):
        result = original_train(rows, intents)
        store.label({"example_id": changed["example_id"], "labels": [],
                     "language": "sv"}, intents)
        return result
    monkeypatch.setattr(store.learning, "train", mutation_during_training)
    with pytest.raises(store.ReviewError, match="changed during training"):
        store.train_model(["Refund", "Account access"])
    # The old model remains on disk but is now stale; a partially trained
    # replacement must never overwrite it.
    assert store.model_status()["model_sha256"] == before
    assert store.model_status()["usable"] is False


def test_disabling_review_still_deletes_model_and_all_review_examples(prepared):
    store = prepared
    status = store.configure({"enabled": False})
    assert status["example_count"] == 0 and not status["model_trained"]
    assert store.learned_classifier() is None
    assert not store._model_path().exists()
