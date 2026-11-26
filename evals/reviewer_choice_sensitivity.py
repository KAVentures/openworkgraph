"""Private reviewer-choice sensitivity for the existing three-arm pilot.

All values come from validated, complete reviews. A choice is a complete
review row (not an invented combination of its fields). Each entry may take
one of the original or adjudicated scores. Marginal extrema are sharp in
this finite sensitivity model; joint gate extrema are bounded and, for
small choice spaces, exhaustively enumerated. No inference about true labels,
reviewer accuracy, causal effects, or future work is made.
"""
from collections import Counter
from fractions import Fraction
from itertools import product
from math import prod

from .continuity.scoring import decision, score_episode
from .product_value import ARMS

FORMAT = "owg.reviewer-choice-sensitivity.v1"
MAX_JOINT_ASSIGNMENTS = 4096
METRICS = (
    ("resource_recall", "resource_recall", True),
    ("continuation_success_rate", "continuation_success", True),
    ("clarification_questions_per_episode", "clarification_questions", False),
    ("wrong_resource_rate", "wrong_resource_rate", False),
    ("wrong_assumptions_per_episode", "wrong_assumptions", False),
    ("consequential_wrong_action_rate", "consequential_wrong_action", False),
)
COMPARISONS = (("goal_vs_no_owg","control","goal"),
               ("raw_vs_no_owg","control","raw"),
               ("goal_vs_raw","raw","goal"))
PASS = "continue_continuity_thesis"
STOP = "stop_or_pause_product_pivot"


def _vector(episode,row):
    score=score_episode(episode,row)
    # Match statistics.mean's exact binary-float sum and final float/round
    # in the canonical scorer. Booleans become exact zero/one.
    return tuple(Fraction(int(score[k])) if isinstance(score[k],bool)
                 else Fraction(score[k]) for _,k,_ in METRICS)


def _zeros():
    return [Fraction(0) for _ in METRICS]


def _add(dst,src):
    for i,v in enumerate(src):
        dst[i]+=v


def _metrics(sums,n):
    return {name:round(float(v/n),6) for (name,_,_),v in zip(METRICS,sums)}


def _arm_bounds(rows,n):
    lo,hi=_zeros(),_zeros()
    for _,choices in rows:
        _add(lo,[min(v[i] for v in choices) for i in range(len(METRICS))])
        _add(hi,[max(v[i] for v in choices) for i in range(len(METRICS))])
    return _metrics(lo,n),_metrics(hi,n)


def _corners(left,right):
    """Build monotone favorable and adverse corners of the six-metric box.

    These coordinates need not be simultaneously attainable. A pass at the
    adverse corner guarantees all row selections pass; a fail at the
    favorable corner guarantees none pass. The converse is not asserted.
    """
    lo_l,hi_l=left
    lo_r,hi_r=right
    adverse_l,adverse_r,favor_l,favor_r={},{},{},{}
    for name,_,higher in METRICS:
        adverse_l[name],adverse_r[name]=(hi_l[name],lo_r[name]) if higher else (lo_l[name],hi_r[name])
        favor_l[name],favor_r[name]=(lo_l[name],hi_r[name]) if higher else (hi_l[name],lo_r[name])
    return decision(adverse_l,adverse_r),decision(favor_l,favor_r)


def _joint(left,right,n):
    fixed={0:_zeros(),1:_zeros()}
    varying=[]
    for arm,rows in enumerate((left,right)):
        for rid,choices in rows:
            if len(choices)==1:_add(fixed[arm],choices[0])
            else:varying.append((arm,rid,choices))
    count=prod(len(x[2]) for x in varying)
    if count>MAX_JOINT_ASSIGNMENTS:
        return {"score_vector_assignments":str(count),"enumerated":False,
                "assignments_evaluated":0,"decision_counts":None}
    outcomes=Counter()
    for picks in product(*(range(len(x[2])) for x in varying)):
        sums={k:list(v) for k,v in fixed.items()}
        for (arm,_,choices),pick in zip(varying,picks):
            _add(sums[arm],choices[pick])
        result=decision(_metrics(sums[0],n),_metrics(sums[1],n))
        outcomes[result["decision"]]+=1
    return {"score_vector_assignments":str(count),"enumerated":True,
            "assignments_evaluated":count,
            "decision_counts":{PASS:outcomes[PASS],STOP:outcomes[STOP]}}


def review_sensitivity(episodes,mapping,first,second,final):
    """Called only after frozen basis and independent sheets are validated.

    The returned report is private, after unblinding. It contains aggregate
    statistics, no reviewer IDs, entry IDs, source facts or model output.
    """
    truth={str(row["episode_id"]).strip():row for row in episodes}
    n=len(truth)
    if not n or set(mapping)!=set(first) or set(mapping)!=set(second) or set(mapping)!=set(final):
        raise ValueError("sensitivity requires every frozen review entry")
    rows={arm:[] for arm in ARMS}
    counts={arm:Counter() for arm in ARMS}
    observed={arm:_zeros() for arm in ARMS}
    seen=set()
    for rid,meta in mapping.items():
        arm,eid=meta["arm"],meta["episode_id"]
        if arm not in rows or eid not in truth or (arm,eid) in seen:
            raise ValueError("invalid sensitivity arm/episode coverage")
        seen.add((arm,eid))
        vectors=tuple(dict.fromkeys(_vector(truth[eid],r)
                      for r in (first[rid],second[rid],final[rid])))
        rows[arm].append((rid,vectors))
        _add(observed[arm],_vector(truth[eid],final[rid]))
        counts[arm][len(vectors)]+=1
    if seen!={(a,e) for a in ARMS for e in truth}:
        raise ValueError("incomplete sensitivity arm/episode coverage")
    bounds={arm:_arm_bounds(rows[arm],n) for arm in ARMS}
    final_metrics={arm:_metrics(observed[arm],n) for arm in ARMS}
    arm_reports={}
    for arm in ARMS:
        low,high=bounds[arm]
        arm_reports[arm]={"entries":n,"entries_by_distinct_decision_score_vectors":{
            str(k):counts[arm][k] for k in (1,2,3)},
            "metric_bounds":{k:{"lower":low[k],"upper":high[k]} for k,_,_ in METRICS},
            "adjudicated_metrics":final_metrics[arm]}
    comparisons={}
    for label,left,right in COMPARISONS:
        adverse,favorable=_corners(bounds[left],bounds[right])
        joint=_joint(rows[left],rows[right],n)
        if joint["enumerated"]:
            c=joint["decision_counts"]
            classification=("all_observed_choices_pass" if not c[STOP]
                            else "all_observed_choices_stop" if not c[PASS]
                            else "decision_depends_on_review_choice")
        elif adverse["decision"]==PASS:
            classification="all_observed_choices_pass"
        elif favorable["decision"]==STOP:
            classification="all_observed_choices_stop"
        else:
            classification="unresolved_joint_choice_space"
        lo_l,hi_l=bounds[left]
        lo_r,hi_r=bounds[right]
        comparisons[label]={"classification":classification,
            "adjudicated_point_gate":decision(final_metrics[left],final_metrics[right])["decision"],
            "candidate_minus_baseline_metric_bounds":{
                k:{"lower":round(lo_r[k]-hi_l[k],6),"upper":round(hi_r[k]-lo_l[k],6)}
                for k,_,_ in METRICS},
            "adverse_marginal_corner_gate":adverse["decision"],
            "favorable_marginal_corner_gate":favorable["decision"],
            "marginal_corners_may_not_be_jointly_attainable":True,
            "joint_choice_review":joint}
    return {"format":FORMAT,"episode_count":n,"arms":arm_reports,
            "comparisons":comparisons,"maximum_joint_assignments_per_comparison":MAX_JOINT_ASSIGNMENTS,
            "choices_are_complete_review_score_vectors":True,
            "choice_counts_are_probabilities":False,
            "canonical_product_decision_changed":False,"true_labels_identified":False,
            "independent_outcome_verification":False,
            "interpretation":[
                "Each entry can use any distinct complete decision-score vector observed from the two independent sheets or the final adjudicated judgment. This is a finite sensitivity model, not a probability model or a claim that any reviewer is correct.",
                "Arm metric extrema are individually attainable; jointly favorable or adverse corners may not be. Exact enumeration, when bounded, preserves within-entry metric dependencies.",
                "A comparison that cannot be enumerated and is not certified by its adverse or favorable corner remains unresolved. No sampling or fabricated review choices are substituted.",
                "The canonical point gate and real-episode readiness requirement are unchanged. A robust gate on synthetic or unverified evidence does not demonstrate product effectiveness."]}
