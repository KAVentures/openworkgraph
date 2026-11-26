"""Finite-population quality bounds for reviewer-entered marginal counts.

Unchecked cases have unknown outcomes. No random-sampling, independence or
linkage assumption is made. All thresholds are evaluated exactly as fractions.
"""
from fractions import Fraction


def _fraction(n,d):
    return Fraction(n,d) if d else None


def _display(x):
    return None if x is None else round(float(x),10)


def _cohort(row):
    n, checked, failed = row["cases"],row["checked"],row["failed"]
    unchecked = n-checked
    passed = checked-failed
    nonhandoff = row["attempts"]-row["handoffs"]
    # Known passed cases could be outside the nonhandoff subset; all
    # unchecked cases could pass or fail. These marginal bounds are sharp.
    joint_lower = max(0, nonhandoff+passed-n)
    joint_upper = min(nonhandoff,n-failed)
    return {"cases":n,"checked_cases":checked,"known_failed_cases":failed,
            "known_passed_cases":passed,"unchecked_cases":unchecked,
            "failure_count_lower":failed,"failure_count_upper":failed+unchecked,
            "failure_rate_lower":_display(_fraction(failed,n)),
            "failure_rate_upper":_display(_fraction(failed+unchecked,n)),
            "nonhandoff_attempted_cases":nonhandoff,
            "nonhandoff_and_quality_pass_count_lower":joint_lower,
            "nonhandoff_and_quality_pass_count_upper":joint_upper,
            "nonhandoff_and_quality_pass_share_of_eligible_lower":
                _display(_fraction(joint_lower,row["eligible"])),
            "nonhandoff_and_quality_pass_share_of_eligible_upper":
                _display(_fraction(joint_upper,row["eligible"]))}


def quality_population_review(baseline, after, threshold):
    """Bound the after-minus-baseline failure-rate difference exactly.

    A threshold can be established, disproved or unresolved even if every
    checked case meets a reviewer criterion. No unchecked label is imputed.
    """
    b,a = _cohort(baseline),_cohort(after)
    bn,an = b["cases"],a["cases"]
    low = Fraction(a["failure_count_lower"],an)-Fraction(b["failure_count_upper"],bn)
    high = Fraction(a["failure_count_upper"],an)-Fraction(b["failure_count_lower"],bn)
    target = Fraction(str(threshold))
    status = ("established_within_threshold" if high<=target else
              "established_above_threshold" if low>target else "unresolved")
    # Evidence resolution guide: minimum number of favorable new checks
    # needed to establish the threshold, if outcomes are favorable. Checking
    # an after case that passes reduces the upper bound by 1/an; checking
    # a baseline case that fails reduces it by 1/bn. Prefer larger steps.
    # Not a sampling recommendation, and never choose which cases to check.
    # The adverse direction is analogous and uses strict > at the threshold.
    def required(gap, buckets, strict):
        if gap < 0 or gap == 0 and not strict:
            return 0
        count = 0
        for step, capacity in sorted(buckets, reverse=True):
            needed = gap // step + 1 if strict else -(-gap // step)
            used = min(capacity, max(0, needed))
            count += used
            gap -= used*step
            if gap < 0 or gap == 0 and not strict:
                return count
        return None
    capacities=[(Fraction(1,an),a["unchecked_cases"]),
                (Fraction(1,bn),b["unchecked_cases"])]
    return {"format":"openworkgraph.quality-population-bounds.v1",
            "baseline":b,"after":a,"failure_rate_change_lower":_display(low),
            "failure_rate_change_upper":_display(high),
            "maximum_declared_increase":_display(target),"threshold_status":status,
            "minimum_favorable_new_checks_to_establish_within":required(high-target,capacities,False),
            "minimum_adverse_new_checks_to_establish_above":required(target-low,capacities,True),
            "check_counts_are_best_case_arithmetic_not_sampling_plan":True,
            "all_cases_quality_observed":not (b["unchecked_cases"] or a["unchecked_cases"]),
            "bounds_are_confidence_intervals":False,"case_level_linkage_available":False,
            "autonomous_success_verified":False,
            "interpretation":"Bounds allow every unchecked case to pass or fail and every overlap of the entered quality and non-handoff marginals. The check-count diagnostics assume favorable or adverse newly observed outcomes, respectively; they are not sampling plans or guaranteed resolution. Non-handoff plus quality pass does not prove autonomous completion."}
