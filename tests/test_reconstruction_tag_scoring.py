"""Semantic-tag scoring contract and real source queue integration."""
import unittest
from unittest.mock import patch

from server.reconstruction_tag_scoring import tagged_review_proposals


def source(rows):
    return {"evidence_sha256": "digest", "queue": rows, "truncated": False}


def candidate(a, b, reasons=()):
    return {"event_id": a, "toward_event_id": b, "relation": "same_tab_unreferenced",
            "risk_reasons": list(reasons)}


class TagScoringTests(unittest.TestCase):
    def test_requires_explicit_consent(self):
        with self.assertRaises(PermissionError):
            tagged_review_proposals([], {}, consent=False)

    def test_matching_categories_produce_non_authoritative_review(self):
        with patch("server.reconstruction_tag_scoring.prioritized_review_queue",
                   return_value=source([candidate("a", "b")])):
            result = tagged_review_proposals([], {"a": ["invoice", "approval"],
                                                    "b": ["invoice", "approval"]}, consent=True)
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["tag_overlap_score"], 1)
        self.assertFalse(result["authoritative"])
        self.assertFalse(result["proposals"][0]["automatic_merge"])
        self.assertEqual(result["evidence_sha256"], "digest")
        self.assertNotIn("invoice", str(result))

    def test_conflicts_and_tainted_evidence_never_score(self):
        for reason in ("conflicting_explicit_records", "missing_or_cross_scope",
                       "tainted_source_evidence"):
            with self.subTest(reason=reason), patch(
                "server.reconstruction_tag_scoring.prioritized_review_queue",
                return_value=source([candidate("a", "b", [reason])])
            ):
                self.assertFalse(tagged_review_proposals([], {
                    "a": ["invoice", "approval"], "b": ["invoice", "approval"]
                }, consent=True)["proposals"])

    def test_ambiguous_equal_competition_abstains(self):
        with patch("server.reconstruction_tag_scoring.prioritized_review_queue",
                   return_value=source([candidate("a", "b"), candidate("a", "c")])):
            result = tagged_review_proposals([], {x: ["invoice", "approval"]
                                                    for x in "abc"}, consent=True)
        self.assertEqual(result["proposals"], [])

    def test_rejects_truncated_queue_and_invalid_tags(self):
        with patch("server.reconstruction_tag_scoring.prioritized_review_queue",
                   return_value={"truncated": True, "queue": []}):
            with self.assertRaises(ValueError):
                tagged_review_proposals([], {}, consent=True)
        with self.assertRaises(ValueError):
            tagged_review_proposals([], {"a": ["Patient Name"]}, consent=True)

    def test_real_empty_queue(self):
        result = tagged_review_proposals([], {}, consent=True)
        self.assertEqual(result["proposals"], [])
        self.assertTrue(result["canonical_membership_unchanged"])


if __name__ == "__main__":
    unittest.main()
