"""Exact conditional adoption economics and declared-outcome risk bounds.

The operating-cost evaluator supplies the full paired population. These
helpers never select an agent or treat economic scenarios as realized benefit.
"""
from fractions import Fraction


def interval(value):
    return {"lower":value[0],"upper":value[1]}


def _outcome(value):
    if value=="success":
        return (1,1)
    if value=="failure":
        return (0,0)
    return (0,1)


def paired_outcomes(left,right,minimum_success_bps,maximum_regression_bps):
    n=len(left)
    if n==0 or n!=len(right):
        raise ValueError("equal nonempty paired population required")
    success=[0,0];change=[0,0];regression=[0,0];improvement=[0,0]
    rows=[]
    for a,b in zip(left,right):
        al,au=_outcome(a);bl,bu=_outcome(b)
        s=(bl,bu);c=(bl-au,bu-al)
        r=(int(al==1 and bu==0),int(au==1 and bl==0))
        i=(int(au==0 and bl==1),int(al==0 and bu==1))
        for dest,part in ((success,s),(change,c),(regression,r),(improvement,i)):
            dest[0]+=part[0];dest[1]+=part[1]
        rows.append({"success_change":interval(c),"regression":interval(r),"improvement":interval(i)})
    def minimum(v,limit):
        return "pass" if v[0]*10000>=limit*n else "fail" if v[1]*10000<limit*n else "unknown"
    def maximum(v,limit):
        return "pass" if v[1]*10000<=limit*n else "fail" if v[0]*10000>limit*n else "unknown"
    checks={"declared_success":minimum(success,minimum_success_bps),
            "declared_paired_regressions":maximum(regression,maximum_regression_bps)}
    return {"paired_attempts":n,"candidate_success_count":interval(success),
            "success_count_change":interval(change),"regression_count":interval(regression),
            "improvement_count":interval(improvement),"checks":checks,
            "status":"fail" if "fail" in checks.values() else "unknown" if "unknown" in checks.values() else "pass",
            "rows":rows}


def released_time(left,right):
    """Manual minus candidate person-ms; a null end is unbounded."""
    if len(left)!=len(right) or not left:
        raise ValueError("equal nonempty paired population required")
    a=sum(x["time_known"] for x in left)
    b=sum(x["time_known"] for x in right)
    return (a-b if all(x["time_complete"] for x in right) else None,
            a-b if all(x["time_complete"] for x in left) else None)


def positive_volume_range(slope,fixed,maximum):
    """Exact inclusive positive-saving integer interval for v=1..maximum.

    Return None if the bound is unknown, or an explicit empty interval if
    known and never strictly positive. Works for either sign of slope.
    """
    if slope is None:
        return None
    slope=Fraction(slope);fixed=Fraction(fixed)
    lo,hi=1,maximum
    if slope>0:
        lo=max(lo,(-fixed)//slope+1)
    elif slope<0:
        # v < fixed/(-slope); ceil(x)-1 without floating point.
        q=fixed/(-slope)
        hi=min(hi,-((-q.numerator)//q.denominator)-1)
    elif fixed<=0:
        return {"empty":True,"minimum_cases":None,"maximum_cases":None}
    return ({"empty":True,"minimum_cases":None,"maximum_cases":None} if lo>hi else
            {"empty":False,"minimum_cases":int(lo),"maximum_cases":int(hi)})


def scale_bounds(bounds,n,volume,fixed=0):
    if volume==0:
        return (Fraction(0),Fraction(0))
    return tuple(Fraction(v)*volume/n+fixed if v is not None else None for v in bounds)


def adoption(delta,time_delta,n,annual_volume,volumes,avoidable_fixed,annual_cost,one_time,money):
    """Partial adoption assumes explicit avoidable fixed savings only at full
    adoption. Existing manual fixed cost remains during mixed operations.
    Zero adoption incurs zero new fixed cost. Positive adoption incurs the
    full alternative annual and one-time cost.
    """
    if n<=0 or annual_volume<=0:
        raise ValueError("positive population and annual volume required")
    def bounds(v):
        return {"lower_microunits":money(v[0]),"upper_microunits":money(v[1])}
    rows=[]
    for volume in volumes:
        fixed=(avoidable_fixed if volume==annual_volume else 0)-annual_cost
        recurring=scale_bounds(delta,n,volume,fixed)
        first=scale_bounds(delta,n,volume,fixed-one_time)
        capacity=scale_bounds(time_delta,n,volume)
        rows.append({"adopted_cases":volume,"manual_cases_remaining":annual_volume-volume,
                     "annual_recurring_savings":bounds(recurring),"first_year_savings":bounds(first),
                     "annual_released_person_ms":{"lower":money(capacity[0]),"upper":money(capacity[1])}})
    slopes=[Fraction(x)/n if x is not None else None for x in delta]
    # The full-adoption point has a discontinuity in manual fixed costs.
    # Solve the partial range analytically and report the full point separately.
    def frontier(fixed):
        partial=[positive_volume_range(s,fixed,annual_volume-1) if annual_volume>1 else
                 {"empty":True,"minimum_cases":None,"maximum_cases":None} for s in slopes]
        full=scale_bounds(delta,n,annual_volume,fixed+avoidable_fixed)
        return {"partial_guaranteed_positive":partial[0],"partial_possible_positive":partial[1],
                "full_guaranteed_positive":None if full[0] is None else full[0]>0,
                "full_possible_positive":None if full[1] is None else full[1]>0}
    return {"rows":rows,"annual_volume":annual_volume,
            "avoidable_manual_fixed_at_full_adoption_microunits":str(avoidable_fixed),
            "first_year_positive_volume":frontier(-annual_cost-one_time),
            "recurring_positive_volume":frontier(-annual_cost)}
