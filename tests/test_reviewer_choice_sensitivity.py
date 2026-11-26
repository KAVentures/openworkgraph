"""Reviewer-choice sensitivity: exact enumeration and conservative large-space bounds."""
import itertools
import unittest
from unittest.mock import patch

from evals import reviewer_choice_sensitivity as sensitivity
from evals.continuity.scoring import aggregate, decision
from evals.product_value import ARMS


def make(n=2):
    episodes=[{"episode_id":f"ep{i}","correct_resources":["a","b"]} for i in range(n)]
    mapping={}
    first,second,final={},{},{}
    for ep in episodes:
        eid=ep["episode_id"]
        for arm in ARMS:
            rid=arm+"-"+eid
            mapping[rid]={"episode_id":eid,"arm":arm}
            a={"selected_resources":["a"],"selected_agent_runs":[],
               "clarification_questions":1,"wrong_assumptions":0,
               "continuation_success":False,"consequential_wrong_action":False}
            b=dict(a)
            c=dict(a)
            if arm=="goal":
                b.update(selected_resources=["a","b"],continuation_success=True)
                c.update(selected_resources=["a","b"],continuation_success=True,wrong_assumptions=1)
            first[rid],second[rid],final[rid]=a,b,c
    return episodes,mapping,first,second,final


class SensitivityTests(unittest.TestCase):
    def test_exact_joint_counts_match_canonical_scorer_oracle(self):
        episodes,mapping,a,b,c=make()
        result=sensitivity.review_sensitivity(episodes,mapping,a,b,c)
        entry_ids=[k for k,v in mapping.items() if v["arm"]=="goal"]
        control=[{"episode_id":v["episode_id"],**a[k]} for k,v in mapping.items() if v["arm"]=="control"]
        base=aggregate(episodes,control)
        counts={sensitivity.PASS:0,sensitivity.STOP:0}
        deltas=[]
        for choices in itertools.product((a,b,c),repeat=len(entry_ids)):
            goal=[{"episode_id":mapping[k]["episode_id"],**sheet[k]} for k,sheet in zip(entry_ids,choices)]
            scored=aggregate(episodes,goal)
            counts[decision(base,scored)["decision"]]+=1
            deltas.append(scored["resource_recall"]-base["resource_recall"])
        r=result["comparisons"]["goal_vs_no_owg"]
        self.assertEqual(r["joint_choice_review"]["decision_counts"],counts)
        self.assertEqual(r["joint_choice_review"]["assignments_evaluated"],9)
        self.assertEqual(r["classification"],"decision_depends_on_review_choice")
        self.assertEqual(r["candidate_minus_baseline_metric_bounds"]["resource_recall"],
                         {"lower":min(deltas),"upper":max(deltas)})
        self.assertEqual(r["adjudicated_point_gate"],sensitivity.STOP)
        self.assertFalse(result["canonical_product_decision_changed"])

    def test_identical_score_vectors_collapse_but_no_false_probability(self):
        episodes,mapping,a,b,c=make(1)
        for rid in mapping:
            b[rid]=dict(a[rid],selected_agent_runs=["different but not scored"])
            c[rid]=dict(a[rid],reviewer_id="third")
        r=sensitivity.review_sensitivity(episodes,mapping,a,b,c)
        self.assertEqual(r["arms"]["goal"]["entries_by_distinct_decision_score_vectors"],{"1":1,"2":0,"3":0})
        self.assertEqual(r["comparisons"]["goal_vs_no_owg"]["classification"],"all_observed_choices_stop")
        self.assertFalse(r["choice_counts_are_probabilities"])

    def test_large_space_not_sampled_and_uncertain_not_certified(self):
        with patch.object(sensitivity,"MAX_JOINT_ASSIGNMENTS",2):
            r=sensitivity.review_sensitivity(*make(2))["comparisons"]["goal_vs_no_owg"]
        self.assertEqual(r["joint_choice_review"]["score_vector_assignments"],"9")
        self.assertFalse(r["joint_choice_review"]["enumerated"])
        self.assertIsNone(r["joint_choice_review"]["decision_counts"])
        self.assertEqual(r["classification"],"unresolved_joint_choice_space")

    def test_large_space_adverse_corner_can_certify_pass(self):
        episodes,mapping,a,b,c=make(2)
        for rid,v in mapping.items():
            if v["arm"]=="goal":
                for sheet in (a,b,c):
                    sheet[rid].update(selected_resources=["a","b"],continuation_success=True,wrong_assumptions=0)
                b[rid]["clarification_questions"]=0
        with patch.object(sensitivity,"MAX_JOINT_ASSIGNMENTS",1):
            r=sensitivity.review_sensitivity(episodes,mapping,a,b,c)["comparisons"]["goal_vs_no_owg"]
        self.assertFalse(r["joint_choice_review"]["enumerated"])
        self.assertEqual(r["classification"],"all_observed_choices_pass")

    def test_joint_fields_not_independently_mixed(self):
        episodes,mapping,a,b,c=make(1)
        for rid,v in mapping.items():
            if v["arm"]=="goal":
                # One complete row has recall gain but worsens assumptions.
                # The other has no gain and no harm. No real choice passes.
                b[rid].update(selected_resources=["a","b"],continuation_success=True,wrong_assumptions=1)
                c[rid]=dict(a[rid])
        r=sensitivity.review_sensitivity(episodes,mapping,a,b,c)["comparisons"]["goal_vs_no_owg"]
        self.assertEqual(r["favorable_marginal_corner_gate"],sensitivity.PASS)
        self.assertEqual(r["classification"],"all_observed_choices_stop")
        self.assertEqual(r["joint_choice_review"]["decision_counts"],{sensitivity.PASS:0,sensitivity.STOP:2})

    def test_incomplete_inputs_fail_closed(self):
        episodes,mapping,a,b,c=make()
        c.pop(next(iter(c)))
        with self.assertRaises(ValueError):
            sensitivity.review_sensitivity(episodes,mapping,a,b,c)


if __name__=="__main__":
    unittest.main()
