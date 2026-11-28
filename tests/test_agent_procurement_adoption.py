from fractions import Fraction
from itertools import product

from shared.agent_procurement_adoption import positive_volume_range, paired_outcomes, scale_bounds


def test_exact_volume_frontier_exhaustive_small_signed_parameters():
    for numerator,denominator,fixed,maximum in product(range(-5,6),range(1,5),range(-8,9),range(1,12)):
        slope=Fraction(numerator,denominator)
        exact=[v for v in range(1,maximum+1) if slope*v+fixed>0]
        result=positive_volume_range(slope,fixed,maximum)
        if exact:
            assert result=={"empty":False,"minimum_cases":exact[0],"maximum_cases":exact[-1]}
        else:
            assert result=={"empty":True,"minimum_cases":None,"maximum_cases":None}
    assert positive_volume_range(None,-10,100) is None


def test_outcome_bounds_exhaustive_against_binary_completions():
    values=("success","failure",None,"unresolved")
    def possible(x):
        return (1,) if x=="success" else (0,) if x=="failure" else (0,1)
    for a,b,c,d in product(values,repeat=4):
        l=[a,b];r=[c,d]
        candidates=[]
        for x,y,z,w in product(*(possible(x) for x in (a,b,c,d))):
            candidates.append((z+w,z+w-x-y,int(x==1 and z==0)+int(y==1 and w==0),
                               int(x==0 and z==1)+int(y==0 and w==1)))
        q=paired_outcomes(l,r,9000,0)
        for i,k in enumerate(("candidate_success_count","success_count_change","regression_count","improvement_count")):
            assert q[k]=={"lower":min(x[i] for x in candidates),"upper":max(x[i] for x in candidates)}


def test_zero_adoption_not_charged_and_unknown_bounds_preserved():
    assert scale_bounds((None,None),3,0,-100)==(0,0)
    assert scale_bounds((None,Fraction(1,3)),3,9,-2)==(None,-1)
