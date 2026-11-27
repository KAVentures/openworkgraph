"""Conformance of selected reported events, not verified business compliance.

All selectors are exact (source job, published activity). No arbitrary code,
fuzzy matching, timestamp tie breaking, or inferred physical occurrence.
"""
from collections import Counter, defaultdict

OUTCOMES=("satisfied","not_satisfied","indeterminate","not_applicable")


def index_events(rows):
    index=defaultdict(list)
    for job,event in rows:
        index[(job,event["activity"])].append(event["observed_at"])
    for times in index.values():
        times.sort()
    return index


def evaluate(index,rule,providers):
    """Evaluate one bounded rule against one linked-case evidence sample."""
    kind=rule["kind"]
    left=rule["left"]
    a=index.get((left["job_id"],left["activity"]),[])
    result={"left_count":len(a),"right_count":None}
    def finish(status,reason):
        return {**result,"status":status,"reason":reason}
    if kind=="required":
        return finish("satisfied","activity_observed") if a else finish("not_satisfied","activity_not_observed")
    if kind=="forbidden":
        return finish("not_satisfied","forbidden_activity_observed") if a else finish("satisfied","forbidden_activity_not_observed")
    if kind=="count_range":
        lo,hi=rule["minimum"],rule["maximum"]
        return finish("satisfied","count_inside_reviewed_range") if lo<=len(a)<=hi else finish("not_satisfied","count_outside_reviewed_range")
    right=rule["right"]
    b=index.get((right["job_id"],right["activity"]),[])
    result["right_count"]=len(b)
    if kind=="cooccurs":
        # Conditional presence: no antecedent, no obligation.
        if not a:
            return finish("not_applicable","trigger_not_observed")
        return finish("satisfied","response_observed") if b else finish("not_satisfied","response_not_observed")
    if not a:
        return finish("not_applicable","trigger_not_observed")
    if not b:
        return finish("not_satisfied","response_not_observed")
    if providers[left["job_id"]]=="sap" or providers[right["job_id"]]=="sap":
        return finish("indeterminate","business_date_is_not_occurrence_time")
    if kind=="precedes":
        # Every left occurrence strictly precedes every right occurrence.
        if a[-1]<b[0]:
            return finish("satisfied","all_left_points_strictly_before_right")
        if a[-1]>b[0]:
            return finish("not_satisfied","reported_precedence_reversed")
        return finish("indeterminate","reported_boundary_timestamp_tie")
    if kind=="response":
        # For each trigger, at least one strictly later response suffices.
        # Reuse of a response is intentional; this is not event pairing.
        # A tie with the last response is indeterminate, not a violation.
        if a[-1]<b[-1]:
            return finish("satisfied","every_trigger_has_later_reported_response")
        if a[-1]>b[-1]:
            return finish("not_satisfied","trigger_without_later_reported_response")
        return finish("indeterminate","last_trigger_response_timestamp_tie")
    raise ValueError("unrecognized conformance rule")


def counts(results):
    c=Counter(r["status"] for r in results)
    return {k:c[k] for k in OUTCOMES}


def group_status(results):
    seen={r["status"] for r in results}
    for status in ("not_satisfied","indeterminate","satisfied","not_applicable"):
        if status in seen:
            return status
    return "not_applicable"
