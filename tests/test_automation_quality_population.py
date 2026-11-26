"""Exact enumeration oracles for quality bounds and resolution diagnostics."""
from fractions import Fraction
from itertools import combinations,product

import pytest

from server.automation_quality_population import quality_population_review


def cohort(n,c,f,m=0,eligible=None):
    return {"cases":n,"checked":c,"failed":f,"attempts":m,"handoffs":0,
            "eligible":n if eligible is None else eligible}


def test_every_unchecked_completion_and_joint_overlap_is_bounded():
    b=cohort(3,1,1,2)
    a=cohort(4,2,1,3)
    r=quality_population_review(b,a,.1)
    values=[Fraction(1+y,4)-Fraction(1+x,3) for x in range(3) for y in range(3)]
    assert r["failure_rate_change_lower"]==pytest.approx(float(min(values)))
    assert r["failure_rate_change_upper"]==pytest.approx(float(max(values)))
    # One known checked pass, one known failure, two unknowns; enumerate
    # every quality assignment and every set of three nonhandoff cases.
    overlaps=[]
    for labels in product((False,True),repeat=2):
        quality=(True,False,*labels)
        for nonhandoff in combinations(range(4),3):
            overlaps.append(sum(quality[i] for i in nonhandoff))
    assert r["after"]["nonhandoff_and_quality_pass_count_lower"]==min(overlaps)
    assert r["after"]["nonhandoff_and_quality_pass_count_upper"]==max(overlaps)
    assert not r["autonomous_success_verified"]


@pytest.mark.parametrize("threshold,status",[(.1,"established_within_threshold"),
    (.099999999,"established_above_threshold")])
def test_exact_threshold_boundary_when_all_checked(threshold,status):
    r=quality_population_review(cohort(10,10,1),cohort(10,10,2),threshold)
    assert r["threshold_status"]==status
    assert r["failure_rate_change_lower"]==r["failure_rate_change_upper"]==.1
    assert r["all_cases_quality_observed"]
    assert r["minimum_favorable_new_checks_to_establish_within"]==(0 if status=="established_within_threshold" else None)
    assert r["minimum_adverse_new_checks_to_establish_above"]==(0 if status=="established_above_threshold" else None)


def test_best_case_resolution_matches_exhaustive_integer_oracle():
    for bn,an in product(range(1,5),repeat=2):
        for bc,ac in product(range(bn+1),range(an+1)):
            for bf,af in product(range(bc+1),range(ac+1)):
                b,a=cohort(bn,bc,bf),cohort(an,ac,af)
                for threshold in (0,.25,1):
                    result=quality_population_review(b,a,threshold)
                    target=Fraction(str(threshold))
                    lo=Fraction(af,an)-Fraction(bf+bn-bc,bn)
                    hi=Fraction(af+an-ac,an)-Fraction(bf,bn)
                    favorable,adverse=[],[]
                    for i,j in product(range(bn-bc+1),range(an-ac+1)):
                        shift=Fraction(i,bn)+Fraction(j,an)
                        if hi-shift<=target:favorable.append(i+j)
                        if lo+shift>target:adverse.append(i+j)
                    assert result["minimum_favorable_new_checks_to_establish_within"]==min(favorable,default=None)
                    assert result["minimum_adverse_new_checks_to_establish_above"]==min(adverse,default=None)


def test_coverage_does_not_impute_unknown_quality_and_handoff_linkage():
    r=quality_population_review(cohort(100,0,0,0,0),cohort(100,20,0,50),0)
    assert r["baseline"]["failure_rate_lower"]==0
    assert r["baseline"]["failure_rate_upper"]==1
    assert r["baseline"]["nonhandoff_and_quality_pass_share_of_eligible_lower"] is None
    assert r["after"]["nonhandoff_and_quality_pass_count_lower"]==0
    assert r["after"]["nonhandoff_and_quality_pass_count_upper"]==50
    assert r["threshold_status"]=="unresolved"
    assert not r["bounds_are_confidence_intervals"]
