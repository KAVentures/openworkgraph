"""Evaluate reconstruction review queues against independently labeled event pairs.

This is a *measurement tool*, not a source of truth. Unlisted events are not
automatically negative; the gold set must explicitly enumerate evaluated
events and their permitted case/resource-pair associations. Never evaluate
on the same examples used to tune an assignment algorithm.
"""
from __future__ import annotations

import argparse
import json
from typing import Any


def evaluate_review_queue(review: dict[str, Any], gold: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute micro precision/recall/F1 for *unambiguous proposals* only.

    Gold rows have window, event_id, and expected_pair (canonical pair of
    provider/kind/ref triples, or null for an explicitly unrelated event).
    Ambiguous review rows are abstentions, not automatically correct matches.
    """
    if not isinstance(review, dict) or not isinstance(review.get("event_reviews"), list):
        raise ValueError("invalid review queue")
    if review.get("coverage_incomplete") is not False:
        raise ValueError("complete coverage must be explicitly verified before scoring")
    if review.get("unlisted_events_are_unassigned") is not True or review.get("no_case_identity_inferred") is not True:
        raise ValueError("review must preserve unassigned events and non-authoritative case identity")
    if review.get("event_reviews_count") != len(review["event_reviews"]):
        raise ValueError("review event count does not match its evidence rows")
    if not isinstance(gold, list) or not gold:
        raise ValueError("nonempty independently labeled gold rows required")

    def canonical(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("expected pair must have exactly two pointers")
        triples = []
        for p in value:
            if not isinstance(p, dict):
                raise ValueError("invalid gold/review pointer")
            t = [p.get(k) for k in ("provider", "resource_kind", "resource_ref")]
            if any(not isinstance(v, str) or not v for v in t):
                raise ValueError("invalid gold/review pointer identity")
            triples.append(t)
        if triples[0] == triples[1]:
            raise ValueError("self pair is invalid")
        return json.dumps(sorted(triples), separators=(",", ":"))

    expected: dict[tuple[str, str], str | None] = {}
    for item in gold:
        if not isinstance(item, dict):
            raise ValueError("invalid gold row")
        key = (item.get("window"), item.get("event_id"))
        if any(not isinstance(x, str) or not x for x in key) or key in expected:
            raise ValueError("invalid or duplicate gold event")
        if "expected_pair" not in item:
            raise ValueError("gold must explicitly label unrelated events")
        expected[key] = canonical(item["expected_pair"])

    proposals: dict[tuple[str, str], str | None] = {}
    abstentions = 0
    for row in review["event_reviews"]:
        if not isinstance(row, dict) or row.get("confirmed_case") is not False:
            raise ValueError("review rows must remain unconfirmed")
        key = (row.get("window"), row.get("event_id"))
        if key not in expected or key in proposals:
            raise ValueError("unlabeled or duplicate review event")
        pairs = row.get("candidate_resource_pairs")
        if not isinstance(pairs, list) or not pairs:
            raise ValueError("review row missing evidence")
        candidates = [canonical(p) for p in pairs]
        if len(set(candidates)) != len(candidates):
            raise ValueError("duplicate pair evidence")
        if len(candidates) > 1:
            if row.get("review_status") != "ambiguous_multiple_pairs":
                raise ValueError("ambiguous event incorrectly classified")
            proposals[key] = None
            abstentions += 1
        else:
            if row.get("review_status") != "observed_pair_needs_review":
                raise ValueError("single-pair event incorrectly classified")
            proposals[key] = candidates[0]

    tp = fp = fn = contamination = unrelated_false_positives = 0
    for key, truth in expected.items():
        pred = proposals.get(key)
        if pred is not None and pred == truth:
            tp += 1
        elif pred is not None:
            fp += 1
            if truth is not None:
                contamination += 1
            else:
                unrelated_false_positives += 1
        if truth is not None and pred != truth:
            fn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "gold_events": len(expected),
        "positive_gold_events": sum(x is not None for x in expected.values()),
        "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "cross_case_contamination": contamination,
        "unrelated_false_positives": unrelated_false_positives,
        "abstentions": abstentions,
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "metric_scope": "exact event-to-resource-pair proposals, not confirmed business cases",
        "held_out_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a review queue against explicit gold event-pair labels")
    parser.add_argument("review_json")
    parser.add_argument("gold_json")
    args = parser.parse_args()
    try:
        with open(args.review_json, encoding="utf-8") as stream:
            review = json.load(stream)
        with open(args.gold_json, encoding="utf-8") as stream:
            gold = json.load(stream)
        print(json.dumps(evaluate_review_queue(review, gold), sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"evaluation refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
