from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "evals" / "automation_interpretation_cases.json"


def _cases() -> list[dict]:
    payload = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


def _perfect_score_sheet() -> dict:
    return {
        "model": "test-model",
        "client": "test-client",
        "revision": "deadbeef",
        "cases": [
            {
                "id": case["id"],
                "must_cover": [2] * len(case["must_cover"]),
                "must_not": [2] * len(case["must_not"]),
            }
            for case in _cases()
        ],
    }


def test_automation_interpretation_eval_has_balanced_underestimation_and_overreach_cases():
    cases = _cases()
    assert 14 <= len(cases) <= 20
    axes = [case["bias_axis"] for case in cases]
    assert axes.count("underestimation") >= 6
    assert axes.count("overreach") >= 6
    assert len({case["id"] for case in cases}) == len(cases)


def test_each_eval_case_has_positive_and_negative_criteria():
    for case in _cases():
        assert case["user_intent"].strip()
        assert case["evidence_summary"].strip()
        assert len(case["must_cover"]) >= 3
        assert len(case["must_not"]) >= 2
        assert all(str(item).strip() for item in case["must_cover"])
        assert all(str(item).strip() for item in case["must_not"])


def test_eval_set_covers_known_interpretation_failure_modes():
    text = EVAL_PATH.read_text(encoding="utf-8").lower()
    required_concepts = (
        "missing historical",
        "downstream",
        "next autonomy boundary",
        "pytest",
        "financial",
        "clinical",
        "standing authorization",
        "macro",
        "not yet strong evidence",
    )
    for concept in required_concepts:
        assert concept in text


def test_eval_scorer_reports_both_bias_axes_and_total(tmp_path):
    from evals.score_automation_interpretation import score

    path = tmp_path / "scores.json"
    path.write_text(json.dumps(_perfect_score_sheet()), encoding="utf-8")
    report = score(path)

    assert report["underestimation_score"] == 100.0
    assert report["overreach_score"] == 100.0
    assert report["total_score"] == 100.0
    assert report["critical_failures"] == []


def test_eval_scorer_flags_must_not_violation_as_critical(tmp_path):
    from evals.score_automation_interpretation import score

    sheet = _perfect_score_sheet()
    sheet["cases"][0]["must_not"][0] = 0
    path = tmp_path / "scores.json"
    path.write_text(json.dumps(sheet), encoding="utf-8")
    report = score(path)

    assert report["total_score"] < 100.0
    assert report["critical_failures"] == ["gmail_salesforce_sheets_reply:must_not[0]"]


def test_paired_scorer_reports_owg_uplift(tmp_path):
    from evals.score_automation_interpretation import compare

    owg = _perfect_score_sheet()
    control = _perfect_score_sheet()
    control["cases"][0]["must_cover"][0] = 0
    control["cases"][1]["must_not"][0] = 0
    control_path = tmp_path / "control.json"
    owg_path = tmp_path / "owg.json"
    control_path.write_text(json.dumps(control), encoding="utf-8")
    owg_path.write_text(json.dumps(owg), encoding="utf-8")

    report = compare(control_path, owg_path)
    assert report["uplift"]["total_score"] > 0
    assert report["new_critical_failures"] == []
    assert report["resolved_critical_failures"]
    assert report["cases_improved"] == 2
    assert report["cases_regressed"] == 0
    assert len(report["case_deltas"]) == len(_cases())


def test_eval_set_covers_product_value_failure_modes():
    text = EVAL_PATH.read_text(encoding="utf-8").lower()
    concepts = (
        "pricing",
        "exception",
        "escalation",
        "downstream",
        "resource pointer",
        "unfinished",
        "failure detection",
        "recovery",
        "approval threshold",
        "interruption/noise",
        "access revocation",
        "live authorized",
    )
    for concept in concepts:
        assert concept in text
