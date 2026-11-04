"""Regression tests for global opaque-tag proposal selection."""
import unittest
from unittest.mock import patch

from server.reconstruction_tag_global_assignment import globally_tagged_proposals

DIGEST = "a" * 64


def candidate(a, b, *, risk=None, status="inspect"):
    return {
        "event_id": a, "toward_event_id": b,
        "relation": "same_tab_unreferenced",
        "review_status": status,
        "risk_reasons": risk or [],
    }


def queue(rows):
    return {
        "evidence_sha256": DIGEST, "queue": rows,
        "total_candidates": len(rows), "truncated": False,
    }


class GlobalTagAssignmentTests(unittest.TestCase):
    def test_global_optimum_beats_greedy(self):
        # A-B wins greedily, but A-C + B-D is globally superior.
        rows = [candidate("a", "b"), candidate("a", "c"), candidate("b", "d")]
        tags = {
            "a": ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
            "b": ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
            "c": ["alpha", "beta", "gamma", "delta", "epsilon"],
            "d": ["alpha", "beta", "gamma", "delta", "epsilon"],
        }
        # A-B=1, A-C=B-D=5/6; margin > 0.08, optimum is two edges.
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue", return_value=queue(rows)), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue", return_value=queue(rows)
        ):
            result = globally_tagged_proposals([], tags, consent=True)
        pairs = {(p["event_id"], p["toward_event_id"]) for p in result["proposals"]}
        self.assertEqual(pairs, {("a", "c"), ("b", "d")})
        self.assertFalse(result["automatic_merge"])

    def test_unsafe_and_competition_ties_abstain(self):
        rows = [
            candidate("a", "b"),
            candidate("a", "c", risk=["competing_candidate_components"], status="conflicted"),
            candidate("d", "e", risk=["missing_or_cross_scope"], status="conflicted"),
        ]
        tags = {key: ["alpha", "beta"] for key in "abcde"}
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue", return_value=queue(rows)), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue", return_value=queue(rows)
        ):
            result = globally_tagged_proposals([], tags, consent=True)
        self.assertEqual(result["proposals"], [])
        self.assertGreater(result["abstained_ambiguous_endpoints"], 0)
        self.assertEqual(result["tag_scored_pairs"], 2)

    def test_consent_and_input_bounds(self):
        with self.assertRaises(PermissionError):
            globally_tagged_proposals([], {}, consent=False)
        with self.assertRaises(ValueError):
            globally_tagged_proposals([], {"a": ["contains spaces", "alpha"]}, consent=True)
        with self.assertRaises(ValueError):
            globally_tagged_proposals([], {"a": ["alpha"]}, consent=True, minimum_shared=1)

    def test_truncated_queue_fails_closed(self):
        with patch("server.reconstruction_tag_global_assignment.prioritized_review_queue",
                   return_value={**queue([]), "truncated": True}):
            with self.assertRaisesRegex(ValueError, "truncated"):
                globally_tagged_proposals([], {}, consent=True)


if __name__ == "__main__":
    unittest.main()
