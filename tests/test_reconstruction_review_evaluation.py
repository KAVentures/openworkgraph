"""Gold evaluation regression tests; these synthetic cases are NOT held-out scores."""
import unittest

from server.reconstruction_review_evaluation import evaluate_review_queue


def ptr(ref):
    return {"provider": "github", "resource_kind": "issue", "resource_ref": ref}


def pair(a, b):
    return [ptr(a), ptr(b)]


def row(event, candidates):
    return {"window": "current", "event_id": event,
            "candidate_resource_pairs": candidates,
            "review_status": "ambiguous_multiple_pairs" if len(candidates) > 1 else "observed_pair_needs_review",
            "confirmed_case": False}


def review(rows, **overrides):
    payload = {"coverage_incomplete": False, "event_reviews": rows,
               "event_reviews_count": len(rows), "unlisted_events_are_unassigned": True,
               "no_case_identity_inferred": True}
    payload.update(overrides)
    return payload


def gold(event, expected):
    return {"window": "current", "event_id": event, "expected_pair": expected}


class EvaluationTests(unittest.TestCase):
    def test_true_false_positives_and_cross_case_contamination(self):
        review_payload = review([
            row("correct", [pair("a", "b")]),
            row("cross", [pair("c", "d")]),
            row("unrelated", [pair("e", "f")]),
        ])
        labels = [gold("correct", pair("a", "b")),
                  gold("cross", pair("a", "b")),
                  gold("unrelated", None),
                  gold("missed", pair("g", "h"))]
        out = evaluate_review_queue(review_payload, labels)
        self.assertEqual((out["true_positive"], out["false_positive"], out["false_negative"]), (1, 2, 2))
        self.assertEqual(out["cross_case_contamination"], 1)
        self.assertEqual(out["unrelated_false_positives"], 1)
        self.assertAlmostEqual(out["precision"], 1 / 3)
        self.assertAlmostEqual(out["recall"], 1 / 3)
        self.assertAlmostEqual(out["f1"], 1 / 3)
        self.assertFalse(out["held_out_verified"])

    def test_ambiguous_abstains_and_does_not_inflate_precision(self):
        out = evaluate_review_queue(review([row("e", [pair("a", "b"), pair("a", "c")])]),
                                    [gold("e", pair("a", "b"))])
        self.assertEqual(out["abstentions"], 1)
        self.assertEqual(out["true_positive"], 0)
        self.assertEqual(out["false_negative"], 1)
        self.assertEqual(out["cross_case_contamination"], 0)
        self.assertEqual(out["unrelated_false_positives"], 0)

    def test_missing_gold_labels_refused(self):
        with self.assertRaisesRegex(ValueError, "unlabeled"):
            evaluate_review_queue(review([row("secret", [pair("a", "b")])]),
                                  [gold("other", None)])

    def test_duplicate_gold_and_review_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate gold"):
            evaluate_review_queue(review([]), [gold("e", None), gold("e", None)])
        with self.assertRaisesRegex(ValueError, "duplicate review"):
            evaluate_review_queue(review([row("e", [pair("a", "b")])] * 2),
                                  [gold("e", pair("a", "b"))])

    def test_truncated_or_confirmed_evidence_refused(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            evaluate_review_queue(review([], coverage_incomplete=True), [gold("e", None)])
        bad = row("e", [pair("a", "b")])
        bad["confirmed_case"] = True
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            evaluate_review_queue(review([bad]), [gold("e", pair("a", "b"))])

    def test_missing_coverage_and_provenance_are_refused(self):
        labels = [gold("e", pair("a", "b"))]
        base = review([row("e", [pair("a", "b")])])
        for field in ("coverage_incomplete", "unlisted_events_are_unassigned",
                      "no_case_identity_inferred", "event_reviews_count"):
            with self.subTest(field=field):
                invalid = dict(base)
                invalid.pop(field)
                with self.assertRaises(ValueError):
                    evaluate_review_queue(invalid, labels)
        with self.assertRaisesRegex(ValueError, "count"):
            evaluate_review_queue(review(base["event_reviews"], event_reviews_count=2), labels)

    def test_reversed_pointer_order_same_pair(self):
        out = evaluate_review_queue(review([row("e", [pair("b", "a")])]),
                                    [gold("e", pair("a", "b"))])
        self.assertEqual(out["true_positive"], 1)
        self.assertEqual(out["f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
