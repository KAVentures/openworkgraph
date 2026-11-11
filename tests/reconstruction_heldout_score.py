"""Strict held-out scoring for R1/R2 event-to-case candidate assignments.

Inputs are JSON objects with `events` arrays. Gold: [{event_id, window, case_id}]
where case_id may be null (unrelated). Predictions: [{event_id, window,
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
            case_id = row.get("case_id")
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


def score(gold_document, prediction_document):
    gold = _index(gold_document, gold=True)
    predictions = _index(prediction_document, gold=False)
    if not gold:
        raise ValueError("empty gold set: cannot report metrics")
    if set(predictions) != set(gold):
        missing = len(set(gold) - set(predictions))
        extra = len(set(predictions) - set(gold))
        raise ValueError(f"event universe mismatch: missing={missing}, extra={extra}")
    tp = fp = fn = contamination_events = abstentions = 0
    for key, expected in gold.items():
        proposed = predictions[key]
        tp += len(expected & proposed)
        fp += len(proposed - expected)
        fn += len(expected - proposed)
        contamination_events += bool(proposed - expected)
        abstentions += bool(expected and not proposed)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "gold_events": len(gold),
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "cross_case_contamination_events": contamination_events,
        "cross_case_contamination_rate": contamination_events / len(gold),
        "abstentions_on_labeled_events": abstentions,
        "evaluation_scope": "provided_labels_only",
    }


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
