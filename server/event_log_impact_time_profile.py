"""Descriptive event-time bins around a declared intervention.

Membership, metric and exclusions are supplied by the existing impact
analysis. No bin-specific cohort tuning, causal attribution or significance
testing is performed.
"""
from collections import defaultdict, Counter
from datetime import timedelta
from hashlib import sha256
import json
from statistics import mean, median

FORMAT="openworkgraph.intervention-time-profile.v1"


def _stamp(t):
    return t.isoformat()+"Z"


def _summary(rows, minimum):
    values=[r[3] for r in rows]
    n=len(values)
    return {"cases":n,"mean":round(mean(values),6) if n>=minimum else None,
            "median":round(median(values),6) if n>=minimum else None,
            "minimum_support_met":n>=minimum}


def _gap(a,b):
    if a is None or b is None:return None
    return round(a-b,6)


def build(log_id,groups,at,start,end,recent,settings,visible_first,visible_last,period_fn,population_count):
    """Build a complete selected-group profile and its exact case inventory."""
    profile=settings.time_profile
    width=timedelta(days=profile.bin_days)
    bins=range(-profile.baseline_bins,profile.follow_up_bins)
    member_rows={g:defaultdict(list) for g in groups}
    excluded={g:Counter() for g in groups}
    spans={g:Counter() for g in groups}
    included={g:0 for g in groups}
    digest=sha256()
    def feed(value):
        digest.update(json.dumps(value,ensure_ascii=True,sort_keys=True,
                                 separators=(",",":"),allow_nan=False).encode()+b"\n")
    feed([FORMAT,log_id,settings.model_dump(mode="json"),population_count,_stamp(visible_first),_stamp(visible_last),
          _stamp(recent) if recent is not None else None])
    for name,rows in sorted(groups.items()):
        for row in rows:
            key,started,ended,value=row
            feed([name,key,_stamp(started),_stamp(ended),value])
            index=(started-at)//width
            if index not in bins:
                excluded[name]["outside_profile"]+=1
                continue
            period,spanning=period_fn(started,ended,at,start,end,recent,settings.exclude_spanning)
            spans[name][index]+=int(spanning)
            if period not in ("baseline","follow_up"):
                excluded[name][period]+=1
                continue
            member_rows[name][index].append(row)
            included[name]+=1
    reports=[]
    reference=profile.reference_bin
    main="treated" if "treated" in groups else "all"
    reference_cells={g:_summary(member_rows[g][reference],profile.minimum_cases) for g in groups}
    ref_main=reference_cells[main]["mean"]
    ref_gap=_gap(ref_main,reference_cells["control"]["mean"]) if "control" in groups else None
    for index in bins:
        left=at+width*index;right=left+width
        cells={}
        for g in groups:
            cell=_summary(member_rows[g][index],profile.minimum_cases)
            cell["baseline_spanning_intervention_cases_before_exclusion"]=spans[g][index]
            cell["change_from_reference_mean"]=_gap(cell["mean"],reference_cells[g]["mean"])
            cells[g]=cell
        gap=_gap(cells[main]["mean"],cells["control"]["mean"]) if "control" in groups else None
        reports.append({"relative_bin":index,"period":"baseline" if index<0 else "follow_up",
                        "from_utc":_stamp(left),"before_utc":_stamp(right),
                        "inside_visible_event_span":left>=visible_first and right<=visible_last,
                        "recent_follow_up_exclusion_overlaps_bin":
                            index>=0 and recent is not None and right>recent,
                        "reference_bin":index==reference,"groups":cells,
                        "treated_minus_control_mean":gap,
                        "control_adjusted_change_from_reference":_gap(gap,ref_gap)})
    accounting={}
    for g,rows in groups.items():
        counts={k:excluded[g][k] for k in ("outside_profile","outside_window",
                                     "recent_possibly_running","spanning_intervention")}
        if included[g]+sum(counts.values())!=len(rows):
            raise ValueError("Intervention profile accounting did not close")
        accounting[g]={"selected_group_cases":len(rows),"included_cases":included[g],"excluded":counts}
    selected_count=sum(a["selected_group_cases"] for a in accounting.values())
    included_count=sum(a["included_cases"] for a in accounting.values())
    outside_count=population_count-selected_count
    if outside_count<0:
        raise ValueError("Intervention profile selected groups overlap or exceed population")
    population={"all_projected_cases":population_count,"selected_group_cases":selected_count,
                "outside_selected_groups":outside_count,"included_profile_cases":included_count,
                "excluded_from_selected_groups":selected_count-included_count}
    return {"format":FORMAT,"profile_sha256":digest.hexdigest(),"population":population,
            "bin_days":profile.bin_days,"reference_bin":reference,
            "minimum_cases_per_cell":profile.minimum_cases,
            "reference_group_cells":reference_cells,"reference_treated_minus_control_mean":ref_gap,
            "bins":reports,"accounting":accounting,
            "case_start_assignment":True,"bootstrap_intervals_computed_for_bins":False,
            "parallel_trends_established":False,"source_completeness_verified":False,
            "causal_effect_verified":False,
            "interpretation":[
                "Equal-width bins are anchored to the declared intervention, start-inclusive and end-exclusive. Bin -1 immediately precedes it; bin 0 begins at it. Cases are assigned by recorded start, with the same metric and spanning/recent exclusions as the main impact report.",
                "Means and medians are withheld below the selected minimum case count. Changes subtract the explicitly chosen baseline reference mean. With controls, the change in the treated-minus-control gap is descriptive, not a verified causal effect.",
                "Cases outside both explicit groups are disclosed separately from cases excluded within the selected groups.",
                "Visible event span flags do not establish complete observation, and recent follow-up exclusions can reduce bin coverage. Cohort definitions may depend on later events; this profile does not establish ex ante treatment assignment.",
                "No per-bin confidence intervals, trend significance tests, parallel-trends proof or verified savings are claimed. The original before/after and placebo estimates are unchanged."]},member_rows
