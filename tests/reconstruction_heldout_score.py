"""Strict held-out scoring for R1/R2 event-to-case candidate assignments.

Inputs are JSON objects with `events` arrays. Gold: [{event_id, window, case_id}] (null means unrelated), or
[{event_id, window, case_ids: [...]}] for independently labeled multiple
legitimate assignments. The two gold formats must not be mixed per row.
Predictions: [{event_id, window,
candidate_case_ids: [...] }]. Candidate IDs must be independently mapped to
the gold case namespace BEFORE scoring. This script does not perform that
mapping or claim a held-out dataset exists.

Do not score on training fixtures, and do not call pair IDs confirmed cases.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _index(document, *, gold):
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise ValueError("expected object containing events list")
    result = {}
    for row in document["events"]:
        if not isinstance(row, dict):
            raise ValueError("event must be an object")
        event_id, window = row.get("event_id"), row.get("window")
        if not all(isinstance(v, str) and v for v in (event_id, window)):
            raise ValueError("event_id and window must be nonempty strings")
        key = (window, event_id)
        if key in result:
            raise ValueError("duplicate event/window in evaluation data")
        if gold:
            if "case_ids" in row:
                if "case_id" in row:
                    raise ValueError("ambiguous gold case_id and case_ids fields")
                case_ids = row["case_ids"]
                if (not isinstance(case_ids, list) or
                        any(not isinstance(v, str) or not v for v in case_ids) or
                        len(set(case_ids)) != len(case_ids)):
                    raise ValueError("invalid gold case_ids")
                result[key] = set(case_ids)
            else:
                if "case_id" not in row:
                    raise ValueError("missing gold case_id or case_ids")
                case_id = row["case_id"]
                if case_id is not None and (not isinstance(case_id, str) or not case_id):
                    raise ValueError("invalid gold case_id")
                result[key] = {case_id} if case_id is not None else set()
        else:
            candidates = row.get("candidate_case_ids")
            if not isinstance(candidates, list) or any(
                not isinstance(v, str) or not v for v in candidates
            ) or len(set(candidates)) != len(candidates):
                raise ValueError("invalid prediction candidates")
            result[key] = set(candidates)
    return result


def score(gold_document, prediction_document, *, _include_windows=True):
    gold = _index(gold_document, gold=True)
    predictions = _index(prediction_document, gold=False)
    if not gold:
        raise ValueError("empty gold set: cannot report metrics")
    if set(predictions) != set(gold):
        missing = len(set(gold) - set(predictions))
        extra = len(set(predictions) - set(gold))
        raise ValueError(f"event universe mismatch: missing={missing}, extra={extra}")
    tp = fp = fn = contamination_events = abstentions = 0
    exact_match_events = unrelated_events = unrelated_false_positive_events = 0
    labeled_wrong_case_events = 0
    for key, expected in gold.items():
        proposed = predictions[key]
        tp += len(expected & proposed)
        fp += len(proposed - expected)
        fn += len(expected - proposed)
        contamination_events += bool(proposed - expected)
        abstentions += bool(expected and not proposed)
        exact_match_events += (expected == proposed)
        unrelated_events += not expected
        unrelated_false_positive_events += bool(not expected and proposed)
        labeled_wrong_case_events += bool(expected and (proposed - expected))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result = {
        "gold_events": len(gold),
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "cross_case_contamination_events": contamination_events,
        "cross_case_contamination_rate": contamination_events / len(gold),
        "abstentions_on_labeled_events": abstentions,
        "exact_match_events": exact_match_events,
        "exact_match_rate": exact_match_events / len(gold),
        "unrelated_events": unrelated_events,
        "false_positive_unrelated_events": unrelated_false_positive_events,
        "false_positive_unrelated_rate": (unrelated_false_positive_events / unrelated_events
                                          if unrelated_events else None),
        "wrong_case_labeled_events": labeled_wrong_case_events,
        "evaluation_scope": "provided_labels_only",
    }
    # Macro per-case scores expose failures hidden by high-volume cases.
    # A prediction for a case absent from gold remains a false positive in
    # micro metrics; per-case evaluation is restricted to labeled cases.
    case_ids = sorted({case for expected in gold.values() for case in expected})
    per_case = {}
    for case_id in case_ids:
        case_tp = sum(case_id in gold[key] and case_id in predictions[key] for key in gold)
        case_fp = sum(case_id not in gold[key] and case_id in predictions[key] for key in gold)
        case_fn = sum(case_id in gold[key] and case_id not in predictions[key] for key in gold)
        case_precision = case_tp / (case_tp + case_fp) if case_tp + case_fp else 0.0
        case_recall = case_tp / (case_tp + case_fn) if case_tp + case_fn else 0.0
        case_f1 = (2 * case_precision * case_recall / (case_precision + case_recall)
                   if case_precision + case_recall else 0.0)
        per_case[case_id] = {
            "support": case_tp + case_fn,
            "true_positive": case_tp, "false_positive": case_fp,
            "false_negative": case_fn, "precision": case_precision,
            "recall": case_recall, "f1": case_f1,
        }
    result["per_case"] = per_case
    result["macro_f1_labeled_cases"] = (
        sum(item["f1"] for item in per_case.values()) / len(per_case)
        if per_case else None)
    if _include_windows:
        # Report each evidence window independently to reveal masked leakage.
        result["by_window"] = {}
        for window in sorted({w for w, _ in gold}):
            gold_slice = {"events": [
                {"window": w, "event_id": event, "case_ids": sorted(cases)}
                for (w, event), cases in gold.items() if w == window]}
            predicted_slice = {"events": [
                {"window": w, "event_id": event,
                 "candidate_case_ids": sorted(predictions[(w, event)])}
                for (w, event) in gold if w == window]}
            result["by_window"][window] = score(
                gold_slice, predicted_slice, _include_windows=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold", type=Path, help="sealed, independently labeled gold JSON")
    parser.add_argument("predictions", type=Path, help="frozen prediction JSON")
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.gold.read_text(encoding="utf-8")),
                           json.loads(args.predictions.read_text(encoding="utf-8"))),
                     sort_keys=True))


if __name__ == "__main__":
    main()
