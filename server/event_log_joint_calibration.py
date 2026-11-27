"""Joint outcome-label sensitivity for two fixed-bin forecasters.

Group unresolved cases by their pair of bin memberships. Any integer
positive count in each group is attainable independently. Enumerate
that compressed space when bounded; otherwise give conservative outer
bounds from exact marginal ECE extrema.
"""
from collections import Counter
from fractions import Fraction
from itertools import product

MAX_ASSIGNMENTS=8192


def compare(rows,bins,minimum,marginal):
    from .event_log_prediction_calibration_math import bin_index,exact,_known_label
    n=len(rows)
    if n<minimum:
        return {"estimated":False,"reason":"insufficient_paired_cases","paired_cases":n}
    sums=[[Fraction(0) for _ in range(bins)] for _ in range(2)]
    known=[[0]*bins for _ in range(2)]
    unknown=Counter()
    for r in rows:
        f=r["frozen"]
        probs=[f["outcome_probability"],f["base_rate"]]
        a,b=[bin_index(p,bins) for p in probs]
        y=_known_label(r)
        for arm,k in enumerate((a,b)):
            sums[arm][k]+=exact(probs[arm])
            if y is not None:known[arm][k]+=y
        if y is None:unknown[a,b]+=1
    groups=sorted(unknown.items())
    size=1
    for _,count in groups:
        size*=count+1
        if size>MAX_ASSIGNMENTS:break
    if size>MAX_ASSIGNMENTS:
        # Compute outward conservative rounded bounds from exact marginal
        # numerators rather than subtracting rounded floating-point values.
        from .event_log_prediction_calibration_math import _calibration_gap
        limits=[]
        for arm in (0,1):
            capacity=[0]*bins
            for pair,count in groups:capacity[pair[arm]]+=count
            low=high=Fraction(0)
            for k in range(bins):
                a,b=_calibration_gap(0,sums[arm][k],known[arm][k],known[arm][k]+capacity[k])
                low+=a;high+=b
            limits.append((low,high))
        lo=(limits[0][0]-limits[1][1])/n
        hi=(limits[0][1]-limits[1][0])/n
        exact_joint=False
        enumerated=0
    else:
        # Precompute per-bin gap at every attainable local count. Groups
        # share label counts across arms. All vectors in the product are
        # feasible, including their extrema.
        capacities=[[0]*bins for _ in range(2)]
        for (a,b),count in groups:
            capacities[0][a]+=count;capacities[1][b]+=count
        gap=[[[abs(Fraction(known[arm][k]+j)-sums[arm][k])
                for j in range(capacities[arm][k]+1)] for k in range(bins)] for arm in (0,1)]
        lo=hi=None
        enumerated=0
        for counts in product(*(range(count+1) for _,count in groups)):
            assigned=[[0]*bins for _ in range(2)]
            for ((a,b),_),count in zip(groups,counts):
                assigned[0][a]+=count;assigned[1][b]+=count
            delta=sum((gap[0][k][assigned[0][k]]-gap[1][k][assigned[1][k]] for k in range(bins)),Fraction(0))/n
            lo=delta if lo is None else min(lo,delta)
            hi=delta if hi is None else max(hi,delta)
            enumerated+=1
        exact_joint=True
    direction=("model_strictly_lower_ece" if hi<0 else "baseline_strictly_lower_ece" if lo>0 else
               "always_equal_ece" if lo==hi==0 else "direction_not_resolved")
    return {"estimated":True,"paired_cases":n,"unknown_outcomes":sum(unknown.values()),
            "distinct_unknown_joint_bins":len(groups),"exact_joint_bounds":exact_joint,
            "compressed_assignments_evaluated":enumerated,"assignment_budget":MAX_ASSIGNMENTS,
            "eventual_model_minus_baseline_ece_lower":float(lo),"eventual_model_minus_baseline_ece_upper":float(hi),
            "direction":direction,"method":"exhaustive_joint_bin_positive_counts" if exact_joint else "conservative_marginal_outer_bounds",
            "interpretation":"Negative favors the model on this fixed-bin empirical calibration metric. "
                "Both forecasters share each unknown binary label. When the compressed search exceeds its budget, "
                "outer bounds are conservative and need not be attainable. No future quality or causal claim."}
