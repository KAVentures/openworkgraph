"""Pure publication-level quality diagnostics. No case IDs or labels leave this report.

Quality gates describe an operator-selected evidence sample, not provider
completeness, business correctness, or actual timestamp accuracy.
"""
from collections import Counter, defaultdict


def bps(n,d):
    """Conservative rounded-up basis points for maximum-rate gates."""
    return (n*10000+d-1)//d if d else None


def quantile(values,p):
    ordered=sorted(values)
    return ordered[(len(ordered)*p+99)//100-1] if ordered else None


def summarize(events,*,day_precision=False):
    by_case=defaultdict(list)
    activity=Counter()
    for e in events:
        by_case[e["case_id"]].append(e)
        activity[e["activity"]]+=1
    sizes=[len(v) for v in by_case.values()]
    n=len(events);c=len(sizes)
    tied=0;tie_cases=0;tie_buckets=0;repeats=0;single_activity=0
    variants=Counter();unordered=0;spans=[]
    for group in by_case.values():
        counts=Counter(e["observed_at"] for e in group)
        ties=[v for v in counts.values() if v>1]
        tied+=sum(ties);tie_cases+=bool(ties);tie_buckets+=len(ties)
        acts=Counter(e["activity"] for e in group)
        repeats+=sum(v-1 for v in acts.values())
        single_activity+=len(acts)==1
        # A tie must not manufacture a deterministic activity sequence.
        # SAP business dates are not occurrence timestamps even if unique.
        if ties or day_precision:
            unordered+=1
        else:
            ordered=sorted(group,key=lambda e:e["observed_at"])
            variants[tuple(e["activity"] for e in ordered)]+=1
            from datetime import datetime
            spans.append(int((datetime.fromisoformat(ordered[-1]["observed_at"])-
                              datetime.fromisoformat(ordered[0]["observed_at"])).total_seconds()))
    one=sum(v==1 for v in sizes)
    return {
        "events":n,"cases":c,"distinct_activities":len(activity),
        "singleton_cases":one,"singleton_case_rate_bps":bps(one,c),
        "single_activity_cases":single_activity,"single_activity_case_rate_bps":bps(single_activity,c),
        "repeated_activity_occurrences":repeats,"repeated_activity_rate_bps":bps(repeats,n),
        "largest_activity_count":max(activity.values(),default=0),
        "largest_activity_rate_bps":bps(max(activity.values(),default=0),n),
        "events_per_case":{"min":min(sizes,default=None),"p50":quantile(sizes,50),
                           "p90":quantile(sizes,90),"max":max(sizes,default=None)},
        "same_reported_time_events":tied,"same_reported_time_cases":tie_cases,
        "same_reported_time_buckets":tie_buckets,
        "same_reported_time_event_rate_bps":bps(tied,n),
        "timestamp_tie_event_rate_bps":None if day_precision else bps(tied,n),
        "day_precision_present":day_precision,
        "cases_eligible_for_reported_order":c-unordered,
        "cases_excluded_from_reported_order":unordered,
        "distinct_reported_activity_sequences":len(variants) if variants else None,
        "most_common_sequence_cases":max(variants.values()) if variants else None,
        "reported_span_seconds":{"p50":quantile(spans,50),"p90":quantile(spans,90),"max":max(spans)}
            if spans else None,
        "reported_order_is_not_verified_action_order":True,
    }


GATES={
    "min_events":("events","min"),
    "min_cases":("cases","min"),
    "max_singleton_case_rate_bps":("singleton_case_rate_bps","max"),
    "max_single_activity_case_rate_bps":("single_activity_case_rate_bps","max"),
    "max_largest_activity_rate_bps":("largest_activity_rate_bps","max"),
    "max_timestamp_tie_event_rate_bps":("timestamp_tie_event_rate_bps","max"),
    "min_cases_eligible_for_reported_order":("cases_eligible_for_reported_order","min"),
}


def evaluate(metrics,policy):
    results=[]
    for name,(field,direction) in GATES.items():
        limit=policy.get(name)
        if limit is None:
            continue
        actual=metrics[field]
        if actual is None:
            status="indeterminate"
        elif (actual>=limit if direction=="min" else actual<=limit):
            status="pass"
        else:
            status="fail"
        results.append({"gate":name,"actual":actual,"threshold":limit,"status":status})
    return results
