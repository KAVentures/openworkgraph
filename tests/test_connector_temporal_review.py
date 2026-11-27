"""Reported-time analysis: arithmetic oracle and actual publication review."""
from datetime import datetime,timedelta,timezone
from itertools import product
from random import Random

import pytest
from pydantic import ValidationError

from shared.connector_temporal_intervals import reported_interval,compare,span
from shared.connector_event_snapshot import Snapshot,Manifest,digest,utc
from gateway.connector_temporal_review import Alignment,temporal_review
from gateway.connector_case_review import ReviewedCases,Plan
from gateway.connector_jobs import Create,JobError
from gateway.connector_event_publications import Remove
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db


def oracle(a,b):
    counts=dict.fromkeys(("left_strictly_before_right","right_strictly_before_left",
                         "equal_reported_points","overlapping_intervals","unaligned_date_pairs"),0)
    for x,y in product(a,b):
        if x is None or y is None:k="unaligned_date_pairs"
        elif x[1]<y[0]:k="left_strictly_before_right"
        elif y[1]<x[0]:k="right_strictly_before_left"
        elif x[0]==x[1]==y[0]==y[1]:k="equal_reported_points"
        else:k="overlapping_intervals"
        counts[k]+=1
    return counts


def test_random_pair_counts_match_quadratic_oracle():
    rng=Random(311)
    def events(n):
        return [None if rng.randrange(5)==0 else tuple(sorted((rng.randrange(10),rng.randrange(10)))) for _ in range(n)]
    for _ in range(500):
        a,b=events(rng.randrange(20)),events(rng.randrange(20))
        r=compare(a,b);expected=oracle(a,b)
        assert {k:r[k] for k in expected}==expected
        assert sum(expected.values())==r["event_pairs"]==len(a)*len(b)


def test_span_extrema_match_exhaustive_discrete_oracle():
    rng=Random(123)
    for _ in range(250):
        intervals=[tuple(sorted((rng.randrange(6),rng.randrange(6)))) for _ in range(rng.randrange(1,5))]
        possible=[max(row)-min(row) for row in product(*(range(lo,hi+1) for lo,hi in intervals))]
        r=span(intervals)
        assert r["minimum_seconds"]==min(possible)/1e6
        assert r["maximum_seconds"]==max(possible)/1e6
    assert span([None,(1,1)]) is None
    assert span([]) is None


def test_dst_calendar_boundaries_and_shared_boundary():
    spring=reported_interval("2026-03-29T00:00:00Z",day=True,timezone_name="Europe/Stockholm")
    autumn=reported_interval("2026-10-25T00:00:00Z",day=True,timezone_name="Europe/Stockholm")
    assert spring[1]-spring[0]==23*3600*1e6
    assert autumn[1]-autumn[0]==25*3600*1e6
    boundary=(spring[1],spring[1])
    r=compare([spring],[boundary])
    assert r["overlapping_intervals"]==1 and r["separation"] is None
    assert reported_interval("2026-03-29T00:00:00Z",day=True) is None
    with pytest.raises(ValueError,match="canonical UTC midnight"):
        reported_interval("2026-03-29T01:00:00Z",day=True)
    with pytest.raises(ValueError,match="nonexistent"):
        reported_interval("2011-12-30T00:00:00Z",day=True,timezone_name="Pacific/Apia")


def test_whole_source_separation_and_gap_not_pair_mean():
    a=[(0,2_000_000),(1_000_000,3_000_000)]
    b=[(7_000_000,8_000_000),(9_000_000,9_000_000)]
    r=compare(a,b)
    assert r["left_strictly_before_right"]==4
    assert r["separation"]=="left_before_right"
    assert r["reported_boundary_gap_bounds"]=={"minimum_seconds":4.0,"maximum_seconds":7.0}
    assert compare(b,a)["separation"]=="right_before_left"
    assert compare([*a,None],b)["reported_boundary_gap_bounds"] is None
    assert compare(a,[(3_000_000,3_000_000)])["separation"] is None


def test_large_pair_count_without_materializing_pairs():
    a=[(i,i) for i in range(10000)]
    b=[(i,i) for i in range(10000)]
    r=compare(a,b)
    assert r["event_pairs"]==100_000_000
    assert r["equal_reported_points"]==10_000
    assert r["left_strictly_before_right"]==r["right_strictly_before_left"]==49_995_000


@pytest.fixture
def mixed(deployed):
    jobs,pubs=deployed
    point,token,snap,_=source(deployed)
    pg=grant(pubs,point,snap);upload(pubs,token,pg,snap)
    # Construct an explicit synthetic SAP publication on the same day as
    # the REST fixture. Real Gateway grants/storage and pin checks apply.
    stamp=datetime.fromisoformat(snap.events[0].observed_at).replace(hour=0,minute=0,second=0,microsecond=0)
    p=jobs.create(Create(organization_id="customer",name="Business date fixture",provider="sap",
         profile_id="rest_"+"f"*24,profile_revision=1,profile_sha256="b"*64,
         interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True),"owner")
    day=utc(stamp.isoformat());end=utc((stamp+timedelta(days=1)).isoformat())
    rows=[{"event_id":"a"*64,"case_id":"b"*64,"activity":"A_"+"c"*64,"observed_at":day}]
    m=Manifest(profile_id=p["job"]["profile_id"],profile_revision=1,profile_sha256="b"*64,
        provider="sap",since=day,until=end,checkpoint=end,event_count=1,case_count=1,
        activity_mode="coded",rows_sha256=digest(rows))
    dates=Snapshot(manifest=m,events=rows)
    dg=grant(pubs,p["job"],dates);upload(pubs,p["worker_token"],dg,dates)
    reviewed=ReviewedCases(pubs)
    plan={"snapshots":[pin(pg),pin(dg)],"confirm_inspection":True,"groups":[
        {"members":[{"job_id":point["job_id"],"case_id":snap.events[0].case_id},
                    {"job_id":p["job"]["job_id"],"case_id":"b"*64}],"basis":"reviewed_business_record"}]}
    preview=reviewed.preview("customer",Plan.model_validate(plan),"owner")
    req={**plan,"expected_review_sha256":preview["review_sha256"],"confirm_temporal_assumptions":True}
    return reviewed,req,dg,pg


def test_unknown_date_suppresses_order_and_span_and_reviewed_alignment_is_pinned(mixed):
    reviewed,req,dg,pg=mixed
    result=temporal_review(reviewed,"customer",Alignment.model_validate(req),"owner")
    assert result["summary"]["unaligned_date_pairs"]==2
    assert result["summary"]["event_pairs"]==2
    assert result["groups"][0]["reported_span_bounds"] is None
    assert result["unaligned_business_date_jobs"]==[dg["job_id"]]
    aligned={**req,"business_date_timezones":{dg["job_id"]:"UTC"}}
    r=temporal_review(reviewed,"customer",Alignment.model_validate(aligned),"owner")
    assert r["summary"]["overlapping_intervals"]==2
    assert r["summary"]["unaligned_date_pairs"]==0
    assert r["groups"][0]["reported_span_bounds"] is not None
    assert not r["physical_occurrence_times_verified"] and not r["independently_verified_handoffs"]
    assert r["temporal_scope_sha256"]!=result["temporal_scope_sha256"]
    with pytest.raises(JobError,match="temporal_scope_changed"):
        temporal_review(reviewed,"customer",Alignment.model_validate({**aligned,
            "expected_temporal_scope_sha256":result["temporal_scope_sha256"]}),"owner")
    with pytest.raises(JobError,match="timezone_requires_linked_business_date_source"):
        temporal_review(reviewed,"customer",Alignment.model_validate({**req,
            "business_date_timezones":{pg["job_id"]:"UTC"}}),"owner")


def test_consent_exact_review_and_revocation(mixed):
    reviewed,req,dg,pg=mixed
    with pytest.raises(JobError,match="explicit_temporal_assumption"):
        temporal_review(reviewed,"customer",Alignment.model_validate({**req,"confirm_temporal_assumptions":False}),"owner")
    with pytest.raises(JobError,match="cross_source_review_changed"):
        temporal_review(reviewed,"customer",Alignment.model_validate({**req,"expected_review_sha256":"e"*64}),"owner")
    with pytest.raises(ValidationError):
        Alignment.model_validate({**req,"offset":1})
    with pytest.raises(ValidationError):
        Alignment.model_validate({**req,"business_date_timezones":{dg["job_id"]:"../etc/passwd"}})
    reviewed.publications.remove("customer",dg["job_id"],Remove(expected_publication_revision=dg["revision"],confirm_delete=True),"owner",dg["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        temporal_review(reviewed,"customer",Alignment.model_validate(req),"owner")


def test_case_weighted_directional_rollup_excludes_overlapping_and_unknown():
    from gateway.connector_temporal_review import summarize_source_pairs
    def group(a,b):
        return {"source_pairs":[{"left_job_id":"a","right_job_id":"b",**compare(a,b)}]}
    rows=[group([(0,0)],[(1_000_000,2_000_000)]),
          group([(0,0)]*100,[(5_000_000,8_000_000)]*100),
          group([(10_000_000,10_000_000)],[(0,1_000_000)]),
          group([(0,2_000_000)],[(1_000_000,1_000_000)]),
          group([None],[(1_000_000,1_000_000)])]
    r=summarize_source_pairs(rows,{"a":"sap","b":"rest"})[0]
    assert r["linked_groups"]==5
    assert r["wholly_left_before_right_groups"]==2
    assert r["wholly_right_before_left_groups"]==1
    assert r["not_wholly_separated_groups"]==2
    assert r["groups_with_overlaps"]==1 and r["groups_with_unaligned_dates"]==1
    assert r["pair_counts"]["event_pairs"]==10004
    d=r["left_before_right_gap_distribution"]
    assert d["groups"]==2
    assert d["p50_bounds"]=={"minimum_seconds":1.0,"maximum_seconds":2.0}
    assert d["p90_bounds"]=={"minimum_seconds":5.0,"maximum_seconds":8.0}
    assert r["right_before_left_gap_distribution"]["groups"]==1


def test_temporal_output_is_stable_across_selection_order(mixed):
    reviewed,req,dg,pg=mixed
    r=temporal_review(reviewed,"customer",Alignment.model_validate(req),"owner")
    reorder={**req,"snapshots":list(reversed(req["snapshots"])),
             "groups":[{**req["groups"][0],"members":list(reversed(req["groups"][0]["members"]))}],
             "expected_temporal_scope_sha256":r["temporal_scope_sha256"]}
    again=temporal_review(reviewed,"customer",Alignment.model_validate(reorder),"owner")
    assert again==r
    assert r["source_pair_rollup"][0]["groups_with_unaligned_dates"]==1
    assert r["source_pair_rollup"][0]["left_before_right_gap_distribution"] is None
