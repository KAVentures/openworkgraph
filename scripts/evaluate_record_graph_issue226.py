#!/usr/bin/env python3
"""Matched, telemetry-grounded agent A/B/C evaluation for issue #226.

Accepts privacy-safe *real* agent-run records and a separate adjudicated
ground-truth file. It never fabricates a model run, calls a provider, invents
source outcomes, or treats a model's claim of tool usage as a tool receipt.
Run collection and holdout adjudication must happen outside this evaluator.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from math import sqrt
from pathlib import Path
import re
from typing import Any

CONDITIONS = ("A", "B", "C")
_CASE = re.compile(r"^[a-z][a-z0-9_-]{2,60}$")
_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_OUTCOMES = {"resolved", "unresolved", "unknown", "paid", "unpaid", "refunded", "denied"}
_REQUIRED_STRESSORS = {
    "interleaved", "no_reference", "source_outage", "stale_state",
    "divergent_clocks", "inconsistent_ids", "cross_device",
}


def _validate_cases(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cases = {}
    for case in items:
        cid = str(case.get("case_id") or "")
        if not _CASE.fullmatch(cid) or cid in cases:
            raise ValueError("case IDs must be unique opaque slugs")
        outcome = case.get("gold_outcome")
        if outcome not in _OUTCOMES:
            raise ValueError("gold_outcome is missing/invalid")
        ref = case.get("resource_ref")
        if ref is not None and not _REF.fullmatch(str(ref)):
            raise ValueError("resource_ref must be an opaque OWG record pointer or null")
        if str(case.get("fold")) not in {"development", "holdout"}:
            raise ValueError("fold must be development or holdout")
        cases[cid] = dict(case)
    return cases


def _wilson(successes: int, total: int) -> dict[str, float] | None:
    if total < 1:
        return None
    z = 1.959963984540054
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    radius = z * sqrt((p * (1 - p) / total) + z * z / (4 * total * total)) / denom
    return {"lower": round(max(0, center - radius), 4),
            "upper": round(min(1, center + radius), 4)}


def evaluate(cases_input: list[dict[str, Any]], runs: list[dict[str, Any]],
             *, minimum_replicates: int = 2) -> dict[str, Any]:
    """Score exactly matched case+replicate IDs; no credit for self-attested tools."""
    cases = _validate_cases(cases_input)
    if not cases:
        raise ValueError("no adjudicated cases supplied")
    if minimum_replicates < 2:
        raise ValueError("at least two replicates required")
    keys: set[tuple[str, str, int]] = set()
    lookup = {}
    for run in runs:
        cid = str(run.get("case_id") or "")
        condition = str(run.get("condition") or "")
        replicate = run.get("replicate")
        if cid not in cases or condition not in CONDITIONS or type(replicate) is not int or replicate < 1:
            raise ValueError("run must match a case, condition A/B/C, and positive replicate")
        key = (cid, condition, replicate)
        if key in keys:
            raise ValueError("duplicate case/condition/replicate")
        keys.add(key)
        prediction = run.get("predicted_outcome")
        if prediction not in _OUTCOMES:
            raise ValueError("invalid predicted outcome")
        tool_logs = run.get("host_tool_logs")
        if not isinstance(tool_logs, list):
            raise ValueError("missing host tool invocation logs")
        # Runner-provided logs are *not* attested until independently verified
        # by the evaluation collector. Reject model-authored summaries as proof.
        matched_record = False
        any_owg = False
        source_read = False
        source_snapshot = None
        for entry in tool_logs:
            if not isinstance(entry, dict):
                continue
            if entry.get("origin") != "orchestrator_tool_execution":
                continue
            name = str(entry.get("tool") or "")
            status = str(entry.get("status") or "")
            if name in {"get_record_graph", "get_record_graph_export"} and status == "success":
                any_owg = True
                if cases[cid].get("resource_ref") and entry.get("resource_ref") == cases[cid]["resource_ref"]:
                    matched_record = True
            # Outages must be represented in the comparison, not discarded.
            # Success and explicit unavailable/error both prove access was
            # attempted; no source facts are inferred from an unsuccessful call.
            if (name == "source_audit_log_read" and status in {"success", "unavailable", "error"}
                    and entry.get("source_record_match") is True):
                source_read = True
                digest = str(entry.get("source_snapshot_sha256") or "")
                if status == "success" and re.fullmatch(r"[0-9a-f]{64}", digest):
                    source_snapshot = "success:" + digest
                elif status in {"unavailable", "error"}:
                    source_snapshot = "outage:" + status
        lookup[key] = {
            "correct": prediction == cases[cid]["gold_outcome"],
            "unknown_predicted": prediction == "unknown",
            "owg_called": any_owg,
            "owg_exact_record_used": matched_record,
            "source_log_access_attempted": source_read,
            "source_snapshot": source_snapshot,
            "condition": condition,
            "case_id": cid,
            "replicate": replicate,
        }

    paired = []
    incomplete = []
    for cid in sorted(cases):
        for rep in range(1, minimum_replicates + 1):
            trio = [lookup.get((cid, cond, rep)) for cond in CONDITIONS]
            if any(x is None for x in trio):
                incomplete.append({"case_id": cid, "replicate": rep})
                continue
            paired.append(trio)

    scores: dict[str, Any] = {}
    for i, condition in enumerate(CONDITIONS):
        values = [trio[i] for trio in paired]
        correct = sum(x["correct"] for x in values)
        scores[condition] = {
            "matched_trials": len(values),
            "correct": correct,
            "accuracy": round(correct / len(values), 4) if values else None,
            "wilson_95": _wilson(correct, len(values)),
            "unknown_predictions": sum(x["unknown_predicted"] for x in values),
            "actual_owg_tool_calls": sum(x["owg_called"] for x in values),
            "matched_owg_record_calls": sum(x["owg_exact_record_used"] for x in values),
            "matched_source_log_access_attempts": sum(x["source_log_access_attempted"] for x in values),
        }
    deltas = [int(trio[2]["correct"]) - int(trio[1]["correct"]) for trio in paired]
    positive = sum(x > 0 for x in deltas)
    negative = sum(x < 0 for x in deltas)
    c_missing = sum(not trio[2]["owg_exact_record_used"] for trio in paired if cases[trio[2]["case_id"]].get("resource_ref"))
    stressors = set().union(*(set(c.get("stressors") or []) for c in cases.values()))
    missing_stressors = sorted(_REQUIRED_STRESSORS - stressors)
    holds = any(c.get("fold") == "holdout" for c in cases.values())
    # Merely exposing OWG to condition C was the key October failure.
    # A/B/C must be matched, C must use the correct record when available,
    # and B/C both require source-log evidence for an incremental-value test.
    confounded = sum(
        # An absent opaque record reference makes a matched source read
        # impossible. B/C must both abstain: penalizing their correct refusal
        # would invalidate every benchmark that includes the required
        # no_reference stressor.
        (
            trio[1]["source_log_access_attempted"] or trio[2]["source_log_access_attempted"]
        ) if cases[trio[1]["case_id"]].get("resource_ref") is None else (
            not trio[1]["source_log_access_attempted"]
            or not trio[2]["source_log_access_attempted"]
            or not trio[1]["source_snapshot"]
            or trio[1]["source_snapshot"] != trio[2]["source_snapshot"]
        )
        for trio in paired
    )
    valid = (not incomplete and bool(paired) and c_missing == 0
             and confounded == 0 and not missing_stressors and holds)
    return {
        "schema": "openworkgraph.issue226.matched-evaluation.v1",
        "status": "evaluable" if valid else "insufficient_evidence",
        "cases_supplied": len(cases),
        "matched_triplets": len(paired),
        "incomplete_pairs": incomplete[:50],
        "scores": scores,
        "c_wrong_or_missing_record_retrievals": c_missing,
        "b_c_source_log_ablation_confounds": confounded,
        "stressors_missing": missing_stressors,
        "holdout_present": holds,
        "c_minus_b_accuracy": round(sum(deltas) / len(deltas), 4) if deltas else None,
        "c_b_paired_wins": positive,
        "c_b_paired_losses": negative,
        "superiority_claimed": False,
        "retrieval_logs_are_collector_provided_not_cryptographically_attested": True,
        "missing_tool_call_means_not_used": True,
        "no_fake_trials_generated": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--minimum-replicates", type=int, default=2)
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    cases = json.loads(opts.cases.read_text(encoding="utf-8"))
    runs = json.loads(opts.runs.read_text(encoding="utf-8"))
    result = evaluate(cases, runs, minimum_replicates=opts.minimum_replicates)
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if opts.output:
        opts.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
