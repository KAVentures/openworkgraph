from __future__ import annotations

import json
import re

from evals.reconstruction.fixtures import generate_cases, validate_cases, _structural_signature
from evals.reconstruction.baselines import group_by_position_parity, score_baselines
from evals.reconstruction.prepare import split_cases
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
    assert len(cases) == 54
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
    assert report["family_macro"]["families"] == 34
    assert report["family_macro"]["required_uncertainty_recall"] == 1.0
    verdict = acceptance(report)
    assert verdict["basis"] == "family_macro"
    assert verdict["passed"] is True


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
    assert report["uncertainty_accuracy"] < 1.0
    assert report["required_uncertainty_recall"] == 0.0
    assert report["family_macro"]["required_uncertainty_recall"] == 0.0
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
    assert re.fullmatch(r"x-[0-9a-f]{16}", clipboard_event["clipboard_transfer_id"])


def test_hidden_rule_case_requires_explicit_uncertainty():
    case = next(case for case in generate_cases() if case["ground_truth"]["requires_uncertainty"])
    prediction = _oracle_prediction(case)
    assert score_case(case, prediction)["uncertainty_correct"] is True
    prediction["insufficient_evidence"] = False
    assert score_case(case, prediction)["uncertainty_correct"] is False


def test_all_cases_are_structurally_distinct_and_labels_are_neutral():
    cases = generate_cases()
    assert len({_structural_signature(case) for case in cases}) == 54
    forbidden = (
        "unrelated", "different project", "customer alpha", "customer beta",
        "ticket b", "update b", "opportunity a", "opportunity b",
    )
    for case in cases:
        rendered = json.dumps(case["presented_evidence"]).lower()
        assert not any(token in rendered for token in forbidden)


def test_shortcut_baselines_do_not_pass_benchmark():
    reports = score_baselines(generate_cases())
    assert {"tab_context", "resource_reference", "position_parity", "application"} <= set(reports)
    for report in reports.values():
        verdict = acceptance(report)
        assert verdict["basis"] == "family_macro"
        assert verdict["passed"] is False
        assert not all(verdict["checks"].values())


def test_no_id_cases_are_not_solved_by_position_alternation():
    cases = {case["case_id"]: case for case in generate_cases()}
    for case_id in ("reconstruction-20", "reconstruction-27"):
        case = cases[case_id]
        result = score_case(case, group_by_position_parity(case))
        assert result["workflow_assignment_f1"] < 1.0


def test_family_level_holdout_never_splits_variants():
    cases = generate_cases()
    development = split_cases(cases, "development")
    holdout = split_cases(cases, "holdout")
    dev_families = {case["family_id"] for case in development}
    holdout_families = {case["family_id"] for case in holdout}
    assert development
    assert holdout
    assert dev_families.isdisjoint(holdout_families)
    assert {case["case_id"] for case in development + holdout} == {case["case_id"] for case in cases}


def test_hard_corpus_has_independent_uncertainty_and_long_session_coverage():
    cases = generate_cases()
    independent = [case for case in cases if "-v" not in case["case_id"]]
    assert len(independent) == 24
    uncertain = [case for case in cases if case["ground_truth"]["requires_uncertainty"]]
    assert len(uncertain) >= 7
    assert len({case["family_id"] for case in uncertain}) >= 5
    assert any(len(case["presented_evidence"]) >= 50 for case in cases)
    assert any(len(case["ground_truth"]["workflows"]) >= 5 for case in cases)
    assert any(
        all(not ((event.get("metadata") or {}).get("resource_reference")) for event in case["presented_evidence"])
        for case in independent
    )


def test_production_formatter_and_reference_normalizer_accept_all_fixtures():
    from shared.evidence import rich_evidence_row
    from shared.core.resource_references import normalize_resource_reference
    for case in generate_cases():
        for source, presented in zip(case["source_events"], case["presented_evidence"]):
            assert presented == rich_evidence_row(source)
            ref = source["metadata"].get("resource_reference")
            if ref:
                assert normalize_resource_reference(ref, include_locator=False, allow_persisted=True) == ref


def test_generic_case_24_does_not_require_false_certainty():
    case = next(c for c in generate_cases() if c["case_id"] == "reconstruction-24")
    assert "membership" in case["ground_truth"]["uncertainty_dimensions"]
    prediction = _oracle_prediction(case)
    prediction["insufficient_evidence"] = False
    assert score_case(case, prediction)["uncertainty_correct"] is False
    prediction["uncertainty"] = {"membership": True}
    assert score_case(case, prediction)["uncertainty_correct"] is True


def test_pure_fragments_are_not_counted_as_cross_workflow_contamination():
    case = generate_cases()[0]
    prediction = _oracle_prediction(case)
    row = prediction["workflows"].pop(0)
    prediction["workflows"].extend({"event_ids": [e]} for e in row["event_ids"])
    score = score_case(case, prediction)
    assert score["cross_workflow_contamination"] == 0
    assert score["pure_fragment_excess"] > 0
    assert score["workflow_assignment_f1"] < 1


def test_authenticated_replay_preserves_events_and_hosted_linkage(tmp_path):
    from evals.reconstruction.replay import replay_client, replay_case
    cases = generate_cases()
    with replay_client(tmp_path) as (db, call):
        for case in cases:
            payload = replay_case(db, call, case, page_size=7)
            assert [r["event_id"] for r in payload["presented_evidence"]] == [r["event_id"] for r in case["source_events"]]
            assert payload["replay_contract"]["returned_rows"] == len(case["source_events"])
            assert "ground_truth" not in payload and "source_events" not in payload
            for row in payload["presented_evidence"]:
                assert "actor_id" not in row and "page_path" not in row
            original = {r["event_id"]: r for r in case["source_events"]}
            for row in payload["presented_evidence"]:
                meta = original[row["event_id"]]["metadata"]
                if meta.get("clipboard_transfer_id"):
                    assert row["clipboard_transfer_id"] == meta["clipboard_transfer_id"]


def test_hallucinated_ids_reduce_assignment_precision():
    case=generate_cases()[0]
    prediction=_oracle_prediction(case)
    prediction['workflows'][0]['event_ids'].append('invented-event')
    scored=score_case(case,prediction)
    assert scored['unknown_event_id_count']==1
    assert scored['workflow_assignment_precision']<1


def test_outcome_uncertainty_is_separate_from_membership_and_rule():
    case=next(c for c in generate_cases() if not c['ground_truth']['requires_uncertainty'])
    prediction=_oracle_prediction(case)
    prediction['uncertainty']={'membership':False,'business_rule':False,'outcome':True}
    assert score_case(case,prediction)['uncertainty_correct']
    assert aggregate([case],[prediction])['unnecessary_uncertainty_rate']==0
