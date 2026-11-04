"""Production global matching of consented discriminative category observations."""
import unittest
from unittest.mock import patch

from server.reconstruction_discriminative_categories import discriminative_category_proposals


def event(eid, values, org="tenant"):
    return {"event_id": eid, "organization_id": org,
            "metadata": {"semantic_category_ids": values}}


def source(rows):
    return {"version": 1, "evidence_sha256": "a" * 64, "queue": rows,
            "total_candidates": len(rows), "truncated": False}


def pair(a, b, risk=None):
    return {"event_id": a, "toward_event_id": b,
            "review_status": "conflicted" if risk else "inspect",
            "risk_reasons": risk or []}


def evaluate(events, rows):
    with patch("server.reconstruction_discriminative_categories.prioritized_review_queue",
               return_value=source(rows)), patch(
        "server.reconstruction_semantic_assignment.prioritized_review_queue",
        return_value=source(rows)
    ):
        return discriminative_category_proposals(events, consent=True)


class DiscriminativeCategoriesTests(unittest.TestCase):
    def test_rare_shared_evidence_matches_and_generic_only_abstains(self):
        events = [event("a", ["generic", "invoice", "review"]),
                  event("b", ["generic", "invoice", "review"]),
                  event("c", ["generic", "other"]), event("d", ["generic", "other"]),
                  event("e", ["generic", "other"])]
        result = evaluate(events, [pair("a", "b"), pair("c", "d")])
        self.assertEqual([(p["event_id"], p["toward_event_id"])
                          for p in result["proposals"]], [("a", "b")])
        self.assertEqual(result["suppressed_ubiquitous_category_count"], 1)
        self.assertFalse(result["automatic_merge"])

    def test_competing_equal_candidates_abstain(self):
        events = [event("a", ["alpha", "beta"]),
                  event("b", ["alpha", "beta"]),
                  event("c", ["alpha", "beta"])]
        rows = [pair("a", "b", ["competing_candidate_components"]),
                pair("a", "c", ["competing_candidate_components"])]
        result = evaluate(events, rows)
        self.assertEqual(result["proposals"], [])
        self.assertGreater(result["abstained_ambiguous_endpoints"], 0)

    def test_cross_scope_and_source_taint_veto(self):
        events = [event("a", ["alpha", "beta"]),
                  event("b", ["alpha", "beta"], org="other")]
        result = evaluate(events, [pair("a", "b", ["missing_or_cross_scope"])])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["scored_candidate_pairs"], 0)

    def test_duplicate_and_invalid_evidence_suppressed(self):
        events = [event("a", ["alpha", "beta"]),
                  event("a", ["alpha", "beta"]),
                  event("b", ["alpha", "beta"]),
                  event("c", ["raw private text!"])]
        result = evaluate(events, [pair("a", "b")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["rejected_category_event_ids"], 2)

    def test_consent_and_bounds(self):
        with self.assertRaises(PermissionError):
            discriminative_category_proposals([], consent=False)
        with self.assertRaises(ValueError):
            discriminative_category_proposals([], consent=True, minimum_shared=1)
        with self.assertRaises(ValueError):
            discriminative_category_proposals([], consent=True, max_document_fraction=0.2)
        with self.assertRaises(ValueError):
            discriminative_category_proposals([{"event_id": 42}], consent=True)


if __name__ == "__main__":
    unittest.main()
