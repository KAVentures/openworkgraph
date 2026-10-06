from __future__ import annotations

import json

from evals.reconstruction.fixtures import generate_cases, validate_cases, _structural_signature
from evals.reconstruction.baselines import score_baselines
from evals.reconstruction.scoring import acceptance, aggregate, score_case


def _oracle_prediction(case: dict) -> dict:
    truth = case["ground_truth"]
    workflows = []
    for idx, row in enumerate(truth["workflows"], start=1):
        workflows.append({
            "prediction_id": f"workflow_{idx}",
            "event_ids": list(row["event_ids"]),
            "ordered_event_ids": list(row["event_ids"]),
            "automation_relevant_event_ids": list(row["automation_relevant_event_ids"]),
            "ui_mechanic_event_ids": list(row["ui_mechanic_event_ids"]),
        })
    return {
        "case_id": case["case_id"],
        "workflows": workflows,
        "unassigned_event_ids": list(truth["noise_event_ids"]),
        "insufficient_evidence": bool(truth["requires_uncertainty"]),
    }


def test_reconstruction_fixtures_are_blind_and_partition_every_event():
    cases = generate_cases()
    assert len(cases) == 30
    assert validate_cases(cases) == []


def test_oracle_predictions_score_perfectly_and_pass_thresholds():
    cases = generate_cases()
    report = aggregate(cases, [_oracle_prediction(case) for case in cases])
    assert report["workflow_assignment_f1"] == 1.0
    assert report["cross_workflow_contamination"] == 0.0
    assert report["workflow_count_accuracy"] == 1.0
    assert report["checkpoint_recall"] == 1.0
    assert report["interruption_rejection"] == 1.0
    assert report["uncertainty_accuracy"] == 1.0
    assert report["required_uncertainty_recall"] == 1.0
    assert report["unnecessary_uncertainty_rate"] == 0.0
    assert acceptance(report)["passed"] is True


def test_cross_workflow_event_is_counted_as_contamination():
    case = generate_cases()[0]
    prediction = _oracle_prediction(case)
    assert len(prediction["workflows"]) == 2
    moved = prediction["workflows"][1]["event_ids"][0]
    prediction["workflows"][0]["event_ids"].append(moved)
    result = score_case(case, prediction)
    assert result["cross_workflow_contamination"] > 0
    assert result["workflow_assignment_precision"] < 1.0


def test_never_admitting_uncertainty_fails_required_uncertainty_threshold():
    cases = generate_cases()
    predictions = [_oracle_prediction(case) for case in cases]
    for prediction in predictions:
        prediction["insufficient_evidence"] = False
    report = aggregate(cases, predictions)
    assert report["uncertainty_accuracy"] == 0.9
    assert report["required_uncertainty_recall"] == 0.0
    assert acceptance(report)["passed"] is False


def test_presented_fixture_matches_ai_facing_trace_identity_boundary():
    cases = generate_cases()
    presented = cases[0]["presented_evidence"][0]
    source = cases[0]["source_events"][0]

    assert presented["session_id"] == "session:synthetic-workday"
    assert "actor_id" not in presented
    assert "device_id" not in presented
    assert "sensor_id" not in presented
    assert source["actor_id"] == "human"
    assert source["device_id"] == "device:test"

    clipboard_case = cases[24]
    clipboard_event = clipboard_case["presented_evidence"][0]
    assert clipboard_event["event_type"] == "browser_copy"
    assert clipboard_event["action"] == "copy"
    assert clipboard_event["clipboard_transfer_id"] == "xfer-0"


def test_hidden_rule_case_requires_explicit_uncertainty():
    case = next(case for case in generate_cases() if case["ground_truth"]["requires_uncertainty"])
    prediction = _oracle_prediction(case)
    assert score_case(case, prediction)["uncertainty_correct"] is True
    prediction["insufficient_evidence"] = False
    assert score_case(case, prediction)["uncertainty_correct"] is False


def test_all_30_cases_are_structurally_distinct_and_labels_are_neutral():
    cases = generate_cases()
    assert len({_structural_signature(case) for case in cases}) == 30
    forbidden = (
        "unrelated", "different project", "customer alpha", "customer beta",
        "ticket b", "update b", "opportunity a", "opportunity b",
    )
    for case in cases:
        rendered = json.dumps(case["presented_evidence"]).lower()
        assert not any(token in rendered for token in forbidden)


def test_shortcut_baselines_do_not_pass_benchmark():
    reports = score_baselines(generate_cases())
    for report in reports.values():
        assert acceptance(report)["passed"] is False
        assert report["workflow_assignment_f1"] < 0.90
        assert report["workflow_count_accuracy"] < 0.90
