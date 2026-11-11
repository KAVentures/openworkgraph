"""Metric-contract tests; synthetic examples are NOT held-out performance results."""
import unittest

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "reconstruction_heldout_score", Path(__file__).with_name("reconstruction_heldout_score.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
score = _module.score


def gold(*rows):
    return {"events": [{"window": "current", "event_id": event, "case_id": case}
                       for event, case in rows]}


def predicted(*rows):
    return {"events": [{"window": "current", "event_id": event,
                        "candidate_case_ids": list(cases)} for event, cases in rows]}


class ReconstructionHeldoutScoreTests(unittest.TestCase):
    def test_cross_case_leakage_counts_as_false_positive_even_with_correct_case(self):
        result = score(gold(("a", "A"), ("b", "B"), ("noise", None)),
                       predicted(("a", ("A", "B")), ("b", ("B",)), ("noise", ())))
        self.assertEqual((result["true_positive"], result["false_positive"],
                          result["false_negative"]), (2, 1, 0))
        self.assertAlmostEqual(result["precision"], 2 / 3)
        self.assertEqual(result["recall"], 1)
        self.assertAlmostEqual(result["f1"], 0.8)
        self.assertEqual(result["cross_case_contamination_events"], 1)

    def test_abstention_is_not_false_positive_but_reduces_recall(self):
        result = score(gold(("a", "A"), ("noise", None)),
                       predicted(("a", ()), ("noise", ())))
        self.assertEqual(result["false_negative"], 1)
        self.assertEqual(result["abstentions_on_labeled_events"], 1)
        self.assertEqual(result["f1"], 0)

    def test_missing_or_extra_event_refused_instead_of_inflating_metrics(self):
        with self.assertRaisesRegex(ValueError, "universe mismatch"):
            score(gold(("a", "A"), ("b", "B")), predicted(("a", ("A",))))
        with self.assertRaisesRegex(ValueError, "universe mismatch"):
            score(gold(("a", "A")), predicted(("a", ("A",)), ("extra", ())))

    def test_duplicate_event_key_and_duplicate_candidate_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate event"):
            score(gold(("a", "A"), ("a", "A")), predicted(("a", ("A",))))
        with self.assertRaisesRegex(ValueError, "invalid prediction"):
            score(gold(("a", "A")), predicted(("a", ("A", "A"))))

    def test_multiple_legitimate_gold_cases_do_not_count_as_contamination(self):
        labeled = {"events": [
            {"window": "current", "event_id": "shared", "case_ids": ["A", "B"]},
            {"window": "current", "event_id": "noise", "case_ids": []},
        ]}
        correct = score(labeled, predicted(("shared", ("A", "B")), ("noise", ())))
        self.assertEqual((correct["true_positive"], correct["false_positive"],
                          correct["false_negative"]), (2, 0, 0))
        self.assertEqual(correct["f1"], 1)
        self.assertEqual(correct["cross_case_contamination_events"], 0)
        missing = score(labeled, predicted(("shared", ("A",)), ("noise", ())))
        self.assertEqual((missing["true_positive"], missing["false_positive"],
                          missing["false_negative"]), (1, 0, 1))

    def test_gold_multiple_case_labels_must_be_explicit_and_unique(self):
        for row in (
            {"event_id": "a", "window": "current", "case_ids": ["A", "A"]},
            {"event_id": "a", "window": "current", "case_ids": ["A"], "case_id": "A"},
            {"event_id": "a", "window": "current"},
        ):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "gold"):
                score({"events": [row]}, predicted(("a", ("A",))))

    def test_contamination_breakdown_separates_unrelated_and_wrong_case(self):
        result = score(
            gold(("a", "A"), ("b", "B"), ("noise", None), ("quiet", None)),
            predicted(("a", ("A", "B")), ("b", ("B",)),
                      ("noise", ("A",)), ("quiet", ())))
        self.assertEqual(result["cross_case_contamination_events"], 2)
        self.assertEqual(result["wrong_case_labeled_events"], 1)
        self.assertEqual(result["unrelated_events"], 2)
        self.assertEqual(result["false_positive_unrelated_events"], 1)
        self.assertEqual(result["false_positive_unrelated_rate"], 0.5)
        self.assertEqual(result["exact_match_events"], 2)
        self.assertEqual(result["exact_match_rate"], 0.5)

    def test_no_unrelated_population_reports_undefined_rate_not_zero(self):
        result = score(gold(("a", "A")), predicted(("a", ("A",))))
        self.assertIsNone(result["false_positive_unrelated_rate"])
        self.assertEqual(result["exact_match_rate"], 1)

    def test_empty_gold_refused(self):
        with self.assertRaisesRegex(ValueError, "empty gold"):
            score(gold(), predicted())


if __name__ == "__main__":
    unittest.main()
