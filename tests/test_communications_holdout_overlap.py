"""R22: lexical-overlap diagnostic and screened language quality."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from shared import communications_holdout_overlap as overlap
from server import communications_review as review


def row(text, language="en", labels=None):
    return {"text": text, "language": language,
            "labels": ["Refund"] if labels is None else labels}


def test_normalization_near_match_and_cross_language_detection():
    template = "please review the outstanding refund for this purchase and confirm the expected payment date today"
    training = [row("Order 12345: REFUND requested!"), row(template), row("ignored", labels="not labelled")]
    holdout = [
        row("order 98765 refund requested", "sv"),
        row(template + " thanks", "en"),
        row("a completely unrelated enquiry about a locked account", "en"),
        row("unlabelled", labels="not labelled"),
    ]
    result, remaining = overlap.audit(training, holdout)
    assert result["complete"] is True
    assert result["training_examples"] == 2
    assert result["holdout_examples"] == 3
    assert result["normalized_exact_overlap"] == 1
    assert result["near_lexical_overlap"] == 1
    assert result["no_detected_overlap"] == 1
    assert result["by_language"]["sv"]["normalized_exact_overlap"] == 1
    assert result["by_language"]["en"]["near_lexical_overlap"] == 1
    assert remaining == [holdout[2]]
    assert template not in json.dumps(result)
    assert "98765" not in json.dumps(result)
    assert result["thread_or_semantic_independence_verified"] is False
    assert overlap.audit(list(reversed(training)), holdout) == (result, remaining)


def test_budget_exhaustion_and_oversized_input_never_report_partial_clean_score():
    a = row("a b c d e f g h i j k l")
    b = row("a b c d e f g h i j k x")
    for training, held, budget in (
        ([a], [b], 0),
        ([row("a" * 4001)], [b], overlap.MAX_WORK),
        ([a] * 2000, [b], overlap.MAX_WORK),
    ):
        result, eligible = overlap.audit(training, held, max_work=budget)
        assert result["complete"] is False
        assert result["no_detected_overlap"] is None
        assert result["normalized_exact_overlap"] is None
        assert result["by_language"] == {}
        assert eligible == []


def test_short_distinct_text_is_not_called_near_duplicate():
    result, eligible = overlap.audit([row("please refund")], [row("please refund today")])
    assert result["near_lexical_overlap"] == 0
    assert len(eligible) == 1


def _evaluation(n=20, positives=10, f1=.95):
    return {"languages": ["en"], "intents": [{"intent": "Refund", "by_language": {
        "en": {"examples": n, "positive_examples": positives, "f1": f1,
               "uncertain_rate": .05}}}]}


def test_gate_uses_screened_support_not_apparently_good_full_score():
    full = _evaluation()
    assert review._holdout_gate(full, ["Refund"], "en")["evaluation_basis"] == "legacy_unscreened_holdout"
    full["lexical_overlap"] = {"complete": True}
    full["without_detected_overlap"] = _evaluation(n=8, positives=4)
    refused = review._holdout_gate(full, ["Refund"], "en")
    assert refused["ready"] is False
    assert refused["evaluation_basis"] == "holdout_without_detected_lexical_overlap"
    full["without_detected_overlap"] = _evaluation()
    assert review._holdout_gate(full, ["Refund"], "en")["ready"] is True
    full["lexical_overlap"]["complete"] = False
    assert review._holdout_gate(full, ["Refund"], "en")["ready"] is False


def test_training_persists_both_evaluations_without_reassigning_examples(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "DATA_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=10)).isoformat()
    data = review._empty()
    data["settings"] = {"enabled": True, "retention_days": 10}
    examples = [
        {**row("refund order 1234"), "example_id": "train-1", "split": "train"},
        {**row("refund order 5678"), "example_id": "held-1", "split": "holdout"},
        {**row("different account access issue", labels=[]),
         "example_id": "held-2", "split": "holdout"},
    ]
    for item in examples:
        item["expires_at"] = expires
    data["examples"] = examples
    review._write(review._path(), data)
    trained, evaluated = [], []

    def fake_train(rows, intents):
        trained.extend(e["example_id"] for e in rows)
        return {"intents": {"Refund": {}}, "skipped_intents": [],
                "training_example_count": len(rows), "thresholds": {}}

    def fake_evaluate(model, rows):
        evaluated.append([e["example_id"] for e in rows])
        return {**_evaluation(), "holdout_examples": len(rows)}

    monkeypatch.setattr(review.learning, "train", fake_train)
    monkeypatch.setattr(review.learning, "evaluate", fake_evaluate)
    status = review.train_model(["Refund"])
    assert trained == ["train-1"]
    assert evaluated == [["held-1", "held-2"], ["held-2"]]
    report = status["evaluation"]
    assert report["holdout_examples"] == 2
    assert report["without_detected_overlap"]["holdout_examples"] == 1
    assert report["lexical_overlap"]["normalized_exact_overlap"] == 1
    assert review._load()["examples"] == examples
    assert "refund order" not in json.dumps(report)
    review.configure({"enabled": False})
    assert review.model_status()["trained"] is False
