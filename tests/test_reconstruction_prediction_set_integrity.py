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

        @property
        def text(self):
            return json.dumps(self.json())

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


def test_resume_refuses_unterminated_jsonl_before_spending_or_mutating(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    path = tmp_path / "synthetic-model.predictions.jsonl"
    path.write_text(json.dumps(_prediction("case-a")), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a"), _case("case-b")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("unterminated resume must not call provider"))
    ))
    with pytest.raises(ValueError, match="must end with a newline"):
        run_external.main([
            "--model", "synthetic-model", "--base-url", "https://api.openai.com/v1",
            "--output-dir", str(tmp_path), "--resume",
        ])
    assert path.read_bytes() == before
    assert not (tmp_path / "synthetic-model.metadata.json").exists()
    assert not (tmp_path / "synthetic-model.report.json").exists()


def test_completed_unterminated_jsonl_resume_remains_readable(tmp_path, monkeypatch):
    from evals.reconstruction import run_external

    path = tmp_path / "synthetic-model.predictions.jsonl"
    path.write_text(json.dumps(_prediction("case-a")), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("completed resume must not call provider"))
    ))
    run_external.main([
        "--model", "synthetic-model", "--base-url", "https://api.openai.com/v1",
        "--output-dir", str(tmp_path), "--resume",
    ])
    assert path.read_bytes() == before
    assert (tmp_path / "synthetic-model.report.json").exists()
    assert json.loads((tmp_path / "synthetic-model.metadata.json").read_text())[
        "resume_provenance_unverified"
    ] is True


@pytest.mark.parametrize("prior_file", ["missing", "empty", "blank"])
def test_resume_refuses_orphan_manifest_before_provider_request(
    tmp_path, monkeypatch, prior_file,
):
    from evals.reconstruction import run_external

    path = tmp_path / "synthetic-model.predictions.jsonl"
    if prior_file != "missing":
        path.write_text("" if prior_file == "empty" else "   ", encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    metadata_path = tmp_path / "synthetic-model.metadata.json"
    metadata_path.write_text(json.dumps(_resume_manifest(cases=1)), encoding="utf-8")
    before_metadata = metadata_path.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [_case("case-a")])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("orphan manifest must not call provider"))
    ))
    with pytest.raises(ValueError, match="metadata exists without matching prediction rows"):
        run_external.main([
            "--model", "synthetic-model", "--base-url", "https://api.openai.com/v1",
            "--output-dir", str(tmp_path), "--resume",
        ])
    assert (path.read_bytes() if path.exists() else None) == before
    assert metadata_path.read_bytes() == before_metadata
    assert not (tmp_path / "synthetic-model.report.json").exists()

@pytest.mark.parametrize("cases,pattern", [
    ([], "at least one case"),
    ([{"case_id": "same", "presented_evidence": []},
      {"case_id": "same", "presented_evidence": []}], "duplicate selected case_id"),
    ([{"case_id": "", "presented_evidence": []}], "nonempty string case_id"),
    ([{"case_id": " ", "presented_evidence": []}], "nonempty string case_id"),
    ([{"case_id": None, "presented_evidence": []}], "nonempty string case_id"),
    ([None], "must be an object"),
    ([{"case_id": "case-a", "presented_evidence": None}], "presented_evidence must be a list"),
    ([{"case_id": "case-a", "presented_evidence": [None]}], "event 1 must be an object"),
    ([{"case_id": "case-a", "presented_evidence": [{"event_id": ""}]}],
     "nonempty string event_id"),
    ([{"case_id": "case-a", "presented_evidence": [{"event_id": 12}]}],
     "nonempty string event_id"),
    ([{"case_id": "case-a", "presented_evidence": [
        {"event_id": "e1"}, {"event_id": "e1"},
    ]}], "duplicate event_id"),
])
def test_selected_blind_corpus_preflight_rejects_ambiguous_identity(cases, pattern):
    from evals.reconstruction.run_external import _validate_selected_blind_cases

    with pytest.raises(ValueError, match=pattern):
        _validate_selected_blind_cases(cases)


def test_selected_blind_corpus_preflight_allows_case_local_ids_and_empty_evidence():
    from evals.reconstruction.run_external import _validate_selected_blind_cases

    cases = [
        {"case_id": "a", "presented_evidence": [
            {"event_id": "e2", "observed_at": "later"},
            {"event_id": "e1", "observed_at": "earlier"},
        ]},
        {"case_id": "b", "presented_evidence": [{"event_id": "e1"}]},
        {"case_id": "c", "presented_evidence": []},
    ]
    before = json.dumps(cases)
    assert _validate_selected_blind_cases(cases) is None
    assert json.dumps(cases) == before  # no sorting, inference, or mutation


@pytest.mark.parametrize("cases,pattern", [
    ([_case("same"), _case("same")], "duplicate selected case_id"),
    ([{"case_id": "case-a", "presented_evidence": [
        {"event_id": "e1"}, {"event_id": "e1"},
    ]}], "duplicate event_id"),
    ([{"case_id": "case-a", "presented_evidence": [{"event_id": None}]}],
     "nonempty string event_id"),
])
def test_runner_rejects_invalid_selected_blind_corpus_before_requests_or_files(
    tmp_path, monkeypatch, cases, pattern,
):
    from evals.reconstruction import run_external

    output_dir = tmp_path / "not-yet-created"
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: cases)
    monkeypatch.setattr(run_external, "split_cases", lambda selected, split: selected)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("invalid corpus must not request a model"))
    ))
    with pytest.raises(ValueError, match=pattern):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(output_dir),
        ])
    assert not output_dir.exists()



@pytest.mark.parametrize("raw", [
    '{"case_id":"a","case_id":"b","workflows":[]}',
    '{"case_id":"a","workflows":[{"event_ids":["e1"],"event_ids":["e2"]}]}',
    '{"case_id":"a","workflows":[],"uncertainty":{"membership":true,"membership":false}}',
])
def test_model_json_rejects_duplicate_keys_at_any_depth(raw):
    from evals.reconstruction.run_external import _extract_json

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        _extract_json(raw)


def test_strict_json_accepts_same_field_names_in_distinct_objects():
    from evals.reconstruction.run_external import _strict_json_loads

    assert _strict_json_loads('{"workflows":[{"event_ids":["a"]},{"event_ids":["b"]}]}') == {
        "workflows": [{"event_ids": ["a"]}, {"event_ids": ["b"]}]
    }


def test_fake_provider_duplicate_json_key_is_rejected_before_persistence():
    from evals.reconstruction.run_external import _request

    class FakeResponse:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return json.dumps(self.json())

        def json(self):
            return {"choices": [{"message": {
                "content": '{"case_id":"case-a","case_id":"case-b","workflows":[]}'
            }}]}

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse()

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        _request(
            FakeClient(), base_url="https://invalid.example", api_key="synthetic-only",
            model="synthetic-model", case=_blind_case(), temperature=None, timeout=1.0,
        )


@pytest.mark.parametrize("artifact", ["predictions", "metadata"])
def test_resume_duplicate_json_key_fails_before_provider_or_mutation(
    tmp_path, monkeypatch, artifact,
):
    from evals.reconstruction import run_external

    predictions_path = tmp_path / "synthetic-model.predictions.jsonl"
    metadata_path = tmp_path / "synthetic-model.metadata.json"
    if artifact == "predictions":
        predictions_path.write_text(
            '{"case_id":"case-a","case_id":"case-a","workflows":[]}' + chr(10),
            encoding="utf-8",
        )
    else:
        predictions_path.write_text(
            json.dumps(_prediction("case-a")) + chr(10), encoding="utf-8",
        )
        manifest = _resume_manifest(cases=2)
        metadata_path.write_text(
            json.dumps(manifest)[:-1] + ',"model_requested":"synthetic-model"}',
            encoding="utf-8",
        )
    before_predictions = predictions_path.read_bytes()
    before_metadata = metadata_path.read_bytes() if metadata_path.exists() else None
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(
        run_external, "load_cases", lambda: [_case("case-a"), _case("case-b")]
    )
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("duplicate JSON must not call provider"))
    ))

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(tmp_path), "--resume",
        ])
    assert predictions_path.read_bytes() == before_predictions
    assert (metadata_path.read_bytes() if metadata_path.exists() else None) == before_metadata
    assert not (tmp_path / "synthetic-model.report.json").exists()


@pytest.mark.parametrize("payload", [
    '{"case_id":"case-a","workflows":[],"uncertainty":{"membership":NaN}}',
    '{"case_id":"case-a","workflows":[],"extra":Infinity}',
    '{"case_id":"case-a","workflows":[],"extra":-Infinity}',
    '{"case_id":"case-a","workflows":[],"extra":1e999}',
    '{"case_id":"case-a","workflows":[],"extra":-1e999}',
])
def test_model_json_rejects_nonfinite_numbers_at_any_depth(payload):
    from evals.reconstruction.run_external import _extract_json

    with pytest.raises(ValueError, match="non-finite JSON"):
        _extract_json(payload)


def test_model_json_preserves_finite_fractional_values_and_nested_objects():
    from evals.reconstruction.run_external import _extract_json

    parsed = _extract_json(
        '{"case_id":"case-a","workflows":[],"confidence":0.125,'
        '"extra":{"fraction":1.5e2}}'
    )
    assert parsed["confidence"] == 0.125
    assert parsed["extra"]["fraction"] == 150.0


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_resume_refuses_nonfinite_prediction_before_mutation(tmp_path, invalid):
    from evals.reconstruction.run_external import _load_resume_predictions

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"case_id":"case-a","workflows":[],"extra":' + invalid + '}\n',
        encoding="utf-8",
    )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="non-finite JSON"):
        _load_resume_predictions(path, {"case-a"})
    assert path.read_bytes() == before


def test_resume_manifest_rejects_nonfinite_metadata_without_rewriting(tmp_path):
    from evals.reconstruction.run_external import _load_resume_metadata

    path = tmp_path / "metadata.json"
    path.write_text(
        '{"model_requested":"synthetic","temperature":NaN,"responses":[]}',
        encoding="utf-8",
    )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="non-finite JSON"):
        _load_resume_metadata(
            path, {"model_requested": "synthetic", "temperature": None}, set()
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("flag,value", [
    ("--temperature", "nan"),
    ("--temperature", "inf"),
    ("--temperature", "-0.1"),
    ("--timeout", "nan"),
    ("--timeout", "inf"),
    ("--timeout", "-inf"),
    ("--timeout", "0"),
    ("--timeout", "-1"),
])
def test_invalid_numeric_request_options_fail_before_artifacts_or_provider(
    tmp_path, monkeypatch, flag, value,
):
    from evals.reconstruction import run_external

    output_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(
        run_external, "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid request options must not contact provider")
        ),
    )
    monkeypatch.setattr(
        run_external, "load_cases",
        lambda: (_ for _ in ()).throw(
            AssertionError("invalid request options must not load corpus")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(output_dir),
            flag, value,
        ])
    assert exc.value.code == 2
    assert not output_dir.exists()


@pytest.mark.parametrize("bad", [
    float("nan"),
    float("inf"),
    -float("inf"),
    {"nested": [{"ratio": float("nan")}]},
    {"non_json_key": {1: "ambiguous"}},
    {"tuple": ("python-only",)},
    object(),
])
def test_selected_blind_evidence_preflight_rejects_noncanonical_json(bad):
    from evals.reconstruction.run_external import _validate_selected_blind_cases

    case = _case("case-a")
    case["presented_evidence"][0]["attribute"] = bad
    with pytest.raises(ValueError, match="blind evidence must be canonical finite JSON"):
        _validate_selected_blind_cases([case])


def test_selected_blind_evidence_preflight_accepts_finite_nested_json():
    from evals.reconstruction.run_external import (
        _blind_evidence_digest, _validate_selected_blind_cases,
    )

    case = _case("case-a")
    case["presented_evidence"][0]["attribute"] = {
        "ratio": 0.125, "count": 42, "flags": [True, False, None],
        "nested": {"note": "synthetic"},
    }
    assert _validate_selected_blind_cases([case]) is None
    assert len(_blind_evidence_digest(case["case_id"], case["presented_evidence"])) == 64


@pytest.mark.parametrize("resume", [False, True])
def test_noncanonical_blind_evidence_fails_before_artifacts_or_model_call(
    tmp_path, monkeypatch, resume,
):
    from evals.reconstruction import run_external

    case = _case("case-a")
    case["presented_evidence"][1]["attributes"] = {
        "nested": [{"invalid": float("inf")}],
    }
    output_dir = tmp_path / "results"
    if resume:
        output_dir.mkdir()
        predictions_path = output_dir / "synthetic-model.predictions.jsonl"
        predictions_path.write_text(
            json.dumps(_prediction("case-a")) + "\n", encoding="utf-8"
        )
        before = predictions_path.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-never-used")
    monkeypatch.setattr(run_external, "load_cases", lambda: [case])
    monkeypatch.setattr(run_external, "split_cases", lambda cases, split: cases)
    monkeypatch.setattr(run_external, "_request", lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError("invalid evidence must not call model"))
    ))
    with pytest.raises(ValueError, match="blind evidence must be canonical finite JSON"):
        run_external.main([
            "--model", "synthetic-model", "--output-dir", str(output_dir),
            *(["--resume"] if resume else []),
        ])
    if resume:
        assert predictions_path.read_bytes() == before
        assert not (output_dir / "synthetic-model.report.json").exists()
        assert not (output_dir / "synthetic-model.metadata.json").exists()
    else:
        assert not output_dir.exists()


@pytest.mark.parametrize("extra, error", [
    ('"choices":[]', "duplicate JSON object key"),
    ('"usage":{"prompt_tokens":1,"prompt_tokens":2}', "duplicate JSON object key"),
    ('"usage":{"prompt_tokens":NaN}', "non-finite JSON"),
    ('"usage":{"completion_tokens":Infinity}', "non-finite JSON"),
    ('"usage":{"completion_tokens":-Infinity}', "non-finite JSON"),
    ('"usage":{"completion_tokens":1e999}', "non-finite JSON"),
    ('"unused":{"nested":[{"ratio":-1e999}]}', "non-finite JSON"),
])
def test_fake_provider_envelope_rejects_ambiguous_or_nonfinite_receipts(extra, error):
    """The outer HTTP envelope is evidence too, even when model JSON is valid."""
    from evals.reconstruction.run_external import _request

    content = json.dumps(json.dumps(_prediction("case-a")))
    raw = '{"choices":[{"message":{"content":' + content + '}}],' + extra + '}'

    class FakeResponse:
        text = raw

        def raise_for_status(self):
            pass

        def json(self):
            raise AssertionError("permissive response.json() must not be used")

    class FakeClient:
        def post(self, *args, **kwargs):
            assert kwargs["json"]["model"] == "synthetic-model"
            return FakeResponse()

    with pytest.raises(ValueError, match=error):
        _request(
            FakeClient(), base_url="https://invalid.example", api_key="synthetic-only",
            model="synthetic-model", case=_blind_case(), temperature=None, timeout=1.0,
        )


def test_fake_provider_envelope_accepts_finite_receipts_without_json_shortcut():
    from evals.reconstruction.run_external import _request

    raw = json.dumps({
        "id": "synthetic-only",
        "model": "synthetic-model",
        "choices": [{"message": {"content": json.dumps(_prediction("case-a"))}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "details": {"ratio": 0.125}},
    })

    class FakeResponse:
        text = raw

        def raise_for_status(self):
            pass

        def json(self):
            raise AssertionError("must parse raw envelope with strict JSON")

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse()

    prediction, receipt = _request(
        FakeClient(), base_url="https://invalid.example", api_key="synthetic-only",
        model="synthetic-model", case=_blind_case(), temperature=None, timeout=1.0,
    )
    assert prediction["case_id"] == "case-a"
    assert receipt["response_id"] == "synthetic-only"
    assert receipt["usage"]["details"]["ratio"] == 0.125


def test_fake_provider_rejects_nonobject_outer_completion_envelope():
    from evals.reconstruction.run_external import _request

    class FakeResponse:
        text = "[]"

        def raise_for_status(self):
            pass

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse()

    with pytest.raises(ValueError, match="provider completion envelope must be a JSON object"):
        _request(
            FakeClient(), base_url="https://invalid.example", api_key="synthetic-only",
            model="synthetic-model", case=_blind_case(), temperature=None, timeout=1.0,
        )


# The standalone scorer ingests predictions from external producers.
@pytest.mark.parametrize("raw,pattern", [
    ('{"case_id":"case-a","case_id":"case-b","workflows":[]}', "duplicate JSON object key"),
    ('{"case_id":"case-a","workflows":[{"event_ids":["task"],"event_ids":["noise"]}]}',
     "duplicate JSON object key"),
    ('{"case_id":"case-a","workflows":[],"uncertainty":{"membership":true,"membership":false}}',
     "duplicate JSON object key"),
    ('{"case_id":"case-a","workflows":[],"extra":NaN}', "non-finite JSON constant"),
    ('{"case_id":"case-a","workflows":[],"extra":-Infinity}', "non-finite JSON constant"),
    ('{"case_id":"case-a","workflows":[],"extra":{"ratio":1e999}}', "non-finite JSON number"),
    ('{"case_id":"case-a","workflows":[],"extra":[-1e999]}', "non-finite JSON number"),
])
def test_standalone_scorer_jsonl_rejects_ambiguous_rows_with_line_context(tmp_path, raw, pattern):
    from evals.reconstruction.scoring import _jsonl

    path = tmp_path / "synthetic-predictions.jsonl"
    path.write_text('{"case_id":"case-ok","workflows":[]}\n' + raw + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=pattern) as error:
        _jsonl(path)
    assert "line 2:" in str(error.value)
    assert path.read_text(encoding="utf-8").endswith(raw + "\n")


def test_standalone_scorer_jsonl_accepts_valid_finite_rows_and_blank_lines(tmp_path):
    from evals.reconstruction.scoring import _jsonl

    first = _prediction("case-a")
    second = {**_prediction("case-b"), "extra": {"ratio": 0.125, "items": [1, 2]}}
    path = tmp_path / "synthetic-predictions.jsonl"
    path.write_text("\n" + json.dumps(first) + "\n  \n" + json.dumps(second) + "\n",
                    encoding="utf-8")
    assert _jsonl(path) == [first, second]
    report = aggregate([_case("case-b"), _case("case-a")], _jsonl(path))
    assert report["workflow_assignment_f1"] == 1.0


def test_standalone_scorer_jsonl_rejects_nonobject_with_line_number(tmp_path):
    from evals.reconstruction.scoring import _jsonl

    path = tmp_path / "synthetic-predictions.jsonl"
    path.write_text("\n[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2: prediction must be an object"):
        _jsonl(path)
