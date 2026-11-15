"""Risk review queue regressions over production source observations."""
import unittest

from server.reconstruction_review_queue import prioritized_review_queue


def event(eid, minute, *, ref=None, tab="tab-1"):
    metadata = {"tab_context_id": tab}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
            "host": "example.test",
        }
    return {
        "event_id": eid, "observed_at": f"2026-01-01T00:{minute:02d}:00Z",
        "organization_id": "org", "actor_id": "actor",
        "device_id": "device", "session_id": "session",
        "app": "browser", "event_type": "click", "source": "browser",
        "metadata": metadata,
    }


class RiskReviewQueueTest(unittest.TestCase):
    def test_empty_and_limits(self):
        result = prioritized_review_queue([])
        self.assertEqual(result["total_candidates"], 0)
        self.assertFalse(result["authoritative"])
        for limit in (0, 501, True):
            with self.assertRaises(ValueError):
                prioritized_review_queue([], limit=limit)

    def test_multiplexed_tab_conflict_and_determinism(self):
        events = [event("a", 0, ref="AAA"), event("loose", 1),
                  event("b", 2, ref="BBB")]
        first = prioritized_review_queue(events)
        self.assertEqual(first, prioritized_review_queue(events))
        self.assertTrue(first["requires_explicit_review"])
        self.assertEqual(first["ordering"], "conflicts_first_then_risk_count_then_relation_priority")
        self.assertEqual(first["conflicted_candidates"], sum(bool(x["risk_reasons"]) for x in first["queue"]))
        self.assertTrue(first["canonical_membership_unchanged"])
        for item in first["queue"]:
            self.assertFalse(item["automatic_merge"])
            self.assertIn(item["review_status"], ("inspect", "conflicted"))

    def test_conflicts_come_before_inspection_only_candidates(self):
        events = [event("a", 0, ref="AAA", tab="mixed"),
                  event("loose", 1, tab="mixed"),
                  event("b", 2, ref="BBB", tab="mixed"),
                  event("plain1", 3, tab="other"),
                  event("plain2", 4, tab="other")]
        result = prioritized_review_queue(events)
        risk = [bool(item["risk_reasons"]) for item in result["queue"]]
        self.assertEqual(risk, sorted(risk, reverse=True))
        self.assertEqual(result["conflicted_candidates"], sum(risk))
        self.assertFalse(any(item["automatic_merge"] for item in result["queue"]))

    def test_pagination_discloses_truncation(self):
        events = [event(f"e{i}", i, tab="tab-1") for i in range(10)]
        result = prioritized_review_queue(events, limit=1)
        if result["total_candidates"] > 1:
            self.assertTrue(result["truncated"])
            self.assertEqual(result["shown_candidates"], 1)

    def test_cross_scope_never_becomes_approval(self):
        events = [event("a", 0), event("b", 1)]
        events[1]["actor_id"] = "another"
        result = prioritized_review_queue(events)
        self.assertFalse(any(x["automatic_merge"] for x in result["queue"]))


if __name__ == "__main__":
    unittest.main()
