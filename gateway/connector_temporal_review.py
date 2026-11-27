"""Owner-reviewed reported-time diagnostics over immutable snapshot pins."""
from collections import defaultdict, Counter
from itertools import combinations
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import DIGEST,digest
from shared.connector_temporal_intervals import reported_interval,compare,span,zone
from .connector_case_review import Plan,group_key
from .connector_jobs import JobError,JOB

FORMAT="openworkgraph.connector-temporal-review.v1"
MAX_PAGE=50
METRICS=("event_pairs","left_strictly_before_right","right_strictly_before_left",
         "equal_reported_points","overlapping_intervals","unaligned_date_pairs")


class Alignment(Plan):
    expected_review_sha256:str=Field(pattern=DIGEST)
    confirm_temporal_assumptions:StrictBool
    business_date_timezones:dict[str,str]=Field(default_factory=dict,max_length=10)
    offset:StrictInt=Field(ge=0,le=500,default=0)
    limit:StrictInt=Field(ge=1,le=MAX_PAGE,default=20)
    expected_temporal_scope_sha256:str|None=Field(default=None,pattern=DIGEST)

    @model_validator(mode="after")
    def check_alignment(self):
        if self.offset and self.expected_temporal_scope_sha256 is None:
            raise ValueError("pin temporal scope before requesting further pages")
        ids={p.job_id for p in self.snapshots}
        if set(self.business_date_timezones)-ids:
            raise ValueError("timezone specified outside snapshot scope")
        for value in self.business_date_timezones.values():
            zone(value)
        return self


def _group_result(group,cases,providers,scope,zones):
    by_source=defaultdict(list)
    reported_dates=defaultdict(list)
    for job,case in group_key(group):
        for event in cases[job,case]:
            at=event["observed_at"]
            day=providers[job]=="sap"
            try:
                interval=reported_interval(at,day=day,timezone_name=zones.get(job))
            except ValueError:
                raise JobError("invalid_or_unalignable_reported_business_date",422) from None
            by_source[job].append(interval)
            if day:
                reported_dates[job].append(at[:10])
    sources=[]
    for job,intervals in sorted(by_source.items()):
        dates=reported_dates.get(job,[])
        sources.append({"job_id":job,"provider":providers[job],"event_count":len(intervals),
                        "reported_time_kind":"business_date" if dates else "reported_point",
                        "operator_assumed_timezone":zones.get(job),
                        "unaligned_events":sum(v is None for v in intervals),
                        "first_business_date":min(dates) if dates else None,
                        "last_business_date":max(dates) if dates else None,
                        "reported_span_bounds":span(intervals)})
    pairs=[]
    for a,b in combinations(sorted(by_source),2):
        pairs.append({"left_job_id":a,"right_job_id":b,**compare(by_source[a],by_source[b])})
    intervals=[v for values in by_source.values() for v in values]
    return {"linked_case_id":digest({"scope_sha256":scope,"members":group_key(group)}),
            "event_count":len(intervals),"sources":sources,"source_pairs":pairs,
            "reported_span_bounds":span(intervals),
            "contains_business_date_evidence":bool(reported_dates),
            "unaligned_events":sum(v is None for v in intervals),
            "physical_occurrence_times_verified":False}


def nearest_rank(values,percent):
    """Case-weighted empirical quantile, never pair-weighted."""
    ordered=sorted(values)
    return ordered[max(0,(len(ordered)*percent+99)//100-1)]


def gap_distribution(gaps):
    if not gaps:
        return None
    lo=[g["minimum_seconds"] for g in gaps]
    hi=[g["maximum_seconds"] for g in gaps]
    return {"groups":len(gaps),"minimum_lower_bound_seconds":min(lo),
            "maximum_upper_bound_seconds":max(hi),
            "p50_bounds":{"minimum_seconds":nearest_rank(lo,50),"maximum_seconds":nearest_rank(hi,50)},
            "p90_bounds":{"minimum_seconds":nearest_rank(lo,90),"maximum_seconds":nearest_rank(hi,90)}}


def summarize_source_pairs(group_rows,providers):
    """One case-group vote per source pair; directional gap distributions
    include only wholly separated groups in the corresponding direction.
    The inputs are already bounded by the exact publication budget.
    """
    totals={}
    for row in group_rows:
        for pair in row["source_pairs"]:
            a,b=pair["left_job_id"],pair["right_job_id"]
            k=(a,b)
            if k not in totals:
                totals[k]={"metrics":Counter({m:0 for m in METRICS}),"groups":0,
                    "left_gaps":[],"right_gaps":[],"unaligned_groups":0,"overlap_groups":0,
                    "tie_groups":0,"mixed_direction_groups":0}
            t=totals[k]
            t["groups"]+=1
            t["metrics"].update({m:pair[m] for m in METRICS})
            t["unaligned_groups"]+=bool(pair["unaligned_date_pairs"])
            t["overlap_groups"]+=bool(pair["overlapping_intervals"])
            t["tie_groups"]+=bool(pair["equal_reported_points"])
            t["mixed_direction_groups"]+=bool(pair["left_strictly_before_right"] and pair["right_strictly_before_left"])
            if pair["separation"]=="left_before_right":
                t["left_gaps"].append(pair["reported_boundary_gap_bounds"])
            elif pair["separation"]=="right_before_left":
                t["right_gaps"].append(pair["reported_boundary_gap_bounds"])
    results=[]
    for (a,b),t in sorted(totals.items()):
        left,right=len(t["left_gaps"]),len(t["right_gaps"])
        results.append({"left_job_id":a,"left_provider":providers[a],
            "right_job_id":b,"right_provider":providers[b],"linked_groups":t["groups"],
            "wholly_left_before_right_groups":left,"wholly_right_before_left_groups":right,
            "not_wholly_separated_groups":t["groups"]-left-right,
            # These diagnostic flags may overlap within a group.
            "groups_with_unaligned_dates":t["unaligned_groups"],
            "groups_with_overlaps":t["overlap_groups"],
            "groups_with_equal_reported_points":t["tie_groups"],
            "groups_with_pairs_in_both_directions":t["mixed_direction_groups"],
            "left_before_right_gap_distribution":gap_distribution(t["left_gaps"]),
            "right_before_left_gap_distribution":gap_distribution(t["right_gaps"]),
            "pair_counts":dict(t["metrics"])})
    return results


def temporal_review(reviewed,org,spec,principal):
    if not spec.confirm_temporal_assumptions:
        raise JobError("explicit_temporal_assumption_review_required",403)
    with reviewed.jobs._tx(org) as c:
        loaded=reviewed._read(c,org,spec)
        cases,manifests,providers,count,scope,expires=loaded
        _,base=reviewed._compute(org,spec,loaded)
        if base["review_sha256"]!=spec.expected_review_sha256:
            raise JobError("cross_source_review_changed")
        linked_jobs={m.job_id for group in spec.groups for m in group.members}
        date_jobs={job for job in linked_jobs if providers[job]=="sap"}
        if set(spec.business_date_timezones)-date_jobs:
            raise JobError("timezone_requires_linked_business_date_source",422)
        zones=dict(sorted(spec.business_date_timezones.items()))
        totals=Counter({k:0 for k in METRICS})
        rows=[];all_groups=[];unknown=bounded=events=0
        groups=sorted(spec.groups,key=group_key)
        if spec.offset>=len(groups) and spec.offset!=0:
            raise JobError("temporal_page_outside_scope",422)
        for i,group in enumerate(groups):
            row=_group_result(group,cases,providers,scope,zones)
            all_groups.append(row)
            events+=row["event_count"]
            unknown+=row["unaligned_events"]
            bounded+=row["reported_span_bounds"] is not None
            for pair in row["source_pairs"]:
                totals.update({k:pair[k] for k in METRICS})
            if spec.offset<=i<spec.offset+spec.limit:
                rows.append(row)
        rollup=summarize_source_pairs(all_groups,providers)
        # Bind the actual derived result as well as the assumptions; a
        # timezone database update cannot silently change subsequent pages.
        temporal_scope=digest({"format":FORMAT,"review_sha256":base["review_sha256"],
                               "business_date_timezones":zones,"groups":all_groups,"source_pair_rollup":rollup,
                               "policy":"closed_intervals_reported_dates_not_occurrence"})
        if spec.expected_temporal_scope_sha256 and spec.expected_temporal_scope_sha256!=temporal_scope:
            raise JobError("temporal_scope_changed")
        reviewed._fresh(expires)
        reviewed._audit(c,org,spec,principal,"temporal_review",
                        {"review_sha256":base["review_sha256"],"temporal_scope_sha256":temporal_scope,
                         "linked_groups":len(groups),"groups_returned":len(rows)})
        return {"format":FORMAT,"review_sha256":base["review_sha256"],"scope_sha256":scope,
                "temporal_scope_sha256":temporal_scope,"expires_at":expires,
                "business_date_timezones":zones,
                "unaligned_business_date_jobs":sorted(date_jobs-set(zones)),
                "summary":{"linked_groups":len(groups),"linked_events":events,
                           "groups_with_reported_span_bounds":bounded,"unaligned_events":unknown,
                           **dict(totals)},
                "source_pair_rollup":rollup,
                "gap_quantile_method":"nearest_rank_one_vote_per_wholly_separated_linked_group",
                "groups":rows,"offset":spec.offset,
                "next_offset":spec.offset+len(rows) if spec.offset+len(rows)<len(groups) else None,
                "independently_verified_handoffs":False,"physical_occurrence_times_verified":False,
                "source_completeness_verified":False,"unlinked_cases_included":False,
                "interpretation":"Ordering and span bounds describe reported timestamp points and operator-aligned business-date intervals only. Equal points do not establish simultaneous actions; overlapping intervals do not establish order. No actual handoff, waiting time, physical action time, causal relationship or source completeness is verified."}
