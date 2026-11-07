"""Adversarial review queue tests; synthetic fixtures are not assignment-F1 evidence."""
import unittest

from server.reconstruction_review_cli import build_review_queue


def pair(left, right, events, earlier=None, **extra):
    return {"resource_pointers": [
        {"provider": "github", "resource_kind": "issue", "resource_ref": left},
        {"provider": "local_file", "resource_kind": "file", "resource_ref": right}],
        "current_evidence_event_ids": events,
        "earlier_corroborations": earlier or [],
        "assignment_status": "observed_same_event_pair", "proves_same_case": False, **extra}


def payload(pairs):
    return {"derived": True, "authoritative": False, "saved_history_access": "all_saved",
            "observed_pair_event_assignments": pairs, "coverage": {"scan_truncated": False}}


class ReviewQueueTests(unittest.TestCase):
    def test_denied_revoked_and_unknown_access_never_generate_review_rows(self):
        for mode in ("off", "none", "denied", "revoked", "future_mode", None):
            data = payload([pair("A", "F", ["e1"])])
            data["saved_history_access"] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                build_review_queue(data)

    def test_explicit_current_run_scope_is_review_only(self):
        data = payload([pair("A", "F", ["e1"])])
        data["saved_history_access"] = "current_run"
        out = build_review_queue(data)
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertFalse(out["event_reviews"][0]["confirmed_case"])

    def test_interleaved_unrelated_events_are_never_attributed(self):
        out = build_review_queue(payload([pair("A", "F", ["a1"], [
            {"session_started_at": "2026-11-01T09:00:00Z", "evidence_event_ids": ["a0"]}])]))
        self.assertEqual([x["event_id"] for x in out["event_reviews"]], ["a0", "a1"])
        self.assertTrue(out["unlisted_events_are_unassigned"])
        self.assertTrue(all(not x["confirmed_case"] for x in out["event_reviews"]))

    def test_multi_pair_event_is_ambiguous_not_forced_to_first(self):
        out = build_review_queue(payload([pair("A", "F", ["shared"]), pair("B", "G", ["shared"])]))
        self.assertEqual(out["event_reviews_count"], 1)
        self.assertEqual(out["event_reviews"][0]["review_status"], "ambiguous_multiple_pairs")
        self.assertEqual(len(out["event_reviews"][0]["candidate_resource_pairs"]), 2)

    def test_same_id_in_different_windows_is_not_a_collision(self):
        out = build_review_queue(payload([pair("A", "F", ["id"], [
            {"session_started_at": "2026-11-01", "evidence_event_ids": ["id"]}])]))
        self.assertEqual(out["event_reviews_count"], 2)
        self.assertTrue(all(x["review_status"] == "observed_pair_needs_review" for x in out["event_reviews"]))

    def test_truncation_propagates(self):
        data = payload([pair("A", "F", ["e"], current_evidence_truncated=True)])
        self.assertTrue(build_review_queue(data)["coverage_incomplete"])

    def test_malformed_coverage_refused_instead_of_crashing(self):
        data = payload([pair("A", "F", ["e"])])
        data["coverage"] = ["invalid"]
        with self.assertRaisesRegex(ValueError, "coverage"):
            build_review_queue(data)

    def test_unbounded_session_key_cannot_enter_review_output(self):
        data = payload([pair("A", "F", ["e"], [
            {"session_started_at": "2" * 10000, "evidence_event_ids": ["old"]}])])
        with self.assertRaisesRegex(ValueError, "timestamp"):
            build_review_queue(data)

    def test_rejects_unsafe_or_unbounded_data(self):
        for change in (
            {"saved_history_access": "none"},
            {"authoritative": True},
            {"observed_pair_event_assignments": [pair("A", "F", ["e"] * 9)]},
            {"observed_pair_event_assignments": [{**pair("A", "F", ["e"]), "proves_same_case": True}]},
        ):
            data = payload([pair("A", "F", ["e"])])
            data.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_review_queue(data)


if __name__ == "__main__":
    unittest.main()
