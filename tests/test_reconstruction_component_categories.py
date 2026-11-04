"""Corroborated component category matching safety and functional acceptance."""
import unittest
from unittest.mock import patch

from server.reconstruction_component_categories import component_category_proposals


def event(eid, cats):
    return {"event_id": eid, "organization_id": "tenant",
            "metadata": {"semantic_category_ids": cats}}


def fixtures():
    events = [event("a1", ["invoice", "review"]),
              event("a2", ["invoice", "review"]),
              event("b1", ["invoice", "review"]),
              event("b2", ["invoice", "review"]),
              event("c1", ["invoice", "review"]),
              event("c2", ["other", "review"])]
    members = {"a1": ["a1", "a2"], "b1": ["b1", "b2"],
               "c1": ["c1", "c2"]}
    queue = {"version": 1, "evidence_sha256": "a" * 64,
             "queue": [{"event_id": "a2", "toward_event_id": "b1",
                        "review_status": "inspect", "risk_reasons": []},
                       {"event_id": "a2", "toward_event_id": "c1",
                        "review_status": "inspect", "risk_reasons": []}],
             "total_candidates": 2, "truncated": False}
    return events, {"members": members}, queue


class ComponentCategoryTests(unittest.TestCase):
    def run_fixture(self, events, linked, queue):
        with patch("server.reconstruction_component_categories.link_observations",
                   return_value=linked), patch(
            "server.reconstruction_component_categories.prioritized_review_queue",
            return_value=queue
        ), patch(
            "server.reconstruction_semantic_assignment.prioritized_review_queue",
            return_value=queue
        ):
            return component_category_proposals(events, consent=True)

    def test_corrobated_components_propose_and_one_off_category_abstains(self):
        events, linked, queue = fixtures()
        result = self.run_fixture(events, linked, queue)
        self.assertEqual([(x["event_id"], x["toward_event_id"])
                          for x in result["proposals"]], [("a2", "b1")])
        self.assertEqual(result["scored_candidate_pairs"], 1)
        self.assertFalse(result["automatic_merge"])
        self.assertTrue(result["canonical_membership_unchanged"])

    def test_safety_veto_and_duplicate_event_abstain(self):
        events, linked, queue = fixtures()
        queue["queue"][0]["risk_reasons"] = ["missing_or_cross_scope"]
        queue["queue"][0]["review_status"] = "conflicted"
        self.assertEqual(self.run_fixture(events, linked, queue)["proposals"], [])
        events, linked, queue = fixtures()
        events.append(event("a1", ["invoice", "review"]))
        self.assertEqual(self.run_fixture(events, linked, queue)["proposals"], [])

    def test_ambiguous_competitors_abstain(self):
        events, linked, queue = fixtures()
        events[4]["metadata"]["semantic_category_ids"] = ["invoice", "review"]
        events[5]["metadata"]["semantic_category_ids"] = ["invoice", "review"]
        queue["queue"][0]["risk_reasons"] = ["competing_candidate_components"]
        queue["queue"][0]["review_status"] = "conflicted"
        queue["queue"][1]["risk_reasons"] = ["competing_candidate_components"]
        queue["queue"][1]["review_status"] = "conflicted"
        self.assertEqual(self.run_fixture(events, linked, queue)["proposals"], [])

    def test_consent_and_invalid_inputs(self):
        with self.assertRaises(PermissionError):
            component_category_proposals([], consent=False)
        with self.assertRaises(ValueError):
            component_category_proposals([{"event_id": 1}], consent=True)
        with self.assertRaises(ValueError):
            component_category_proposals([event("x", ["private text!"])], consent=True)


if __name__ == "__main__":
    unittest.main()
