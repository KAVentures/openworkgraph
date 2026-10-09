"""Unit tests for multi-client OWG retrieval evaluation scoring (not model runs)."""
from __future__ import annotations

import pytest

from evals.hosted_context.score import load_cases, score_case, score_group, score_runs


CASES = load_cases()
BY_ID = {case["id"]: case for case in CASES}


def call(name, *, arguments=None, events=(), response_chars=0):
    return {
        "name": name, "arguments": arguments or {},
        "event_ids": list(events), "response_chars": response_chars,
    }


def recorded(case_id, *calls):
    return {
        "case_id": case_id, "client": "test-fixture-client",
        "model": "not-a-model", "trial": "unit",
        "harness_recorded": True, "tool_calls": list(calls),
    }


def test_case_registry_balances_positive_and_negative_prompts():
    assert len(CASES) >= 20
    assert len(BY_ID) == len(CASES)
    assert sum(not c["requires_owg"] for c in CASES) >= 5
    assert sum(c["raw_fallback_required"] for c in CASES) >= 7


def test_inference_false_negative_requires_unfiltered_chronology():
    case = BY_ID["raw-without-task"]
    bad = score_case(case, recorded(case["id"], call(
        "get_workflow_trace", arguments={"query": "procurement review"},
        events=["evt-untagged-004"],
    )))
    assert bad["triggered"] and bad["first_tool_correct"]
    assert bad["raw_fallback_met"] is False
    good = score_case(case, recorded(case["id"], call(
        "get_workflow_trace", arguments={"since": "2026-10-01T00:00:00Z"},
        events=["evt-untagged-004"],
    )))
    assert good["raw_fallback_met"] is True
    assert good["target_evidence_coverage"] == 1.0


def test_repeated_work_requires_examples_and_independent_raw_fallback():
    case = BY_ID["workflow-automation"]
    partial = score_case(case, recorded(case["id"], call("find_repeated_workflows")))
    assert partial["triggered"]
    assert partial["all_required_tools_called"] is False
    assert partial["raw_fallback_met"] is False
    complete = score_case(case, recorded(case["id"],
        call("find_repeated_workflows"),
        call("get_workflow_evidence", events=["evt-quote-a"]),
        call("get_workflow_trace", events=["evt-quote-b"]),
    ))
    assert complete["all_required_tools_called"]
    assert complete["raw_fallback_met"]
    assert complete["target_evidence_coverage"] == 1.0


def test_negative_controls_penalize_unnecessary_mcp_calls():
    case = BY_ID["generic-arithmetic"]
    correct = score_case(case, recorded(case["id"]))
    wrong = score_case(case, recorded(case["id"], call("get_current_work_context")))
    assert correct["first_tool_correct"] and not correct["triggered"]
    assert not wrong["first_tool_correct"] and wrong["triggered"]
    scored = score_group([case, BY_ID["recent-continuity"]], [
        recorded("generic-arithmetic", call("get_current_work_context")),
        recorded("recent-continuity", call("get_current_work_context", events=["evt-recent-001"])),
    ])
    assert scored["trigger_precision"] == 0.5
    assert scored["trigger_recall"] == 1.0
    assert scored["negative_prompt_specificity"] == 0.0


def test_evidence_coverage_does_not_use_model_self_claims():
    case = BY_ID["misleading-adjacency"]
    got = score_case(case, recorded(case["id"],
        call("get_current_work_context", events=["evt-unrelated-001"]),
        call("get_workflow_trace", events=["evt-unrelated-001"]),
    ))
    assert got["target_evidence_coverage"] == 0.5
    assert got["adjudicated_supported_claims"] is None
    assert got["adjudicated_unsupported_claims"] is None


def test_manual_grounding_requires_independent_reviewer():
    case = BY_ID["agent-retry"]
    trace = recorded(case["id"], call("get_agent_runs"))
    trace["manual_review"] = {"supported_claims": 2, "unsupported_claims": 1}
    with pytest.raises(ValueError, match="reviewer"):
        score_case(case, trace)
    trace["manual_review"]["reviewer"] = "independent-human-reviewer"
    result = score_group([case], [trace])
    assert result["adjudicated_claim_grounding"] == 0.6667


def test_group_missing_cases_are_reported_not_assumed_correct():
    result = score_group(CASES, [recorded("recent-continuity", call("get_current_work_context"))])
    assert not result["complete"]
    assert result["cases_run"] == 1
    assert len(result["missing_case_ids"]) == len(CASES) - 1


def test_reject_duplicate_or_unknown_cases_and_invalid_tool_shapes():
    case = BY_ID["recent-continuity"]
    row = recorded(case["id"], call("get_current_work_context"))
    with pytest.raises(ValueError, match="duplicate case"):
        score_group([case], [row, row])
    with pytest.raises(ValueError, match="unknown case"):
        score_group([case], [recorded("invented")])
    with pytest.raises(ValueError, match="unknown OWG tool"):
        score_case(case, recorded(case["id"], call("run_shell")))


def test_harness_recorded_is_mandatory_for_model_run_reports():
    case = BY_ID["recent-continuity"]
    row = recorded(case["id"], call("get_current_work_context"))
    report = score_runs([case], [row])
    assert report["model_count"] == 1 and report["trial_count"] == 1
    assert report["groups"][0]["complete"]
    row["harness_recorded"] = False
    with pytest.raises(ValueError, match="instrumented client"):
        score_runs([case], [row])


def test_grouped_trials_do_not_mix_models():
    case = BY_ID["recent-continuity"]
    row_a = recorded(case["id"], call("get_current_work_context"))
    row_b = {**row_a, "client": "other-client", "model": "other-model"}
    report = score_runs([case], [row_a, row_b])
    assert report["trial_count"] == 2 and report["model_count"] == 2
