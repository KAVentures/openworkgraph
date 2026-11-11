"""Opt-in R22 language-specific holdout quality gate for learned communications intents."""
from __future__ import annotations

import pytest

from shared import communications_quality_gate as gate
from server import communications_review as review


def evaluation(*, english_f1=.92, english_uncertain=.1, swedish_positives=3):
    def group(n, positive, f1, uncertain):
        return {"examples": n, "positive_examples": positive, "f1": f1, "uncertain_rate": uncertain}
    return {"languages": ["en", "sv"], "intents": [
        {"intent": "Refund request", "by_language": {
            "en": group(30, 15, english_f1, english_uncertain),
            "sv": group(25, swedish_positives, .98, .01),
        }},
        {"intent": "Account access", "by_language": {
            "en": group(30, 12, .88, .15),
            "sv": group(25, 10, .90, .10),
        }},
    ]}


def test_language_gate_counts_each_intent_positive_and_negative_support():
    names = ["Refund request", "Account access"]
    good = gate.assess(evaluation(), names, "EN")
    assert good["ready"] is True and good["declared_language"] == "en"
    assert good["evaluated_intents"] == 2
    assert good["quality_estimates_from_reviewer_labelled_holdout_only"] is True
    assert good["independent_external_quality_verified"] is False
    assert good["language_inferred_from_upload"] is False
    bad = gate.assess(evaluation(), names, "sv")
    assert bad["ready"] is False
    assert bad["rejected_intents"] == [{
        "intent": "Refund request", "reason": "insufficient_positive_or_negative_holdout_cases"}]
    assert gate.assess(evaluation(), names + ["Order status"], "en")["rejected_intents"][-1] == {
        "intent": "Order status", "reason": "intent_not_trained_or_not_evaluated"}


def test_missing_language_weak_f1_and_abstention_fail_closed():
    names = ["Refund request", "Account access"]
    assert not gate.assess(evaluation(), names, "de")["ready"]
    low = gate.assess(evaluation(english_f1=.50), names, "en")
    assert low["rejected_intents"][0]["reason"] == "holdout_f1_below_gate"
    abstain = gate.assess(evaluation(english_uncertain=.42), names, "en")
    assert abstain["rejected_intents"][0]["reason"] == "holdout_abstention_above_gate"
    assert gate.assess({"intents": []}, names, "en")["ready"] is False
    assert gate.assess(evaluation(), [], "en")["ready"] is False


@pytest.mark.parametrize("language", ["", "eng-US", "en_US", "sv, en", "<script>", 123, None])
def test_language_selection_is_strict_and_bounded(language):
    with pytest.raises(gate.QualityGateError):
        gate.normalize_language(language)
    assert gate.normalize_language("pt-BR") == "pt-br"


def test_learned_inference_is_optional_and_strict_mode_checks_saved_holdout(monkeypatch):
    model = {
        "format": "openworkgraph.communications-intent-model.v1",
        "intents": {
            n: {"weights": {}, "bias": 0.0, "calibration": {"a": 1.0, "b": 0.0}}
            for n in ("Refund request", "Account access")
        },
        "evaluation": evaluation(),
        "taxonomy_intents": ["Refund request", "Account access"],
        "trained_at": "2026-11-11T01:00:00Z",
        "model_sha256": "a" * 64,
        "skipped_intents": [],
        "training_example_count": 40,
        "thresholds": {"present_at": .7, "absent_at": .3},
        "expires_at": "2099-01-01T00:00:00Z",
    }
    monkeypatch.setattr(review, "_load", lambda: {"settings": {"enabled": True}, "examples": []})
    monkeypatch.setattr(review, "_load_model", lambda: model)
    monkeypatch.setattr(review, "_freshness", lambda *args, **kwargs: (True, None))
    names = ["Refund request", "Account access"]
    normal, metadata = review.learned_classifier(names)
    assert normal("example") == {"Refund request": .5, "Account access": .5}
    assert metadata["holdout_language_gate"] is None
    guarded, metadata = review.learned_classifier(names, require_holdout_language="en")
    assert guarded("example") == normal("example")
    assert metadata["holdout_language_gate"]["ready"] is True
    assert metadata["source_language_independently_verified"] is False
    with pytest.raises(review.ReviewError, match="held-out quality gate refused"):
        review.learned_classifier(names, require_holdout_language="sv")
    with pytest.raises(review.ReviewError, match="Declare a language"):
        review.learned_classifier(names, require_holdout_language="bad_code")
    info = review.model_status(names)
    assert info["holdout_language_gates"]["en"]["ready"] is True
    assert info["holdout_language_gates"]["sv"]["ready"] is False
    assert info["holdout_gate_is_optional"] is True


def test_http_rejects_quality_gate_for_keyword_and_missing_language_quality(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.secure_app import app
    from server import local_auth

    called = []
    def fake_classifier(names, *, require_holdout_language=None):
        called.append(require_holdout_language)
        if require_holdout_language == "sv":
            raise review.ReviewError("Language-specific held-out quality gate refused learned classification")
        return lambda text: {"Refund request": .8}, {"model_sha256": "a" * 64}

    monkeypatch.setattr(review, "learned_classifier", fake_classifier)
    content = b"ticket,text\nC1,Please refund my order\n"
    with TestClient(app) as client:
        owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(),
                 "x-owg-local-processing-confirmed": "true"}
        prefix = "/v1/communications/analyze?format=csv&text_columns=text&thread_column=ticket"
        r = client.post(prefix+"&quality_gate_language=sv", headers=owner, content=content)
        assert r.status_code == 422
        r = client.post(prefix+"&classifier=learned&quality_gate_language=sv", headers=owner, content=content)
        assert r.status_code == 409
        assert called == ["sv"]
        r = client.post(prefix+"&classifier=learned&quality_gate_language=xx_bad",
                        headers=owner, content=content)
        assert r.status_code == 409
        assert called[-1] == "xx_bad"
