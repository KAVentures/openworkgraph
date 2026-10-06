from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from statistics import mean
from typing import Any

from .fixtures import case_family_id, load_cases

def _set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(x) for x in value if str(x)}

def _best_mapping(truth: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[int, int]:
    """Return predicted-index -> truth-index maximizing total event overlap."""
    if not truth or not predicted:
        return {}
    tsets = [_set(w.get("event_ids")) for w in truth]
    psets = [_set(w.get("event_ids")) for w in predicted]
    best_score = -1
    best: dict[int, int] = {}
    if len(predicted) <= len(truth):
        for truth_perm in itertools.permutations(range(len(truth)), len(predicted)):
            score = sum(len(psets[p] & tsets[t]) for p, t in enumerate(truth_perm))
            if score > best_score:
                best_score = score
                best = {p: t for p, t in enumerate(truth_perm)}
    else:
        for pred_subset in itertools.permutations(range(len(predicted)), len(truth)):
            score = sum(len(psets[p] & tsets[t]) for t, p in enumerate(pred_subset))
            if score > best_score:
                best_score = score
                best = {p: t for t, p in enumerate(pred_subset)}
    return best

def _pairwise_order_accuracy(true_order: list[str], predicted_order: list[str]) -> tuple[int, int]:
    rank = {event_id: idx for idx, event_id in enumerate(predicted_order)}
    shared = [event_id for event_id in true_order if event_id in rank]
    correct = total = 0
    for i in range(len(shared)):
        for j in range(i + 1, len(shared)):
            total += 1
            if rank[shared[i]] < rank[shared[j]]:
                correct += 1
    return correct, total

def score_case(case: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    truth = list((case.get("ground_truth") or {}).get("workflows") or [])
    predicted = list(prediction.get("workflows") or [])
    mapping = _best_mapping(truth, predicted)
    all_event_ids = {str(e.get("event_id") or "") for e in case.get("presented_evidence") or []}
    truth_noise = _set((case.get("ground_truth") or {}).get("noise_event_ids"))

    tp = fp = fn = 0
    correct_order = total_order = 0
    recovered_checkpoints = total_checkpoints = 0

    for p_idx, prow in enumerate(predicted):
        pset = _set(prow.get("event_ids")) & all_event_ids
        if p_idx in mapping:
            trow = truth[mapping[p_idx]]
            tset = _set(trow.get("event_ids"))
            tp += len(pset & tset)
            fp += len(pset - tset)
            predicted_order = [str(x) for x in (prow.get("ordered_event_ids") or prow.get("event_ids") or [])]
            c, n = _pairwise_order_accuracy(list(trow.get("event_ids") or []), predicted_order)
            correct_order += c
            total_order += n
            for checkpoint in trow.get("checkpoint_groups") or []:
                total_checkpoints += 1
                cp = _set(checkpoint.get("event_ids"))
                if cp and cp <= pset:
                    recovered_checkpoints += 1
        else:
            fp += len(pset)

    mapped_truth = set(mapping.values())
    for t_idx, trow in enumerate(truth):
        tset = _set(trow.get("event_ids"))
        if t_idx not in mapped_truth:
            fn += len(tset)
        else:
            pred_idx = next(p for p, t in mapping.items() if t == t_idx)
            pset = _set(predicted[pred_idx].get("event_ids")) & all_event_ids
            fn += len(tset - pset)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    contamination = fp / (tp + fp) if (tp + fp) else 0.0

    unassigned = _set(prediction.get("unassigned_event_ids"))
    noise_rejected = len(truth_noise & unassigned) / len(truth_noise) if truth_noise else 1.0

    true_relevant = set()
    true_ui = set()
    for trow in truth:
        true_relevant |= _set(trow.get("automation_relevant_event_ids"))
        true_ui |= _set(trow.get("ui_mechanic_event_ids"))
    pred_relevant = set()
    pred_ui = set()
    for prow in predicted:
        pred_relevant |= _set(prow.get("automation_relevant_event_ids"))
        pred_ui |= _set(prow.get("ui_mechanic_event_ids"))
    rel_tp = len(pred_relevant & true_relevant)
    rel_fp = len(pred_relevant - true_relevant)
    rel_fn = len(true_relevant - pred_relevant)
    rel_precision = rel_tp / (rel_tp + rel_fp) if rel_tp + rel_fp else 0.0
    rel_recall = rel_tp / (rel_tp + rel_fn) if rel_tp + rel_fn else 0.0

    ui_tp = len(pred_ui & true_ui)
    ui_precision = ui_tp / len(pred_ui) if pred_ui else (1.0 if not true_ui else 0.0)
    ui_recall = ui_tp / len(true_ui) if true_ui else 1.0

    expected_unknown = bool((case.get("ground_truth") or {}).get("requires_uncertainty"))
    predicted_unknown = bool(prediction.get("insufficient_evidence"))
    uncertainty_correct = predicted_unknown == expected_unknown

    return {
        "case_id": case.get("case_id"),
        "family_id": case.get("family_id") or case_family_id(str(case.get("case_id") or "")),
        "workflow_assignment_precision": round(precision, 6),
        "workflow_assignment_recall": round(recall, 6),
        "workflow_assignment_f1": round(f1, 6),
        "cross_workflow_contamination": round(contamination, 6),
        "workflow_count_exact": len(predicted) == len(truth),
        "interruption_rejection": round(noise_rejected, 6),
        "ordering_accuracy": round(correct_order / total_order, 6) if total_order else 1.0,
        "checkpoint_recall": round(recovered_checkpoints / total_checkpoints, 6) if total_checkpoints else 1.0,
        "automation_relevance_precision": round(rel_precision, 6),
        "automation_relevance_recall": round(rel_recall, 6),
        "ui_mechanic_precision": round(ui_precision, 6),
        "ui_mechanic_recall": round(ui_recall, 6),
        "uncertainty_correct": uncertainty_correct,
    }

def _summary(
    cases: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    by_prediction: dict[str, bool],
) -> dict[str, Any]:
    metric_names = [
        "workflow_assignment_precision",
        "workflow_assignment_recall",
        "workflow_assignment_f1",
        "cross_workflow_contamination",
        "interruption_rejection",
        "ordering_accuracy",
        "checkpoint_recall",
        "automation_relevance_precision",
        "automation_relevance_recall",
        "ui_mechanic_precision",
        "ui_mechanic_recall",
    ]
    out = {name: round(mean(float(row[name]) for row in rows), 6) for name in metric_names}
    out["cases"] = len(rows)
    out["workflow_count_accuracy"] = round(mean(1.0 if row["workflow_count_exact"] else 0.0 for row in rows), 6)
    out["uncertainty_accuracy"] = round(mean(1.0 if row["uncertainty_correct"] else 0.0 for row in rows), 6)

    required = [
        (case, row) for case, row in zip(cases, rows)
        if bool((case.get("ground_truth") or {}).get("requires_uncertainty"))
    ]
    ordinary = [
        (case, row) for case, row in zip(cases, rows)
        if not bool((case.get("ground_truth") or {}).get("requires_uncertainty"))
    ]
    out["required_uncertainty_recall"] = round(
        mean(
            1.0 if by_prediction.get(str(case.get("case_id") or ""), False) else 0.0
            for case, _row in required
        ),
        6,
    ) if required else None
    out["unnecessary_uncertainty_rate"] = round(
        mean(
            1.0 if by_prediction.get(str(case.get("case_id") or ""), False) else 0.0
            for case, _row in ordinary
        ),
        6,
    ) if ordinary else None
    return out


def aggregate(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(row.get("case_id") or ""): row for row in predictions}
    missing = [case["case_id"] for case in cases if case["case_id"] not in by_id]
    if missing:
        raise ValueError(f"missing predictions for {len(missing)} cases: {missing[:5]}")

    rows = [score_case(case, by_id[case["case_id"]]) for case in cases]
    by_prediction = {
        str(item.get("case_id") or ""): bool(item.get("insufficient_evidence"))
        for item in predictions
    }

    # Keep the historical case-level metrics at the top level for compatibility.
    out = _summary(cases, rows, by_prediction)
    out["required_uncertainty_recall"] = (
        1.0 if out["required_uncertainty_recall"] is None else out["required_uncertainty_recall"]
    )
    out["unnecessary_uncertainty_rate"] = (
        0.0 if out["unnecessary_uncertainty_rate"] is None else out["unnecessary_uncertainty_rate"]
    )
    out["per_case"] = rows

    # The independent scenario family is the statistical unit. Variants of the
    # first ten families must not count three times more than later families.
    grouped: dict[str, list[int]] = {}
    for index, case in enumerate(cases):
        fid = str(case.get("family_id") or case_family_id(str(case.get("case_id") or "")))
        grouped.setdefault(fid, []).append(index)

    family_rows: list[dict[str, Any]] = []
    for fid, indices in sorted(grouped.items()):
        family_cases = [cases[index] for index in indices]
        scored_rows = [rows[index] for index in indices]
        summary = _summary(family_cases, scored_rows, by_prediction)
        summary["family_id"] = fid
        family_rows.append(summary)

    family_metric_names = [
        "workflow_assignment_precision",
        "workflow_assignment_recall",
        "workflow_assignment_f1",
        "cross_workflow_contamination",
        "interruption_rejection",
        "ordering_accuracy",
        "checkpoint_recall",
        "automation_relevance_precision",
        "automation_relevance_recall",
        "ui_mechanic_precision",
        "ui_mechanic_recall",
        "workflow_count_accuracy",
        "uncertainty_accuracy",
    ]
    family_macro = {
        name: round(mean(float(row[name]) for row in family_rows), 6)
        for name in family_metric_names
    }
    required_family_values = [
        float(row["required_uncertainty_recall"])
        for row in family_rows
        if row["required_uncertainty_recall"] is not None
    ]
    ordinary_family_values = [
        float(row["unnecessary_uncertainty_rate"])
        for row in family_rows
        if row["unnecessary_uncertainty_rate"] is not None
    ]
    family_macro["required_uncertainty_recall"] = round(mean(required_family_values), 6) if required_family_values else 1.0
    family_macro["unnecessary_uncertainty_rate"] = round(mean(ordinary_family_values), 6) if ordinary_family_values else 0.0
    family_macro["families"] = len(family_rows)
    family_macro["per_family"] = family_rows
    out["family_macro"] = family_macro
    return out

THRESHOLDS = {
    "workflow_assignment_f1": (">=", 0.90),
    "cross_workflow_contamination": ("<=", 0.05),
    "workflow_count_accuracy": (">=", 0.90),
    "checkpoint_recall": (">=", 0.90),
    "interruption_rejection": (">=", 0.95),
    "required_uncertainty_recall": (">=", 0.90),
    "unnecessary_uncertainty_rate": ("<=", 0.10),
}

def acceptance(report: dict[str, Any]) -> dict[str, Any]:
    gate = report.get("family_macro") if isinstance(report.get("family_macro"), dict) else report
    checks: dict[str, bool] = {}
    observed: dict[str, float] = {}
    for name, (op, threshold) in THRESHOLDS.items():
        value = float(gate[name])
        observed[name] = value
        checks[name] = value >= threshold if op == ">=" else value <= threshold
    return {
        "passed": all(checks.values()),
        "basis": "family_macro" if gate is not report else "case_level",
        "checks": checks,
        "observed": observed,
        "thresholds": {name: {"operator": op, "value": threshold} for name, (op, threshold) in THRESHOLDS.items()},
    }

def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}: each line must be an object")
            rows.append(value)
    return rows

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score workflow reconstruction predictions")
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--cases", help="optional exported JSONL corpus; default uses deterministic built-in fixtures")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    cases = load_cases(Path(args.cases)) if args.cases else load_cases()
    report = aggregate(cases, _jsonl(Path(args.predictions)))
    result = {"metrics": report, "acceptance": acceptance(report)}
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["acceptance"]["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
