"""Censoring-aware empirical calibration of an immutable frozen population.

Quantile ties are not failures: target tau must lie between P(T<q)
and P(T<=q). Outcome bins retain exact integer possible positive counts.
No model is changed or deployed.
"""
from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
import json
import math

FORMAT="openworkgraph.prospective-calibration.v1"
QUANTILES=((10,"remaining_p10_seconds"),(50,"median_remaining_seconds"),(90,"remaining_p90_seconds"))
STATES=("finished","still_open","missing","inconsistent")
MAX_GROUPS=30


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def finite(x):
    return type(x) in (float,int) and math.isfinite(x) and x>=0


def probability(x):
    return finite(x) and x<=1


def exact(x):
    return Fraction(str(x))


def num(x):
    return float(x) if x is not None else None


def quantile_membership(row,field):
    q=row["frozen"].get(field)
    if not finite(q):return None
    if row["state"]=="finished":
        a=row["remaining_seconds"]
        return ("yes" if a<q else "no","yes" if a<=q else "no")
    if row["state"]=="still_open":
        a=row["elapsed_seconds"]
        return ("no" if a>=q else "unknown","no" if a>q else "unknown")
    # Missing/inconsistent observations supply no time bound beyond
    # nonnegativity of the hypothetical eventual remaining time.
    return ("no" if q==0 else "unknown","unknown")


def quantiles(rows):
    reports=[]
    for pct,field in QUANTILES:
        pairs=[p for r in rows if (p:=quantile_membership(r,field)) is not None]
        n=len(pairs)
        counts=[Counter(p[i] for p in pairs) for i in (0,1)]
        def bound(c):
            return {"known_yes":c["yes"],"known_no":c["no"],"unknown":c["unknown"],
                    "lower":c["yes"]/n if n else None,"upper":(c["yes"]+c["unknown"])/n if n else None}
        strict,inclusive=map(bound,counts)
        tau=Fraction(pct,100)
        direction="no_valid_estimates"
        if n:
            if Fraction(strict["known_yes"],n)>tau:direction="definitely_too_high"
            elif Fraction(inclusive["known_yes"]+inclusive["unknown"],n)<tau:direction="definitely_too_low"
            elif not strict["unknown"] and not inclusive["unknown"]:direction="target_bracketed_by_empirical_quantile"
            else:direction="not_resolved"
        reports.append({"quantile_percent":pct,"valid_estimates":n,"excluded_invalid_estimates":len(rows)-n,
                        "strictly_below":strict,"at_or_below":inclusive,"diagnosis":direction})
    return reports


def bin_index(p,bins):
    return min(bins-1,int(exact(p)*bins))


def _known_label(row):
    y=row["outcome"]
    return int(y) if row["state"]=="finished" and type(y) is bool else None


def _calibration_gap(n,total,lo,hi):
    # Attainable positive count is an integer; use nearest feasible integer
    # to the sum of forecast probabilities, not a continuous relaxation.
    floor=total.numerator//total.denominator
    candidates={lo,hi,max(lo,min(hi,floor)),max(lo,min(hi,floor+1))}
    minimum=min(abs(Fraction(x)-total) for x in candidates)
    maximum=max(abs(Fraction(lo)-total),abs(Fraction(hi)-total))
    return minimum,maximum


def reliability(rows,bins,minimum):
    # Both forecasters evaluated on precisely the same valid probability pairs.
    paired=[r for r in rows if probability(r["frozen"].get("outcome_probability")) and probability(r["frozen"].get("base_rate"))]
    out={}
    for arm,field in (("model","outcome_probability"),("baseline","base_rate")):
        groups=defaultdict(list)
        for r in paired:groups[bin_index(r["frozen"][field],bins)].append(r)
        low_total=high_total=Fraction(0)
        reports=[]
        for i in range(bins):
            group=groups[i];n=len(group)
            labels=[_known_label(r) for r in group]
            pos=sum(y==1 for y in labels);neg=sum(y==0 for y in labels);unknown=n-pos-neg
            total=sum((exact(r["frozen"][field]) for r in group),Fraction(0))
            low,high=_calibration_gap(n,total,pos,pos+unknown)
            low_total+=low;high_total+=high
            supported=n>=minimum
            direction="insufficient_support" if not supported else (
                "definitely_overpredicts" if total>pos+unknown else
                "definitely_underpredicts" if total<pos else
                "resolved_equal" if unknown==0 and total==pos else "not_resolved_or_equal")
            reports.append({"bin_index":i,"lower_probability":i/bins,"upper_probability":(i+1)/bins,
                            "upper_inclusive":i==bins-1,"cases":n,"known_positive":pos,"known_negative":neg,"unknown":unknown,
                            "minimum_support_met":supported,"mean_predicted":num(total/n) if supported else None,
                            "eventual_observed_rate_lower":pos/n if supported else None,
                            "eventual_observed_rate_upper":(pos+unknown)/n if supported else None,
                            "absolute_calibration_gap_lower":num(low/n) if supported else None,
                            "absolute_calibration_gap_upper":num(high/n) if supported else None,
                            "diagnosis":direction})
        n=len(paired)
        out[arm]={"bins":reports,"paired_cases":n,"supported_bin_cases":sum(x["cases"] for x in reports if x["minimum_support_met"]),
                  "eventual_fixed_bin_ece_lower":num(low_total/n) if n>=minimum else None,
                  "eventual_fixed_bin_ece_upper":num(high_total/n) if n>=minimum else None}
    return {"paired_probability_cases":len(paired),"excluded_invalid_probability_pairs":len(rows)-len(paired),
            "bins_per_arm":bins,"minimum_bin_cases":minimum,**out,
            "ece_includes_all_nonempty_bins":True,"paired_ece_difference_bounds_computed":False,
            "interpretation":"Each arm has its own fixed bins on identical paired cases. Integer positive-count bounds give sharp marginal ECE bounds for that arm across all assignments of unknown binary labels. "
                "Model and baseline bounds are not joint difference bounds. Fixed-bin empirical calibration depends on the selected binning and does not establish future calibration."}


def group_key(row,group_by):
    if group_by=="none":return ("all","All frozen cases")
    key="last_activity" if group_by=="activity" else "basis"
    val=row["frozen"].get(key)
    return ("known",val) if type(val) is str and val else ("missing",None)


def summary(rows,bins,minimum,outcome):
    states=Counter(r["state"] for r in rows)
    return {"cases":len(rows),"observation_counts":{s:states[s] for s in STATES},
            "quantiles":quantiles(rows),"outcome_reliability":reliability(rows,bins,minimum) if outcome else None}


def build(rows,settings,outcome):
    grouped=defaultdict(list)
    for row in rows:grouped[group_key(row,settings.group_by)].append(row)
    eligible=[(k,v) for k,v in grouped.items() if len(v)>=settings.minimum_cases]
    eligible.sort(key=lambda item:(-len(item[1]),str(item[0])))
    selected=eligible[:MAX_GROUPS]
    visible={k for k,_ in selected}
    return {"overall":summary(rows,settings.bins,settings.minimum_cases,outcome),
            "groups":[{"group_sha256":digest(key),"label_kind":key[0],"label":key[1],
                       **summary(group,settings.bins,settings.minimum_cases,outcome)} for key,group in selected],
            "groups_withheld_or_limited":len(grouped)-len(selected),
            "cases_in_withheld_or_limited_groups":sum(len(v) for k,v in grouped.items() if k not in visible),
            "max_visible_groups":MAX_GROUPS,"minimum_group_cases":settings.minimum_cases,
            "group_by":settings.group_by,"outcome_configured":outcome}


def evidence_row(row):
    item=row["frozen"]
    return {"case_id":item["case_id"],"last_activity":item.get("last_activity"),"basis":item.get("basis"),
            "observation_state":row["state"],"actual_remaining_seconds":row["remaining_seconds"],
            "elapsed_lower_bound_seconds":row["elapsed_seconds"],"observed_outcome":_known_label(row),
            "frozen_quantiles":{str(p):item.get(f) for p,f in QUANTILES},
            "quantile_membership":{str(p):quantile_membership(row,f) for p,f in QUANTILES},
            "model_probability":item.get("outcome_probability"),"baseline_probability":item.get("base_rate")}
