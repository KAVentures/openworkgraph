"""Sensitivity of descriptive standardization to support and reference weights.

All summaries reuse exact cell counts and rational metric sums. Thresholds
are declared, never selected according to an observed improvement.
"""
from fractions import Fraction


def run(stats,cells,totals,thresholds,primary):
    from .event_log_case_mix_math import aggregate,mean,number,delta
    empty=(0,Fraction(0))
    thresholds=sorted(set([primary,*thresholds]))
    outputs=[]
    for threshold in thresholds:
        support=sorted(label for label,v in stats.items() if label[0]=="observed"
                       and all(v.get(c,empty)[0]>=threshold for c in cells))
        counts={c:sum(stats[s][c][0] for s in support) for c in cells}
        shares={c:{s:Fraction(stats[s][c][0],counts[c]) for s in support} for c in cells}
        def average(selected,s):
            return sum((shares[c][s] for c in selected),Fraction(0))/len(selected)
        before=[c for c in cells if c[1]=="baseline"]
        after=[c for c in cells if c[1]=="follow_up"]
        weights={
            "equal_cell":{s:average(cells,s) for s in support},
            "baseline_cell":{s:average(before,s) for s in support},
            "follow_up_cell":{s:average(after,s) for s in support},
            "pooled_case":{s:Fraction(sum(stats[s][c][0] for c in cells),sum(counts.values())) for s in support},
            "equal_stratum":{s:Fraction(1,len(support)) for s in support},
        }
        summaries=[]
        for basis,ref in weights.items():
            if support and sum(ref.values())!=1:raise ValueError("case mix reference weights do not sum to one")
            means={c:sum((ref[s]*mean(stats[s][c]) for s in support),Fraction(0)) if support else None for c in cells}
            groups={}
            for group,_ in before:
                a,b=(group,"baseline"),(group,"follow_up")
                groups[group]={"baseline_mean":number(means[a]),"follow_up_mean":number(means[b]),
                               "standardized_change":number(delta(means[a],means[b]))}
            did=None
            if ("treated","baseline") in means and support:
                did=delta(means["treated","baseline"],means["treated","follow_up"])-delta(means["control","baseline"],means["control","follow_up"])
            summaries.append({"reference_basis":basis,"groups":groups,"standardized_difference_in_differences":number(did)})
        envelope={}
        for g,_ in before:
            values=[row["groups"][g]["standardized_change"] for row in summaries if row["groups"][g]["standardized_change"] is not None]
            envelope[g]={"minimum_change":min(values) if values else None,"maximum_change":max(values) if values else None,
                         "strict_direction_agrees":bool(values and (min(values)>0 or max(values)<0)),
                         "all_zero":bool(values and all(v==0 for v in values))}
        dids=[r["standardized_difference_in_differences"] for r in summaries if r["standardized_difference_in_differences"] is not None]
        outputs.append({"minimum_cases":threshold,"primary_threshold":threshold==primary,
                        "common_support_strata":len(support),"common_support_available":bool(support),
                        "coverage":[{"group":c[0],"period":c[1],"common_support_cases":counts[c],"included_cases":totals[c][0],
                                     "share":number(Fraction(counts[c],totals[c][0])) if totals[c][0] else None} for c in cells],
                        "reference_comparisons":summaries,"group_change_envelopes":envelope,
                        "control_adjusted_envelope":{"minimum_change":min(dids),"maximum_change":max(dids),
                        "strict_direction_agrees":min(dids)>0 or max(dids)<0,"all_zero":all(v==0 for v in dids)} if dids else None})
    return {"thresholds":outputs,"additional_case_scans":0,"threshold_selected_by_outcome":False,
            "confidence_interval":False,"interpretation":"These are deterministic sensitivity comparisons, not uncertainty intervals. "
                "Changing thresholds can change the included population. Reference weights are shared across cells: equal cell shares, baseline cell shares, follow-up cell shares, pooled case shares or equal strata. "
                "A consistent direction across the declared comparisons does not establish a causal effect."}
