"""R25: frozen blinded pilot agreement and adjudication regression tests."""
import copy
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from evals.blinded_product_review import prepare, review_template
from evals.multi_reviewer_product_review import (
    adjudication_template, main, prepare_adjudication, score_adjudicated,
)
from evals.product_value import ARMS


def fixture():
    episodes = [{
        "episode_id": "private-episode", "prompt": "Resume the task",
        "correct_resources": ["a", "b"], "correct_agent_runs": [],
        "distractor_resources": ["c"], "unfinished_state": "draft",
        "real_observed_work": False, "ground_truth_verified_by": "Truth Author",
    }]
    runs = {arm: [{
        "episode_id": "private-episode", "recorded_model_completion": True,
        "model_id": "private-model", "source_state_id": "private-state",
        "review_material_approved": True, "review_text": "Neutral approved evidence.",
    }] for arm in ARMS}
    tokens = (f"opaque-{i}" for i in itertools.count())
    packet, key = prepare(episodes, runs, seed=7, id_factory=lambda: next(tokens))
    first = review_template(packet)
    for i, row in enumerate(first):
        row.update(reviewer_id="First Reviewer", selected_resources=["a", "b"],
                   selected_agent_runs=[], clarification_questions=0, wrong_assumptions=0,
                   continuation_success=i != 2, consequential_wrong_action=False)
    second = copy.deepcopy(first)
    for row in second:
        row.update(reviewer_id="Second Reviewer", selected_resources=["b", "a"])
    second[1]["continuation_success"] = False
    second[1]["wrong_assumptions"] = 2
    second[1]["selected_resources"] = ["a", "c"]
    return episodes, packet, key, first, second


def resolutions(packet):
    rows = adjudication_template(packet)
    for row, entry in zip(rows, packet["entries"]):
        row.update(entry["first_review"], reviewer_id="Third Reviewer",
                   resolution_reason="Selected judgment after reviewing the evidence.")
    return rows


class MultiReviewerProductReviewTests(unittest.TestCase):
    def test_exact_agreement_counts_and_opaque_adjudication(self):
        inputs = fixture()
        packet, basis, agreement = prepare_adjudication(*inputs)
        self.assertEqual(agreement["reviewed_entries"], 3)
        self.assertEqual(agreement["agreement_entries"], 2)
        self.assertEqual(agreement["disagreement_entries"], 1)
        success = agreement["fields"]["continuation_success"]
        self.assertEqual(success["both_true"], 1)
        self.assertEqual(success["both_false"], 1)
        self.assertEqual(success["first_only_true"], 1)
        self.assertEqual(success["second_only_true"], 0)
        self.assertAlmostEqual(success["cohen_kappa"], 0.4)
        self.assertIsNone(agreement["fields"]["consequential_wrong_action"]["cohen_kappa"])
        self.assertAlmostEqual(agreement["fields"]["wrong_assumptions"]["mean_absolute_difference"], 2/3)
        self.assertAlmostEqual(agreement["fields"]["selected_resources"]["mean_jaccard"], 7/9)
        self.assertEqual(agreement["fields"]["selected_agent_runs"]["exact_agreement_rate"], 1)
        [entry] = packet["entries"]
        self.assertEqual(set(entry["differing_fields"]),
                         {"selected_resources", "wrong_assumptions", "continuation_success"})
        self.assertEqual(entry["review_id"], inputs[3][1]["review_id"])
        serialized = json.dumps(packet)
        for forbidden in ("private-episode", "private-model", "private-state", "First Reviewer",
                          "Second Reviewer", "Truth Author"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(agreement["blinding_independently_verified"])
        result = score_adjudicated(*inputs, packet, basis, resolutions(packet))
        self.assertEqual(result["status"], "pilot_only")
        self.assertEqual(result["product_decision"], "insufficient_reviewed_real_episodes")
        self.assertEqual(result["model_calls_performed_by_this_script"], 0)
        multi = result["multi_reviewer_review"]
        self.assertEqual(multi["resolved_disagreement_entries"], 1)
        self.assertTrue(multi["all_entries_resolved"])
        self.assertEqual(multi["agreement_before_adjudication"], agreement)

    def test_every_agreement_requires_no_third_reviewer(self):
        episodes, packet, key, first, second = fixture()
        for row, original in zip(second, first):
            row.update(original, reviewer_id="Second Reviewer",
                       selected_resources=list(reversed(original["selected_resources"])))
        inputs = episodes, packet, key, first, second
        adjudication, basis, agreement = prepare_adjudication(*inputs)
        self.assertEqual(adjudication["entries"], [])
        self.assertEqual(agreement["disagreement_entries"], 0)
        self.assertEqual(adjudication_template(adjudication), [])
        result = score_adjudicated(*inputs, adjudication, basis, [])
        self.assertEqual(result["multi_reviewer_review"]["resolved_disagreement_entries"], 0)

    def test_same_reviewer_alias_truth_author_and_malformed_sheets_refused(self):
        for invalid in ("same", "truth", "mixed", "missing", "duplicate", "extra", "blank", "bad_set"):
            with self.subTest(invalid=invalid):
                episodes, packet, key, first, second = fixture()
                if invalid == "same":
                    for row in second:
                        row["reviewer_id"] = " FIRST REVIEWER "
                elif invalid == "truth":
                    for row in first:
                        row["reviewer_id"] = " truth author "
                elif invalid == "mixed":
                    second[0]["reviewer_id"] = "Other Reviewer"
                elif invalid == "missing":
                    second.pop()
                elif invalid == "duplicate":
                    second[-1] = second[0]
                elif invalid == "extra":
                    second[0]["arm"] = "goal"
                elif invalid == "blank":
                    second[0]["continuation_success"] = None
                else:
                    second[0]["selected_resources"] = ["a", "a"]
                with self.assertRaises(ValueError):
                    prepare_adjudication(episodes, packet, key, first, second)

    def test_changed_frozen_inputs_and_missing_or_nonindependent_resolutions_refused(self):
        inputs = fixture()
        packet, basis, _ = prepare_adjudication(*inputs)
        complete = resolutions(packet)
        for mutation in ("packet", "basis", "review", "key", "missing", "extra", "reason", "identity"):
            with self.subTest(mutation=mutation):
                args = list(copy.deepcopy(inputs))
                public, private, resolved = copy.deepcopy((packet, basis, complete))
                if mutation == "packet":
                    public["entries"][0]["review_text"] += " altered"
                elif mutation == "basis":
                    private["first_review_sha256"] = "f"*64
                elif mutation == "review":
                    args[3][0]["wrong_assumptions"] += 1
                elif mutation == "key":
                    for binding in args[2]["mapping"].values():
                        binding["model_id"] = "changed-model"
                elif mutation == "missing":
                    resolved = []
                elif mutation == "extra":
                    resolved += resolved
                elif mutation == "reason":
                    resolved[0]["resolution_reason"] = " "
                else:
                    resolved[0]["reviewer_id"] = " second reviewer "
                with self.assertRaises(ValueError):
                    score_adjudicated(*args, public, private, resolved)

    def test_cli_separate_outputs_and_exact_score(self):
        episodes, packet, key, first, second = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            truth = root / "truth.jsonl"
            truth.write_text("\n".join(json.dumps(row) for row in episodes), encoding="utf-8")
            for name, data in (("packet", packet), ("key", key), ("first", first), ("second", second)):
                (root / (name+".json")).write_text(json.dumps(data), encoding="utf-8")
            common = ["--episodes", str(truth), "--packet", str(root/"packet.json"),
                      "--key", str(root/"key.json"), "--first-review", str(root/"first.json"),
                      "--second-review", str(root/"second.json")]
            public, private = root/"review", root/"private"
            prepare_args = ["prepare-adjudication", *common, "--review-dir", str(public),
                            "--private-dir", str(private), "--confirm-share-disagreements"]
            self.assertEqual(main(prepare_args), 0)
            self.assertEqual({p.name for p in public.iterdir()}, {"disagreements.json", "resolution-template.json"})
            self.assertEqual({p.name for p in private.iterdir()}, {"basis.json", "agreement.json"})
            public_packet = json.loads((public/"disagreements.json").read_text())
            resolved = root/"resolved.json"
            resolved.write_text(json.dumps(resolutions(public_packet)), encoding="utf-8")
            output = root/"score.json"
            score_args = ["score-adjudicated", *common, "--adjudication-packet", str(public/"disagreements.json"),
                          "--basis", str(private/"basis.json"), "--resolutions", str(resolved), "--out", str(output)]
            self.assertEqual(main(score_args), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "pilot_only")
            with self.assertRaises(FileExistsError):
                main(score_args)
            with self.assertRaises(SystemExit):
                main(prepare_args)


if __name__ == "__main__":
    unittest.main()
