from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "evals" / "automation_interpretation_cases.json"


def _cases() -> list[dict]:
    payload = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


def test_automation_interpretation_eval_has_balanced_underestimation_and_overreach_cases():
    cases = _cases()
    assert 5 <= len(cases) <= 8
    axes = [case["bias_axis"] for case in cases]
    assert axes.count("underestimation") >= 2
    assert axes.count("overreach") >= 2
    assert len({case["id"] for case in cases}) == len(cases)


def test_each_eval_case_has_positive_and_negative_criteria():
    for case in _cases():
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
        "nothing worth automating" if False else "not yet strong evidence",
    )
    for concept in required_concepts:
        assert concept in text
