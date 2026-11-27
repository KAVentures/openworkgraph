"""Independent finite-population oracles for the pilot decision protocol."""
from fractions import Fraction
from itertools import product
from random import Random

import pytest

from evals import pilot_decision_bounds as bounds


def thresholds(**overrides):
    return {"min_enrolled":2,"min_teams":2,"min_episodes_per_team":1,
            "min_goal_success_bps":5000,"max_goal_wrong_action_bps":5000,
            "min_goal_success_delta_bps":0,"max_goal_wrong_action_delta_bps":0,
            "min_leave_one_team_success_delta_bps":0,"max_leave_one_team_wrong_action_delta_bps":0,
            **overrides}


def exact(r):
    return Fraction(r["numerator"],r["denominator"])


def interval(r):
    return exact(r["lower"]),exact(r["upper"])


def observations(values):
    return [{"episode_id":eid,"arm":arm,"status":"reviewed",
             "continuation_success":v,"consequential_wrong_action":not v}
            for (eid,arm),v in values.items() if v is not None]


def test_simpson_effect_and_leave_one_team_out_are_not_hidden():
    roster={str(i):{"team_id":"large" if i<9 else "small"} for i in range(10)}
    values={(eid,arm):arm=="goal" if eid!="9" else arm!="goal"
            for eid in roster for arm in bounds.ARMS}
    r=bounds.analyze(roster,observations(values),thresholds())
    c=r["contrasts"]["goal_vs_control"]["continuation_success"]
    assert interval(c["episode_weighted"])==(Fraction(4,5),Fraction(4,5))
    assert interval(c["equal_team_weighted"])==(0,0)
    assert interval(c["leave_one_team_out_envelope"])==(-1,1)
    assert not r["all_thresholds_met"]
    assert r["threshold_outcome"]=="fail"
    assert not r["causal_effect_verified"] and not r["rollout_authorized"]


def test_incomplete_coverage_never_passes_even_under_permissive_bounds():
    roster={"a":{"team_id":"t1"},"b":{"team_id":"t2"}}
    values={(eid,arm):None for eid in roster for arm in bounds.ARMS}
    # All other numerical gates pass despite one entirely unknown pair:
    # known positive episode and the other unknown cannot make the delta negative.
    for arm in bounds.ARMS:
        values["a",arm]=arm=="goal"
    r=bounds.analyze(roster,observations(values),thresholds(
        min_goal_success_bps=0,max_goal_wrong_action_bps=10000,
        max_goal_wrong_action_delta_bps=10000,min_leave_one_team_success_delta_bps=-10000,
        max_leave_one_team_wrong_action_delta_bps=10000))
    assert r["threshold_outcome"]=="indeterminate"
    assert not r["all_thresholds_met"]
    assert r["enrolled"]==2 and r["complete_paired_episodes"]==1


def test_exact_threshold_edges_and_strict_integer_policy():
    p=thresholds(min_goal_success_delta_bps=3334)
    for invalid in ({**p,"min_enrolled":True},{**p,"min_teams":1},
                    {**p,"min_enrolled":501},{**p,"extra":0}):
        with pytest.raises(ValueError):bounds.policy(invalid)
    c=bounds.decision_check("x",Fraction(1,3),Fraction(1,3),3334,"min")
    assert c["status"]=="fail"
    assert bounds.decision_check("x",Fraction(1,3),Fraction(1,3),3333,"min")["status"]=="pass"
    assert bounds.decision_check("x",Fraction(1,3),Fraction(2,3),5000,"min")["status"]=="indeterminate"
    assert bounds.decision_check("x",Fraction(1,3),Fraction(2,3),5000,"max")["status"]=="indeterminate"


def test_exhaustive_missing_outcomes_match_all_possible_completions():
    rng=Random(247)
    for _ in range(450):
        n=rng.randrange(2,6)
        roster={str(i):{"team_id":str(i%rng.randrange(2,n+1))} for i in range(n)}
        values={(eid,a):rng.choice((True,False,None)) for eid in roster for a in bounds.ARMS}
        r=bounds.analyze(roster,observations(values),thresholds())
        groups={}
        for eid,row in roster.items():groups.setdefault(row["team_id"],[]).append(eid)
        for left,right in bounds.COMPARISONS:
            unknown=[key for key,v in values.items() if key[1] in (left,right) and v is None]
            episodes=[];team_weighted=[];leave_out=[]
            for choices in product((False,True),repeat=len(unknown)):
                realized={**values,**dict(zip(unknown,choices))}
                changes={eid:int(realized[eid,right])-int(realized[eid,left]) for eid in roster}
                episodes.append(Fraction(sum(changes.values()),n))
                team_weighted.append(sum((Fraction(sum(changes[e] for e in ids),len(ids))
                                            for ids in groups.values()),Fraction())/len(groups))
                for ids in groups.values():
                    kept=[e for e in roster if e not in ids]
                    if kept:leave_out.append(Fraction(sum(changes[e] for e in kept),len(kept)))
            c=r["contrasts"][right+"_vs_"+left]["continuation_success"]
            assert interval(c["episode_weighted"])==(min(episodes),max(episodes))
            assert interval(c["equal_team_weighted"])==(min(team_weighted),max(team_weighted))
            if leave_out:
                assert interval(c["leave_one_team_out_envelope"])==(min(leave_out),max(leave_out))
            else:assert c["leave_one_team_out_envelope"] is None
            wrong=r["contrasts"][right+"_vs_"+left]["consequential_wrong_action"]
            assert interval(wrong["episode_weighted"])==(-max(episodes),-min(episodes))
        assert bounds.analyze(roster,list(reversed(observations(values))),thresholds())==r


def test_single_team_never_produces_leave_one_team_out_estimate():
    roster={"a":{"team_id":"t"}}
    r=bounds.analyze(roster,observations({("a",arm):True for arm in bounds.ARMS}),thresholds())
    assert r["threshold_outcome"]=="fail"
    assert r["contrasts"]["goal_vs_raw"]["continuation_success"]["leave_one_team_out_envelope"] is None


def test_large_full_population_and_no_team_or_episode_identifiers_emitted():
    import json
    roster={f"private_episode_{i}":{"team_id":f"private_team_{i}"} for i in range(500)}
    r=bounds.analyze(roster,observations({(eid,a):a=="goal" for eid in roster for a in bounds.ARMS}),
                     thresholds())
    assert r["enrolled"]==500 and r["teams"]==500 and r["all_thresholds_met"]
    assert "private_" not in json.dumps(r)
