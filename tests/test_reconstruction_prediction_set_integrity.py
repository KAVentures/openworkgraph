"""Synthetic prediction-set integrity checks; no holdout keys or model calls."""
from __future__ import annotations

import json

import pytest

from evals.reconstruction.run_external import _load_resume_predictions, _validate_prediction
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


@pytest.mark.parametrize("returned_case_id", ["another-case", "", 42, None])
def test_model_response_wrong_case_id_is_refused_without_relabeling(returned_case_id):
    prediction = {
        "case_id": returned_case_id,
        "workflows": [{"event_ids": ["task"]}],
    }
    with pytest.raises(ValueError, match="case_id does not match"):
        _validate_prediction(prediction, "case-a", {"task"})
    assert prediction["case_id"] == returned_case_id


def test_model_response_may_omit_case_id_or_match_requested_case():
    for prediction in (
        {"workflows": [{"event_ids": ["task"]}]},
        {"case_id": "case-a", "workflows": [{"event_ids": ["task"]}]},
    ):
        validated = _validate_prediction(prediction, "case-a", {"task"})
        assert validated["case_id"] == "case-a"
        assert validated["workflows"][0]["event_ids"] == ["task"]


@pytest.mark.parametrize("bad_prediction,pattern", [
    ({"case_id": "case-a", "workflows": [{"event_ids": ["stale-event"]}]}, "unknown event ids"),
    ({"case_id": "case-a", "workflows": [
        {"event_ids": ["task"]}, {"event_ids": ["task"]},
    ]}, "multiple workflows"),
    ({"case_id": "case-a", "workflows": "not-a-list"}, "workflows must be a list"),
    ({"case_id": "case-a", "workflows": [{"event_ids": "task"}]}, "event_ids must be a list"),
    ({"case_id": "case-a", "workflows": [], "unassigned_event_ids": ["stale-event"]}, "unknown event ids"),
])
def test_resume_refuses_stale_or_malformed_evidence_membership(tmp_path, bad_prediction, pattern):
    path = _write_jsonl(tmp_path, [bad_prediction])
    before = path.read_bytes()
    with pytest.raises(ValueError, match=pattern):
        _load_resume_predictions(
            path, {"case-a"}, evidence_ids_by_case={"case-a": {"task", "noise"}}
        )
    assert path.read_bytes() == before


def test_resume_valid_current_evidence_keeps_persisted_prediction_unchanged(tmp_path):
    prediction = _prediction("case-a")
    path = _write_jsonl(tmp_path, [prediction])
    before = path.read_bytes()
    result = _load_resume_predictions(
        path, {"case-a"}, evidence_ids_by_case={"case-a": {"task", "noise"}}
    )
    assert result["case-a"] == prediction
    assert "prediction_id" not in result["case-a"]["workflows"][0]
    assert path.read_bytes() == before


def test_resume_rejects_missing_current_evidence_mapping(tmp_path):
    path = _write_jsonl(tmp_path, [_prediction("case-a")])
    with pytest.raises(ValueError, match="lack current evidence"):
        _load_resume_predictions(path, {"case-a"}, evidence_ids_by_case={})


def test_runner_refuses_stale_resume_before_model_request(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    path = tmp_path / "synthetic-model.predictions.jsonl"
    stale = _prediction("case-a")
    stale["workflows"][0]["event_ids"] = ["event-from-an-older-corpus"]
    path.write_text(json.dumps(stale) + "\n", encoding="utf-8")
    before = path.read_bytes()

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("a stale resume must never trigger a model request")

    monkeypatch.setattr(run_external, "_request", unexpected_request)
    with pytest.raises(ValueError, match="invalid for current evidence"):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(tmp_path), "--resume",
        ])
    assert path.read_bytes() == before
    assert not (tmp_path / "synthetic-model.report.json").exists()
    assert not (tmp_path / "synthetic-model.metadata.json").exists()


@pytest.mark.parametrize("prediction,pattern", [
    ({"workflows": [{"event_ids": ["task", "task"]}]}, "duplicate event ids"),
    ({"workflows": [{"event_ids": [7]}]}, "nonempty string event ids"),
    ({"workflows": [{"event_ids": [""]}]}, "nonempty string event ids"),
    ({"workflows": [{"event_ids": ["task"], "ordered_event_ids": ["noise"]}]},
     "outside its workflow"),
    ({"workflows": [{"event_ids": ["task"], "automation_relevant_event_ids": ["noise"]}]},
     "outside its workflow"),
    ({"workflows": [{"event_ids": ["task"], "ui_mechanic_event_ids": ["noise"]}]},
     "outside its workflow"),
    ({"workflows": [{"event_ids": ["task"]}], "unassigned_event_ids": ["task"]},
     "both assigned and unassigned"),
    ({"workflows": [], "unassigned_event_ids": ["noise", "noise"]},
     "duplicate event ids"),
    ({"workflows": [], "unassigned_event_ids": [None]},
     "nonempty string event ids"),
])
def test_model_response_rejects_inconsistent_event_membership(prediction, pattern):
    with pytest.raises(ValueError, match=pattern):
        _validate_prediction(prediction, "case-a", {"task", "noise"})


def test_model_response_accepts_partial_order_and_omitted_optional_lists():
    prediction = {
        "workflows": [
            {"event_ids": ["task", "noise"], "ordered_event_ids": ["noise"]},
            {"event_ids": []},
        ],
        "unassigned_event_ids": [],
    }
    validated = _validate_prediction(prediction, "case-a", {"task", "noise"})
    assert validated["case_id"] == "case-a"
    assert validated["workflows"][0]["ordered_event_ids"] == ["noise"]


def test_resume_rejects_contradictory_membership_without_modifying_file(tmp_path):
    prediction = {
        "case_id": "case-a",
        "workflows": [{"event_ids": ["task"]}],
        "unassigned_event_ids": ["task"],
    }
    path = _write_jsonl(tmp_path, [prediction])
    before = path.read_bytes()
    with pytest.raises(ValueError, match="both assigned and unassigned"):
        _load_resume_predictions(
            path, {"case-a"}, evidence_ids_by_case={"case-a": {"task", "noise"}}
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("prediction,pattern", [
    ({"workflows": [], "insufficient_evidence": "false"}, "insufficient_evidence must be a boolean"),
    ({"workflows": [], "insufficient_evidence": 0}, "insufficient_evidence must be a boolean"),
    ({"workflows": [], "insufficient_evidence": None}, "insufficient_evidence must be a boolean"),
    ({"workflows": [], "uncertainty": "unknown"}, "uncertainty must be an object"),
    ({"workflows": [], "uncertainty": None}, "uncertainty must be an object"),
    ({"workflows": [], "uncertainty": {"membership": "false"}}, "uncertainty.membership must be a boolean"),
    ({"workflows": [], "uncertainty": {"business_rule": 0}}, "uncertainty.business_rule must be a boolean"),
    ({"workflows": [], "uncertainty": {"outcome": None}}, "uncertainty.outcome must be a boolean"),
    ({"workflows": [{"prediction_id": "", "event_ids": ["task"]}]},
     "nonempty string prediction_id"),
    ({"workflows": [{"prediction_id": 7, "event_ids": ["task"]}]},
     "nonempty string prediction_id"),
    ({"workflows": [
        {"prediction_id": "same", "event_ids": ["task"]},
        {"prediction_id": "same", "event_ids": ["noise"]},
    ]}, "duplicate workflow prediction_id"),
])
def test_model_response_rejects_ambiguous_uncertainty_or_workflow_identity(prediction, pattern):
    with pytest.raises(ValueError, match=pattern):
        _validate_prediction(prediction, "case-a", {"task", "noise"})


def test_legacy_omissions_and_explicit_boolean_uncertainty_remain_valid():
    legacy = _validate_prediction(
        {"workflows": [{"event_ids": ["task"]}, {"event_ids": ["noise"]}]},
        "case-a", {"task", "noise"},
    )
    assert [row["prediction_id"] for row in legacy["workflows"]] == ["workflow_1", "workflow_2"]
    assert legacy["insufficient_evidence"] is False
    assert "uncertainty" not in legacy

    explicit = _validate_prediction({
        "workflows": [{"prediction_id": "first", "event_ids": ["task"]}],
        "insufficient_evidence": False,
        "uncertainty": {"membership": False, "business_rule": True, "outcome": True},
    }, "case-a", {"task", "noise"})
    assert explicit["workflows"][0]["prediction_id"] == "first"
    assert explicit["uncertainty"]["membership"] is False
    assert explicit["uncertainty"]["business_rule"] is True


def test_resume_refuses_coerced_uncertainty_before_any_new_request(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    path = tmp_path / "synthetic-model.predictions.jsonl"
    invalid = _prediction("case-a")
    invalid["insufficient_evidence"] = "false"
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    before = path.read_bytes()

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("invalid resume must not request a model"))
    ))
    with pytest.raises(ValueError, match="insufficient_evidence must be a boolean"):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(tmp_path), "--resume",
        ])
    assert path.read_bytes() == before
    assert not (tmp_path / "synthetic-model.report.json").exists()
    assert not (tmp_path / "synthetic-model.metadata.json").exists()


def _blind_case():
    return {
        "case_id": "case-a",
        "presented_evidence": [
            {"event_id": "task", "resource_ref": "ticket-1", "action": "review"},
            {"event_id": "noise", "surface": "unrelated-tab"},
        ],
    }


def _bound_prediction(case):
    from evals.reconstruction.run_external import (
        BLIND_EVIDENCE_DIGEST_FIELD, _blind_evidence_digest,
    )

    return {
        **_prediction(case["case_id"]),
        BLIND_EVIDENCE_DIGEST_FIELD: _blind_evidence_digest(
            case["case_id"], case["presented_evidence"]
        ),
    }


def test_fake_provider_response_receives_local_blind_evidence_digest():
    from evals.reconstruction.run_external import (
        BLIND_EVIDENCE_DIGEST_FIELD, _blind_evidence_digest, _request,
    )

    case = _blind_case()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": "synthetic-only",
                "model": "synthetic-model",
                "choices": [{"message": {"content": json.dumps(_prediction("case-a"))}}],
            }

    class FakeClient:
        def post(self, *args, **kwargs):
            assert kwargs["json"]["messages"][1]["content"]
            return FakeResponse()

    prediction, metadata = _request(
        FakeClient(), base_url="https://invalid.example", api_key="not-real",
        model="synthetic-model", case=case, temperature=None, timeout=1.0,
    )
    assert prediction[BLIND_EVIDENCE_DIGEST_FIELD] == _blind_evidence_digest(
        case["case_id"], case["presented_evidence"]
    )
    assert metadata["response_id"] == "synthetic-only"


def test_blind_evidence_digest_ignores_object_key_order_but_not_event_order():
    from evals.reconstruction.run_external import _blind_evidence_digest

    case = _blind_case()
    first = _blind_evidence_digest(case["case_id"], case["presented_evidence"])
    rekeyed = [dict(reversed(list(event.items()))) for event in case["presented_evidence"]]
    assert _blind_evidence_digest(case["case_id"], rekeyed) == first
    assert _blind_evidence_digest(case["case_id"], list(reversed(rekeyed))) != first
    assert _blind_evidence_digest("another-case", case["presented_evidence"]) != first


def test_resume_rejects_changed_blind_evidence_with_identical_event_ids(tmp_path):
    case = _blind_case()
    path = _write_jsonl(tmp_path, [_bound_prediction(case)])
    before = path.read_bytes()
    changed = [dict(event) for event in case["presented_evidence"]]
    changed[0]["resource_ref"] = "ticket-2"
    assert {e["event_id"] for e in changed} == {
        e["event_id"] for e in case["presented_evidence"]
    }

    with pytest.raises(ValueError, match="blind evidence changed"):
        _load_resume_predictions(
            path, {"case-a"},
            evidence_ids_by_case={"case-a": {"task", "noise"}},
            evidence_by_case={"case-a": changed},
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("digest", ["wrong", "f" * 64, None, 42])
def test_resume_rejects_malformed_or_tampered_digest(tmp_path, digest):
    from evals.reconstruction.run_external import BLIND_EVIDENCE_DIGEST_FIELD

    case = _blind_case()
    row = _bound_prediction(case)
    row[BLIND_EVIDENCE_DIGEST_FIELD] = digest
    path = _write_jsonl(tmp_path, [row])
    with pytest.raises(ValueError, match="malformed blind-evidence digest|blind evidence changed"):
        _load_resume_predictions(
            path, {"case-a"},
            evidence_ids_by_case={"case-a": {"task", "noise"}},
            evidence_by_case={"case-a": case["presented_evidence"]},
        )


def test_resume_requires_current_blind_evidence_when_digest_is_present(tmp_path):
    case = _blind_case()
    path = _write_jsonl(tmp_path, [_bound_prediction(case)])
    with pytest.raises(ValueError, match="lack blind evidence"):
        _load_resume_predictions(
            path, {"case-a"}, evidence_ids_by_case={"case-a": {"task", "noise"}},
        )


def test_legacy_digestless_resume_stays_supported_but_unattested(tmp_path):
    case = _blind_case()
    row = _prediction("case-a")
    path = _write_jsonl(tmp_path, [row])
    changed = [dict(event) for event in case["presented_evidence"]]
    changed[0]["action"] = "changed"
    assert _load_resume_predictions(
        path, {"case-a"},
        evidence_ids_by_case={"case-a": {"task", "noise"}},
        evidence_by_case={"case-a": changed},
    )["case-a"] == row


def test_model_cannot_forge_runner_evidence_digest():
    from evals.reconstruction.run_external import BLIND_EVIDENCE_DIGEST_FIELD

    prediction = _prediction("case-a")
    prediction[BLIND_EVIDENCE_DIGEST_FIELD] = "f" * 64
    with pytest.raises(ValueError, match="must not supply runner blind-evidence digest"):
        _validate_prediction(prediction, "case-a", {"task", "noise"})


def test_runner_rejects_changed_same_id_resume_before_request(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    case = _blind_case()
    path = _write_jsonl(tmp_path, [_bound_prediction(case)])
    original_bytes = path.read_bytes()
    path.rename(tmp_path / "synthetic-model.predictions.jsonl")
    path = tmp_path / "synthetic-model.predictions.jsonl"
    changed = {**case, "presented_evidence": [dict(e) for e in case["presented_evidence"]]}
    changed["presented_evidence"][0]["action"] = "reassigned"
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [changed])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("changed resume must not call model"))
    ))
    with pytest.raises(ValueError, match="blind evidence changed"):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(tmp_path), "--resume",
        ])
    assert path.read_bytes() == original_bytes
    assert not (tmp_path / "synthetic-model.report.json").exists()
    assert not (tmp_path / "synthetic-model.metadata.json").exists()


def _resume_manifest(*, cases=2):
    return {
        "model_requested": "synthetic-model",
        "base_url": "https://api.openai.com/v1",
        "temperature": None,
        "split": "all",
        "cases": cases,
        "revision": "synthetic-prior-revision",
        "responses": [{"case_id": "case-a", "response_id": "prior-receipt"}],
    }


@pytest.mark.parametrize("field,changed", [
    ("model_requested", "another-model"),
    ("base_url", "https://different.invalid/v1"),
    ("temperature", 0.4),
    ("split", "holdout"),
    ("cases", 2),
])
def test_runner_refuses_changed_resume_settings_before_request_or_write(
    tmp_path, monkeypatch, field, changed,
):
    from evals.reconstruction import run_external

    predictions_path = _write_jsonl(tmp_path, [_prediction("case-a")])
    predictions_path = predictions_path.rename(tmp_path / "synthetic-model.predictions.jsonl")
    metadata_path = tmp_path / "synthetic-model.metadata.json"
    manifest = _resume_manifest(cases=1)
    manifest[field] = changed
    metadata_path.write_text(json.dumps(manifest), encoding="utf-8")
    before_predictions = predictions_path.read_bytes()
    before_metadata = metadata_path.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("mismatched resume must not call provider"))
    ))
    with pytest.raises(ValueError, match=f"resume metadata mismatch for {field}"):
        run_external.main(["--model", "synthetic-model", "--base-url", "https://api.openai.com/v1", "--api-key-env", "OPENAI_API_KEY", "--output-dir", str(tmp_path), "--resume"])
    assert predictions_path.read_bytes() == before_predictions
    assert metadata_path.read_bytes() == before_metadata
    assert not (tmp_path / "synthetic-model.report.json").exists()


def test_runner_resume_preserves_prior_receipt_and_appends_new_one(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    _write_jsonl(tmp_path, [_prediction("case-a")]).rename(
        tmp_path / "synthetic-model.predictions.jsonl"
    )
    metadata_path = tmp_path / "synthetic-model.metadata.json"
    metadata_path.write_text(json.dumps(_resume_manifest()), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a"), _case("case-b")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    called = []

    def fake_request(*args, **kwargs):
        called.append(kwargs["case"]["case_id"])
        return _prediction("case-b"), {"response_id": "new-receipt", "response_model": "synthetic-model"}

    monkeypatch.setattr(run_external, "_request", fake_request)
    run_external.main(["--model", "synthetic-model", "--base-url", "https://api.openai.com/v1", "--api-key-env", "OPENAI_API_KEY", "--output-dir", str(tmp_path), "--resume"])
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert called == ["case-b"]
    assert [(row["case_id"], row["response_id"]) for row in saved["responses"]] == [
        ("case-a", "prior-receipt"), ("case-b", "new-receipt"),
    ]
    assert saved["resume_provenance_unverified"] is False


def test_interrupted_legacy_resume_without_manifest_is_marked_unattested(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    _write_jsonl(tmp_path, [_prediction("case-a")]).rename(
        tmp_path / "synthetic-model.predictions.jsonl"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a"), _case("case-b")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    called = []

    def fake_request(*args, **kwargs):
        called.append(kwargs["case"]["case_id"])
        return _prediction("case-b"), {"response_id": "only-new-receipt"}

    monkeypatch.setattr(run_external, "_request", fake_request)
    run_external.main(["--model", "synthetic-model", "--base-url", "https://api.openai.com/v1", "--api-key-env", "OPENAI_API_KEY", "--output-dir", str(tmp_path), "--resume"])
    saved = json.loads((tmp_path / "synthetic-model.metadata.json").read_text(encoding="utf-8"))
    assert called == ["case-b"]
    assert saved["resume_provenance_unverified"] is True
    assert [row["case_id"] for row in saved["responses"]] == ["case-b"]


@pytest.mark.parametrize("responses", [
    "not-a-list",
    [{"case_id": "case-a"}, {"case_id": "case-a"}],
    [{"case_id": "other-case"}],
    [42],
])
def test_resume_rejects_corrupt_or_unmatched_prior_receipts(tmp_path, responses):
    from evals.reconstruction.run_external import _load_resume_metadata

    path = tmp_path / "metadata.json"
    manifest = _resume_manifest(cases=1)
    manifest["responses"] = responses
    path.write_text(json.dumps(manifest), encoding="utf-8")
    expected = {key: manifest[key] for key in (
        "model_requested", "base_url", "temperature", "split", "cases"
    )}
    with pytest.raises(ValueError, match="resume metadata"):
        _load_resume_metadata(path, expected, {"case-a"})


def test_resume_missing_old_receipt_stays_unattested_across_later_resumes(tmp_path):
    from evals.reconstruction.run_external import _load_resume_metadata

    path = tmp_path / "metadata.json"
    manifest = _resume_manifest()
    manifest["responses"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")
    expected = {key: manifest[key] for key in (
        "model_requested", "base_url", "temperature", "split", "cases"
    )}
    rows, unverified = _load_resume_metadata(path, expected, {"case-a"})
    assert rows == []
    assert unverified is True
    manifest["responses"] = [{"case_id": "case-a"}]
    manifest["resume_provenance_unverified"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    _, unverified = _load_resume_metadata(path, expected, {"case-a"})
    assert unverified is True
