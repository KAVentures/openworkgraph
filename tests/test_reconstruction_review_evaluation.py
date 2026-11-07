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


def gold(event, expected):
    return {"window": "current", "event_id": event, "expected_pair": expected}


class EvaluationTests(unittest.TestCase):
    def test_true_false_positives_and_cross_case_contamination(self):
        review = {"coverage_incomplete": False, "event_reviews": [
            row("correct", [pair("a", "b")]),
            row("cross", [pair("c", "d")]),
            row("unrelated", [pair("e", "f")]),
        ]}
        labels = [gold("correct", pair("a", "b")),
                  gold("cross", pair("a", "b")),
                  gold("unrelated", None),
                  gold("missed", pair("g", "h"))]
        out = evaluate_review_queue(review, labels)
        self.assertEqual((out["true_positive"], out["false_positive"], out["false_negative"]), (1, 2, 2))
        self.assertEqual(out["cross_case_contamination"], 1)
        self.assertEqual(out["unrelated_false_positives"], 1)
        self.assertAlmostEqual(out["precision"], 1 / 3)
        self.assertAlmostEqual(out["recall"], 1 / 3)
        self.assertAlmostEqual(out["f1"], 1 / 3)
        self.assertFalse(out["held_out_verified"])

    def test_ambiguous_abstains_and_does_not_inflate_precision(self):
        out = evaluate_review_queue({"event_reviews": [row("e", [pair("a", "b"), pair("a", "c")])]},
                                    [gold("e", pair("a", "b"))])
        self.assertEqual(out["abstentions"], 1)
        self.assertEqual(out["true_positive"], 0)
        self.assertEqual(out["false_negative"], 1)
        self.assertEqual(out["cross_case_contamination"], 0)
        self.assertEqual(out["unrelated_false_positives"], 0)

    def test_missing_gold_labels_refused(self):
        with self.assertRaisesRegex(ValueError, "unlabeled"):
            evaluate_review_queue({"event_reviews": [row("secret", [pair("a", "b")])]},
                                  [gold("other", None)])

    def test_duplicate_gold_and_review_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate gold"):
            evaluate_review_queue({"event_reviews": []}, [gold("e", None), gold("e", None)])
        with self.assertRaisesRegex(ValueError, "duplicate review"):
            evaluate_review_queue({"event_reviews": [row("e", [pair("a", "b")])] * 2},
                                  [gold("e", pair("a", "b"))])

    def test_truncated_or_confirmed_evidence_refused(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            evaluate_review_queue({"coverage_incomplete": True, "event_reviews": []}, [gold("e", None)])
        bad = row("e", [pair("a", "b")])
        bad["confirmed_case"] = True
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            evaluate_review_queue({"event_reviews": [bad]}, [gold("e", pair("a", "b"))])

    def test_reversed_pointer_order_same_pair(self):
        out = evaluate_review_queue({"event_reviews": [row("e", [pair("b", "a")])]},
                                    [gold("e", pair("a", "b"))])
        self.assertEqual(out["true_positive"], 1)
        self.assertEqual(out["f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
