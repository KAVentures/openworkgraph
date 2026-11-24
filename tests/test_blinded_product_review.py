"""Regression tests for the standalone blind-review operator workflow."""
import copy
import itertools
import unittest

from evals.blinded_product_review import prepare, reconcile, review_template
from evals.product_value import ARMS


def inputs():
    episodes = [{
        "episode_id": "case-a",
        "prompt": "Resume the pricing task",
        "correct_resources": ["opaque-a"],
        "correct_agent_runs": [],
        "distractor_resources": ["opaque-b"],
        "unfinished_state": "draft",
        "real_observed_work": False,
        "ground_truth_verified_by": "truth-reviewer",
    }]
    runs = {
        arm: [{"episode_id": "case-a", "recorded_model_completion": True,
               "model_id": "same-model", "source_state_id": "same-state",
               "review_material_approved": True,
               "review_text": "An approved neutral record.",
               "private_transport": "never included"}] for arm in ARMS
    }
    return episodes, runs


def package():
    episodes, runs = inputs()
    ids = (f"opaque-{i}" for i in itertools.count())
    packet, key = prepare(episodes, runs, seed=12, id_factory=lambda: next(ids))
    reviews = review_template(packet)
    for row in reviews:
        row.update(reviewer_id="independent-reviewer",
                   selected_resources=["opaque-a"], selected_agent_runs=[],
                   clarification_questions=0, wrong_assumptions=0,
                   continuation_success=True, consequential_wrong_action=False)
    return episodes, packet, key, reviews


class BlindedProductReviewTests(unittest.TestCase):
    def test_allowlisted_packet_and_canonical_score(self):
        episodes, packet, key, reviews = package()
        self.assertEqual(len(packet["entries"]), 3)
        for entry in packet["entries"]:
            self.assertEqual(set(entry), {"review_id", "prompt", "review_text"})
        self.assertEqual(set(key["mapping"]), {r["review_id"] for r in reviews})
        result = reconcile(episodes, packet, key, reviews)
        self.assertEqual(result["status"], "pilot_only")
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["model_calls_performed_by_this_script"], 0)

    def test_preparation_rejects_unapproved_incomplete_or_unmatched_runs(self):
        for field, value in (
                ("review_material_approved", False),
                ("recorded_model_completion", False),
                ("model_id", "other-model"),
                ("source_state_id", "other-state"),
                ("review_text", "")):
            with self.subTest(field=field):
                episodes, runs = inputs()
                runs["goal"][0][field] = value
                with self.assertRaises(ValueError):
                    prepare(episodes, runs)
        episodes, runs = inputs()
        runs["raw"][0]["episode_id"] = "other"
        with self.assertRaises(ValueError):
            prepare(episodes, runs)

    def test_changed_packet_or_truth_refused(self):
        episodes, packet, key, reviews = package()
        changed = copy.deepcopy(packet)
        changed["entries"][0]["review_text"] += " edited"
        with self.assertRaisesRegex(ValueError, "frozen packet"):
            reconcile(episodes, changed, key, reviews)
        truth = copy.deepcopy(episodes)
        truth[0]["correct_resources"] = ["other"]
        with self.assertRaisesRegex(ValueError, "frozen truth"):
            reconcile(truth, packet, key, reviews)

    def test_missing_duplicate_and_unknown_reviews_refused(self):
        episodes, packet, key, reviews = package()
        for invalid in (reviews[:-1], reviews + [reviews[0]],
                        [{**reviews[0], "review_id": "unknown"}, *reviews[1:]]):
            with self.assertRaises(ValueError):
                reconcile(episodes, packet, key, invalid)

    def test_blank_template_and_invalid_review_fields_refused(self):
        episodes, packet, key, reviews = package()
        with self.assertRaises(ValueError):
            reconcile(episodes, packet, key, review_template(packet))
        for field, value in (("continuation_success", 1),
                             ("wrong_assumptions", -1),
                             ("selected_resources", ["a", "a"]),
                             ("selected_agent_runs", [12]),
                             ("reviewer_id", "truth-reviewer")):
            with self.subTest(field=field):
                invalid = copy.deepcopy(reviews)
                invalid[0][field] = value
                with self.assertRaises(ValueError):
                    reconcile(episodes, packet, key, invalid)

    def test_duplicate_binding_and_missing_key_entry_refused(self):
        episodes, packet, key, reviews = package()
        invalid = copy.deepcopy(key)
        ids = list(invalid["mapping"])
        invalid["mapping"][ids[1]] = invalid["mapping"][ids[0]]
        with self.assertRaises(ValueError):
            reconcile(episodes, packet, invalid, reviews)
        invalid = copy.deepcopy(key)
        del invalid["mapping"][ids[0]]
        with self.assertRaises(ValueError):
            reconcile(episodes, packet, invalid, reviews)


if __name__ == "__main__":
    unittest.main()
