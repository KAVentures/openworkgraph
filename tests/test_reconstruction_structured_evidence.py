"""Structured evidence extraction and production global assignment integration."""
import unittest
from unittest.mock import patch

from server.reconstruction_structured_evidence import structured_evidence_proposals


def event(name, tags, *, org="tenant"):
    return {"event_id": name, "organization_id": org,
            "metadata": {"semantic_category_ids": tags}}


def queue(rows):
    return {"version": 1, "evidence_sha256": "a" * 64,
            "queue": rows, "total_candidates": len(rows), "truncated": False}


def candidate(a, b, risk=None):
    return {"event_id": a, "toward_event_id": b,
            "review_status": "conflicted" if risk else "inspect",
            "risk_reasons": risk or []}


class StructuredEvidenceTests(unittest.TestCase):
    def test_structured_categories_feed_global_optimizer(self):
        events = [event("a", ["alpha", "beta"]), event("b", ["beta", "alpha"])]
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue",
                   return_value=queue([candidate("a", "b")])), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue",
            return_value=queue([candidate("a", "b")])
        ):
            result = structured_evidence_proposals(events, consent=True)
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["structured_category_events"], 2)
        self.assertFalse(result["automatic_merge"])
        self.assertTrue(result["requires_human_review"])

    def test_conflicting_duplicate_and_unsafe_candidate_abstain(self):
        events = [event("a", ["alpha", "beta"]),
                  event("a", ["alpha", "beta"]), event("b", ["alpha", "beta"])]
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue",
                   return_value=queue([candidate("a", "b")])), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue",
            return_value=queue([candidate("a", "b")])
        ):
            result = structured_evidence_proposals(events, consent=True)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["rejected_category_event_ids"], 1)

    def test_invalid_category_and_scope_risk_abstain(self):
        events = [event("a", ["alpha", "beta"]), event("b", ["alpha", "beta"], org="other"),
                  event("c", ["private text!"])]
        rows = [candidate("a", "b", ["missing_or_cross_scope"])]
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue",
                   return_value=queue(rows)), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue",
            return_value=queue(rows)
        ):
            result = structured_evidence_proposals(events, consent=True)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["rejected_category_event_ids"], 1)

    def test_consent_and_types(self):
        with self.assertRaises(PermissionError):
            structured_evidence_proposals([], consent=False)
        with self.assertRaises(ValueError):
            structured_evidence_proposals([{"event_id": 1}], consent=True)
        with self.assertRaises(ValueError):
            structured_evidence_proposals([event("a", ["alpha"])], consent=True, minimum_shared=1)


if __name__ == "__main__":
    unittest.main()
