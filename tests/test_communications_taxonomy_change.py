"""R22 training-only taxonomy preview, comparison integrity and local HTTP boundary."""
from __future__ import annotations

import copy
import json

import pytest

from shared import communications_mining as mining
from shared import communications_taxonomy_change as change
from server import communications_review as review
from server import communications_taxonomy_change as service


def taxonomy(refund=("refund",), cancel=("cancel",)):
    return {"format": mining.FORMAT, "intents": [
        {"name": "Refund", "terms": list(refund)}, {"name": "Cancel", "terms": list(cancel)}]}


CURRENT = taxonomy()
CANDIDATE = taxonomy(("refund", "money back"))


def example(i, text, labels, language="en", split="train"):
    return {"example_id": "id-" + str(i), "text": text, "labels": labels, "split": split,
            "language": language, "expires_at": "2099-01-01T00:00:00+00:00"}


def sample():
    return [example(1, "money back PRIVATE_SENTINEL", ["Refund"]),
            example(2, "money back cancel", ["Cancel"]),
            example(3, "refund cancel", ["Refund", "Cancel"], "sv"),
            example(4, "hello", []), example(5, "money back", None),
            example(6, "SEALED_HOLDOUT", ["Refund"], split="holdout")]


def test_exact_paired_transitions_multi_intent_and_no_text():
    report = change.evaluate(CURRENT, CANDIDATE, sample(), {"enabled": True})
    overall = report["overall"]
    assert overall["messages"] == 4
    assert overall["newly_correct"] == 1
    assert overall["newly_not_correct"] == 1
    assert overall["changed_decisions"] == 2
    assert overall["baseline_strict_accuracy"] == .5
    assert overall["candidate_strict_accuracy"] == .5
    assert overall["baseline_unclassified"] == 2
    assert overall["candidate_unclassified"] == 1
    assert sum(x["messages"] for x in overall["correctness_transitions"]) == 4
    assert sum(x["messages"] for x in overall["primary_transitions"]) == 4
    refund = next(x for x in overall["per_intent"] if x["intent"] == "Refund")
    assert refund["baseline"]["positive_support"] == 2
    assert refund["baseline"]["recall"] == 0
    assert refund["candidate"]["recall"] == .5
    assert refund["candidate"]["false_positive"] == 1
    assert report["term_changes"] == [{"intent": "Cancel", "added_normalized_terms": 0, "removed_normalized_terms": 0},
                                      {"intent": "Refund", "added_normalized_terms": 1, "removed_normalized_terms": 0}]
    rendered = json.dumps(report)
    for value in ("PRIVATE_SENTINEL", "SEALED_HOLDOUT", "id-1", "money back"):
        assert value not in rendered
    assert not report["heldout_text_used"] and not report["external_validation"]


def test_holdout_and_unlabelled_text_never_classified_or_pinned(monkeypatch):
    original = mining.classify
    calls = []
    def classify(text, intents):
        calls.append(text)
        assert "SEALED_HOLDOUT" not in text
        return original(text, intents)
    monkeypatch.setattr(mining, "classify", classify)
    rows = sample()
    a = change.evaluate(CURRENT, CANDIDATE, rows, {"enabled": True})
    assert len(calls) == 8
    rows[-1]["text"] = "different holdout"
    rows[-1]["labels"] = ["retired unknown"]
    rows[-2]["text"] = "different unlabelled text"
    b = change.evaluate(CURRENT, CANDIDATE, rows, {"enabled": True})
    assert a == b


def test_ambiguous_set_change_not_hidden_on_diagonal():
    current = {"format": mining.FORMAT, "intents": [{"name": n, "terms": [term]}
               for n, term in [("A", "x"), ("B", "x"), ("C", "y")]]}
    candidate = {"format": mining.FORMAT, "intents": [{"name": n, "terms": [term]}
                 for n, term in [("A", "x"), ("B", "y"), ("C", "x")]]}
    report = change.evaluate(current, candidate, [example(1, "x", ["A"])], {})
    assert report["overall"]["changed_decisions"] == 1
    assert report["overall"]["primary_transitions"] == [{"baseline": "ambiguous", "candidate": "ambiguous", "messages": 1}]


def test_undefined_support_and_language_totals():
    report = change.evaluate(CURRENT, CANDIDATE, [example(i, "hello", [], f"l{i}") for i in range(35)], {})
    assert report["language_groups_total"] == 35
    assert report["language_groups_displayed"] == 30
    assert report["overall"]["messages"] == 35
    assert all(x["baseline"]["recall"] is None for x in report["overall"]["per_intent"])


@pytest.mark.parametrize("kind", ["names", "label", "duplicate", "empty", "work"])
def test_invalid_or_incomplete_basis_refused(kind, monkeypatch):
    rows = sample()
    candidate = copy.deepcopy(CANDIDATE)
    if kind == "names":
        candidate["intents"][0]["name"] = "New"
    elif kind == "label":
        rows[0]["labels"] = ["Retired"]
    elif kind == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif kind == "empty":
        rows = rows[-2:]
    else:
        monkeypatch.setattr(change, "MAX_WORK", 1)
    with pytest.raises(mining.CommunicationsError):
        change.evaluate(CURRENT, candidate, rows, {})


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    review.configure({"enabled": True, "acknowledgement_version": review.ACK_VERSION, "retention_days": 30})
    monkeypatch.setattr(review, "_split", lambda _: "train")
    review.add_examples([{"text": "money back", "language": "en"}])
    eid = review.listing()["examples"][0]["example_id"]
    review.label({"example_id": eid, "labels": ["Refund"]}, ["Refund", "Cancel"])
    state = {"taxonomy": CURRENT}
    return eid, lambda: state["taxonomy"], lambda t: state.update(taxonomy=t)


def test_preview_is_read_only_then_exact_apply(local):
    eid, load, save = local
    before = review.listing()
    report = service.preview(load, CANDIDATE)
    assert load() == CURRENT and review.listing() == before
    result = service.apply(load, save, CANDIDATE, report["preview_sha256"])
    assert load() == CANDIDATE and review.listing() == before
    assert result["applied_preview_sha256"] == report["preview_sha256"]
    with pytest.raises(review.ReviewError):
        service.apply(load, save, CANDIDATE, report["preview_sha256"])


@pytest.mark.parametrize("change_kind", ["label", "taxonomy", "candidate", "delete", "revoke"])
def test_stale_preview_refused_without_write(local, change_kind):
    eid, load, save = local
    report = service.preview(load, CANDIDATE)
    candidate = CANDIDATE
    if change_kind == "label":
        review.label({"example_id": eid, "labels": []}, ["Refund", "Cancel"])
    elif change_kind == "taxonomy":
        save(taxonomy(("refund", "return")))
    elif change_kind == "candidate":
        candidate = taxonomy(("refund", "reimburse"))
    elif change_kind == "delete":
        review.delete([eid])
    else:
        review.configure({"enabled": False})
    before = load()
    with pytest.raises((review.ReviewError, mining.CommunicationsError)):
        service.apply(load, save, candidate, report["preview_sha256"])
    assert load() == before


def test_consent_revocation_during_preview_withholds_report(local, monkeypatch):
    _, load, _ = local
    original = mining.classify
    def revoked(text, intents):
        review.configure({"enabled": False})
        return original(text, intents)
    monkeypatch.setattr(mining, "classify", revoked)
    with pytest.raises(review.ReviewError):
        service.preview(load, CANDIDATE)


def test_authenticated_routes_no_store_and_explicit_apply(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import communications_routes as routes
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "local-session")
    app = FastAPI()
    app.include_router(routes.router)
    owner = {"Authorization": "OWG-Session local-session"}
    path = "/v1/communications/taxonomy"
    with TestClient(app) as client:
        assert client.post(path + "/preview", json=CANDIDATE).status_code == 403
        assert client.post(path + "/preview", json=CANDIDATE, headers={"Authorization": "Bearer token"}).status_code == 403
        assert client.put(path, json=CURRENT, headers=owner).status_code == 200
        assert client.post(path + "/preview", json=CANDIDATE, headers=owner).status_code == 409
        review.configure({"enabled": True, "acknowledgement_version": review.ACK_VERSION, "retention_days": 30})
        monkeypatch.setattr(review, "_split", lambda _: "train")
        review.add_examples([{"text": "money back"}])
        eid = review.listing()["examples"][0]["example_id"]
        review.label({"example_id": eid, "labels": ["Refund"]}, ["Refund", "Cancel"])
        preview = client.post(path + "/preview", json=CANDIDATE, headers=owner)
        assert preview.status_code == 200, preview.text
        assert preview.headers["cache-control"] == "no-store"
        body = {"taxonomy": CANDIDATE, "preview_sha256": preview.json()["preview_sha256"], "confirm": False}
        assert client.post(path + "/apply-preview", json=body, headers=owner).status_code == 422
        body["confirm"] = True
        applied = client.post(path + "/apply-preview", json=body, headers=owner)
        assert applied.status_code == 200, applied.text
        assert applied.headers["cache-control"] == "no-store"
        assert client.get(path, headers=owner).json()["taxonomy"] == CANDIDATE
        assert client.post(path + "/apply-preview", json=body, headers=owner).status_code == 409
