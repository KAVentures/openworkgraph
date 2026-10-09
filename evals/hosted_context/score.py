"""Score real client-side OpenWorkGraph tool traces against synthetic intents.

This is a trace grader, not an LLM runner. It MUST NOT infer model success from
handwritten examples or machine-generated claims about tools it never called.
Use only traces collected by the executing client/instrumented MCP harness.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOOL_NAMES = frozenset({
    "get_current_work_context", "search_work", "get_workflow_trace",
    "find_repeated_workflows", "get_workflow_evidence", "get_agent_runs", "get_profile",
})


def load_cases(path: Path = HERE / "cases.json") -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "owg.hosted-routing-eval.v1":
        raise ValueError("unknown case schema")
    cases = document["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or not cases:
        raise ValueError("case IDs must be unique and nonempty")
    for case in cases:
        if case["requires_owg"] and not case["initial_tool_any"]:
            raise ValueError("positive case requires a routing target")
        if not case["requires_owg"] and (case["initial_tool_any"] or case["must_call"]):
            raise ValueError("negative case must not require any tool")
    return cases


def load_traces(path: Path) -> list[dict[str, Any]]:
    result = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("tool_calls"), list):
            raise ValueError(f"line {i}: expected object with tool_calls list")
        result.append(value)
    return result


def score_case(case: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    calls = trace["tool_calls"]
    names = [str(call.get("name") or "") for call in calls]
    unknown = [name for name in names if name not in TOOL_NAMES]
    if unknown:
        raise ValueError(f"{case['id']}: unknown OWG tool names: {unknown}")
    if any(not isinstance(call.get("arguments", {}), dict) for call in calls):
        raise ValueError(f"{case['id']}: each call arguments must be a dictionary")
    if any(not isinstance(call.get("event_ids", []), list) for call in calls):
        raise ValueError(f"{case['id']}: each call event_ids must be a list")
    relevant = bool(case["requires_owg"])
    first = names[0] if names else None
    triggered = bool(names)
    first_correct = first in case["initial_tool_any"] if relevant else not triggered
    required_calls = set(case["must_call"])
    required_met = required_calls.issubset(set(names))
    fallback_calls = [
        call for call in calls
        if call.get("name") == "get_workflow_trace"
        and not str(call.get("arguments", {}).get("query") or "").strip()
    ]
    fallback_met = bool(fallback_calls) if case["raw_fallback_required"] else None
    retrieved = set()
    for call in calls:
        retrieved.update(str(item) for item in call.get("event_ids", []))
    targets = set(case["target_event_ids"])
    overlap = targets.intersection(retrieved)
    # An empty target is not a missing-evidence failure: no synced evidence
    # onboarding is scored by route choice and adjudicated response honesty.
    observed_coverage = (len(overlap) / len(targets)) if targets else None
    size = sum(max(0, int(call.get("response_chars", 0))) for call in calls)
    manual = trace.get("manual_review")
    if manual is not None:
        if not isinstance(manual, dict) or not manual.get("reviewer"):
            raise ValueError("manual_review requires reviewer and counts")
        supported = int(manual.get("supported_claims", 0))
        unsupported = int(manual.get("unsupported_claims", 0))
        permission = int(manual.get("unauthorized_action_claims", 0))
        if min(supported, unsupported, permission) < 0:
            raise ValueError("manual_review counts cannot be negative")
    else:
        supported = unsupported = permission = None
    return {
        "id": case["id"], "family": case["family"], "trigger_expected": relevant,
        "triggered": triggered, "first_tool_correct": first_correct,
        "all_required_tools_called": required_met,
        "raw_fallback_required": bool(case["raw_fallback_required"]),
        "raw_fallback_met": fallback_met,
        "target_evidence_coverage": observed_coverage,
        "relevant_event_ids_found": sorted(overlap),
        "tool_call_count": len(calls), "response_chars": size,
        "adjudicated_supported_claims": supported,
        "adjudicated_unsupported_claims": unsupported,
        "adjudicated_unauthorized_action_claims": permission,
    }


def _fraction(success: int, denominator: int) -> float | None:
    return round(success / denominator, 4) if denominator else None


def score_group(cases: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = {case["id"]: case for case in cases}
    seen: dict[str, dict[str, Any]] = {}
    for trace in traces:
        case_id = trace.get("case_id")
        if case_id not in lookup:
            raise ValueError(f"unknown case: {case_id}")
        if case_id in seen:
            raise ValueError(f"duplicate case: {case_id}")
        seen[case_id] = score_case(lookup[case_id], trace)
    outcomes = [seen[id] for id in lookup if id in seen]
    positives = [x for x in outcomes if x["trigger_expected"]]
    negatives = [x for x in outcomes if not x["trigger_expected"]]
    fallback = [x for x in outcomes if x["raw_fallback_required"]]
    evidence = [x for x in outcomes if x["target_evidence_coverage"] is not None]
    triggered = [x for x in outcomes if x["triggered"]]
    adjudicated = [x for x in outcomes if x["adjudicated_supported_claims"] is not None]
    supp = sum(x["adjudicated_supported_claims"] for x in adjudicated)
    unsupp = sum(x["adjudicated_unsupported_claims"] for x in adjudicated)
    permission = sum(x["adjudicated_unauthorized_action_claims"] for x in adjudicated)
    missing = [id for id in lookup if id not in seen]
    return {
        "cases_expected": len(cases),
        "cases_run": len(outcomes),
        "missing_case_ids": missing,
        "complete": not missing,
        "trigger_recall": _fraction(sum(x["triggered"] for x in positives), len(positives)),
        "trigger_precision": _fraction(sum(x["trigger_expected"] for x in triggered), len(triggered)),
        "negative_prompt_specificity": _fraction(sum(not x["triggered"] for x in negatives), len(negatives)),
        "first_tool_accuracy": _fraction(sum(x["first_tool_correct"] for x in outcomes), len(outcomes)),
        "required_tool_recall": _fraction(sum(x["all_required_tools_called"] for x in positives), len(positives)),
        "raw_fallback_recall": _fraction(sum(x["raw_fallback_met"] for x in fallback), len(fallback)),
        "mean_event_coverage": round(statistics.mean(x["target_evidence_coverage"] for x in evidence), 4) if evidence else None,
        "adjudicated_claim_grounding": _fraction(supp, supp + unsupp),
        "adjudicated_unauthorized_action_claims": permission if adjudicated else None,
        "mean_tool_calls": round(statistics.mean(x["tool_call_count"] for x in outcomes), 2) if outcomes else None,
        "mean_response_chars": round(statistics.mean(x["response_chars"] for x in outcomes)) if outcomes else None,
        "case_results": outcomes,
    }


def score_runs(cases: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in traces:
        client = str(item.get("client") or "").strip()
        model = str(item.get("model") or "").strip()
        trial = str(item.get("trial") or "").strip()
        if not client or not model or not trial:
            raise ValueError("every trace needs client, model, and trial")
        if item.get("harness_recorded") is not True:
            raise ValueError("traces must be produced by an instrumented client, not fabricated model self-reports")
        grouped[(client, model, trial)].append(item)
    return {
        "schema": "owg.hosted-routing-report.v1",
        "model_results_are_observed": bool(grouped),
        "model_count": len(set((client, model) for client, model, _ in grouped)),
        "trial_count": len(grouped),
        "groups": [
            {"client": client, "model": model, "trial": trial, **score_group(cases, rows)}
            for (client, model, trial), rows in sorted(grouped.items())
        ],
        "warning": (
            "Evidence IDs prove retrieval, NOT factual entailment. Grounding/permissions require "
            "independent manual review. Do not treat synthetic examples as model runs."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade OWG MCP use from instrumented client JSONL traces")
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--cases", default=HERE / "cases.json", type=Path)
    parser.add_argument("--out", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    report = score_runs(load_cases(args.cases), load_traces(args.traces))
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
