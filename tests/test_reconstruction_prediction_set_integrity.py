"""Synthetic prediction-set integrity checks; no holdout keys or model calls."""
from __future__ import annotations

import json

import pytest

from evals.reconstruction.run_external import _load_resume_predictions
from evals.reconstruction.scoring import aggregate


def _case(cid: str) -> dict:
    return {
        "case_id": cid,
        "family_id": cid,
        "presented_evidence": [{"event_id": "task"}, {"event_id": "noise"}],
        "ground_truth": {
            "workflows": [{"event_ids": ["task"]}],
            "noise_event_ids": ["noise"],
            "requires_uncertainty": False,
            "uncertainty_dimensions": [],
        },
    }


def _prediction(cid: str, *, correct: bool = True) -> dict:
    return {
        "case_id": cid,
        "workflows": [{"event_ids": ["task"] if correct else ["noise"]}],
        "unassigned_event_ids": ["noise"] if correct else ["task"],
        "insufficient_evidence": False,
    }


def _write_jsonl(tmp_path, rows):
    path = tmp_path / "predictions.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def test_exact_prediction_set_scores_independently_of_input_row_order():
    cases = [_case("case-a"), _case("case-b")]
    report = aggregate(cases, [_prediction("case-b"), _prediction("case-a")])
    assert report["cases"] == 2
    assert report["workflow_assignment_f1"] == 1.0
    assert [row["case_id"] for row in report["per_case"]] == ["case-a", "case-b"]


def test_duplicate_prediction_cannot_replace_prior_result():
    cases = [_case("case-a")]
    with pytest.raises(ValueError, match="duplicate prediction"):
        aggregate(cases, [_prediction("case-a"), _prediction("case-a", correct=False)])


def test_unexpected_prediction_cannot_cross_selected_split():
    with pytest.raises(ValueError, match="unexpected predictions"):
        aggregate([_case("holdout-a")], [
            _prediction("holdout-a"), _prediction("development-b")
        ])


def test_missing_prediction_is_still_rejected():
    with pytest.raises(ValueError, match="missing predictions"):
        aggregate([_case("case-a"), _case("case-b")], [_prediction("case-a")])


@pytest.mark.parametrize("cases,predictions,pattern", [
    ([], [], "nonempty string case_id"),
    ([_case("same"), _case("same")], [_prediction("same")], "duplicate case_id"),
    ([_case("")], [_prediction("")], "nonempty string case_id"),
    ([_case("case-a")], [{"case_id": "", "workflows": []}], "nonempty string case_id"),
    ([_case("case-a")], [{"case_id": 7, "workflows": []}], "nonempty string case_id"),
])
def test_empty_or_ambiguous_case_identity_is_rejected(cases, predictions, pattern):
    with pytest.raises(ValueError, match=pattern):
        aggregate(cases, predictions)


def test_resume_loads_exact_selected_rows_without_reordering(tmp_path):
    path = _write_jsonl(tmp_path, [_prediction("case-b"), _prediction("case-a")])
    result = _load_resume_predictions(path, {"case-a", "case-b"})
    assert list(result) == ["case-b", "case-a"]
    assert result["case-a"]["case_id"] == "case-a"


def test_resume_rejects_duplicate_rows_before_any_new_model_request(tmp_path):
    path = _write_jsonl(tmp_path, [_prediction("case-a"), _prediction("case-a", correct=False)])
    with pytest.raises(ValueError, match="duplicate case_id"):
        _load_resume_predictions(path, {"case-a"})


def test_resume_rejects_rows_outside_selected_split(tmp_path):
    path = _write_jsonl(tmp_path, [_prediction("holdout-a"), _prediction("development-b")])
    with pytest.raises(ValueError, match="outside selected split"):
        _load_resume_predictions(path, {"holdout-a"})


@pytest.mark.parametrize("bad_row", [[], {"case_id": ""}, {"case_id": 12}])
def test_resume_rejects_nonobject_or_invalid_case_ids(tmp_path, bad_row):
    path = _write_jsonl(tmp_path, [bad_row])
    with pytest.raises(ValueError, match="must be an object|invalid case_id"):
        _load_resume_predictions(path, {"case-a"})


def test_resume_does_not_recover_from_truncated_jsonl_silently(tmp_path):
    path = tmp_path / "predictions.jsonl"
    path.write_text('{"case_id":"case-a"', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _load_resume_predictions(path, {"case-a"})
