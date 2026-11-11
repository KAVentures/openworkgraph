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

    def test_empty_gold_refused(self):
        with self.assertRaisesRegex(ValueError, "empty gold"):
            score(gold(), predicted())


if __name__ == "__main__":
    unittest.main()
