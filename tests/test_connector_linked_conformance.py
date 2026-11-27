"""Conformance over exact publication and linked-case review pins."""
from datetime import datetime,timedelta
from random import Random
from itertools import product
import csv
from io import StringIO

import pytest
from pydantic import ValidationError

from shared.connector_linked_conformance import evaluate,index_events,counts,group_status
from shared.connector_event_snapshot import Snapshot,digest,utc
from gateway.connector_linked_conformance import Rule,Catalog,Review,Worklist,catalog,review,worklist,worklist_csv
from gateway.connector_case_review import ReviewedCases,Plan
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import Create,JobError
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db


def selector(job,activity):
    return {"job_id":job,"activity":activity}


def rule(kind,left=None,right=None,**kw):
    return {"kind":kind,"left":left or selector("a","x"),"right":right,**kw}


def test_presence_count_and_conditional_presence():
    idx={("a","x"):["1","2"],("b","y"):["3"]}
    providers={"a":"rest","b":"rest"}
    for kind,kwargs,status in [
        ("required",{},"satisfied"),("forbidden",{},"not_satisfied"),
        ("count_range",{"minimum":2,"maximum":2},"satisfied"),
        ("count_range",{"minimum":0,"maximum":1},"not_satisfied"),
        ("cooccurs",{"right":selector("b","y")},"satisfied"),
        ("cooccurs",{"right":selector("b","missing")},"not_satisfied")]:
        assert evaluate(idx,rule(kind,**kwargs),providers)["status"]==status
    assert evaluate(idx,rule("cooccurs",left=selector("a","missing"),right=selector("b","y")),providers)["status"]=="not_applicable"
    assert evaluate(idx,rule("required",left=selector("b","missing")),providers)["status"]=="not_satisfied"
    assert evaluate(idx,rule("forbidden",left=selector("b","missing")),providers)["status"]=="satisfied"


@pytest.mark.parametrize("a,b,precedence,response",[
    ([],[],"not_applicable","not_applicable"),
    ([1],[],"not_satisfied","not_satisfied"),
    ([1,2],[3,4],"satisfied","satisfied"),
    ([1,3],[2,4],"not_satisfied","satisfied"),
    ([1,2],[2,4],"indeterminate","satisfied"),
    ([1,4],[2,4],"not_satisfied","indeterminate"),
    ([4],[1,2],"not_satisfied","not_satisfied"),
    ([1],[1],"indeterminate","indeterminate"),
])
def test_order_semantics(a,b,precedence,response):
    idx={("a","x"):a,("b","y"):b}
    for kind,expected in (("precedes",precedence),("response",response)):
        assert evaluate(idx,rule(kind,right=selector("b","y")),{"a":"rest","b":"rest"})["status"]==expected
    if a and b:
        for kind in ("precedes","response"):
            assert evaluate(idx,rule(kind,right=selector("b","y")),{"a":"sap","b":"rest"})["status"]=="indeterminate"


def test_randomized_order_rules_against_pairwise_oracle():
    rng=Random(471)
    for _ in range(1500):
        a,b=[sorted(rng.randrange(10) for _ in range(rng.randrange(12))) for i in range(2)]
        if not a:
            p=r="not_applicable"
        elif not b:
            p=r="not_satisfied"
        else:
            p="not_satisfied" if any(x>y for x,y in product(a,b)) else (
                "indeterminate" if any(x==y for x,y in product(a,b)) else "satisfied")
            if all(any(x<y for y in b) for x in a):
                r="satisfied"
            elif any(all(x>y for y in b) for x in a):
                r="not_satisfied"
            else:
                r="indeterminate"
        idx={("a","x"):a,("b","y"):b}
        for kind,expected in (("precedes",p),("response",r)):
            assert evaluate(idx,rule(kind,right=selector("b","y")),{"a":"rest","b":"rest"})["status"]==expected


def test_index_is_namespace_specific_and_deterministic():
    rows=[("b",{"activity":"x","observed_at":"2"}),("a",{"activity":"x","observed_at":"3"}),
          ("a",{"activity":"x","observed_at":"1"})]
    idx=index_events(rows)
    assert idx[("a","x")]==["1","3"] and idx[("b","x")]==["2"]
    assert index_events(list(reversed(rows)))==idx
    assert group_status([{"status":"not_applicable"}])=="not_applicable"
    assert group_status([{"status":"satisfied"},{"status":"indeterminate"}])=="indeterminate"
    assert group_status([{"status":"indeterminate"},{"status":"not_satisfied"}])=="not_satisfied"


@pytest.fixture
def linked(deployed):
    jobs,store=deployed
    a,at,sa,_=source(deployed,labels=True)
    b,bt,sb,_=source(deployed)
    ga=grant(store,a,sa,confirm_activity_labels=True);gb=grant(store,b,sb)
    upload(store,at,ga,sa);upload(store,bt,gb,sb)
    reviewed=ReviewedCases(store)
    spec={"snapshots":[pin(ga),pin(gb)],"confirm_inspection":True,"groups":[
        {"members":[{"job_id":a["job_id"],"case_id":sa.events[0].case_id},
                    {"job_id":b["job_id"],"case_id":sb.events[0].case_id}],
         "basis":"reviewed_business_record"}]}
    base=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    req={**spec,"expected_review_sha256":base["review_sha256"]}
    return reviewed,req,ga,gb,sa,sb


def selected_rules(ga,gb,sa,sb):
    a=selector(ga["job_id"],sa.events[0].activity)
    b=selector(gb["job_id"],sb.events[0].activity)
    return [{"rule_id":"required_a","kind":"required","left":a},
            {"rule_id":"forbidden_b","kind":"forbidden","left":b},
            {"rule_id":"presence_pair","kind":"cooccurs","left":a,"right":b},
            {"rule_id":"count_a","kind":"count_range","left":a,"minimum":1,"maximum":1}]


def test_catalog_is_pinned_paged_and_only_linked_activities(linked):
    reviewed,req,ga,gb,sa,sb=linked
    spec={**req,"confirm_rule_catalog":True,"limit":1}
    first=catalog(reviewed,"customer",Catalog.model_validate(spec),"owner")
    assert first["total_entries"]==4 and first["next_offset"]==1
    second=catalog(reviewed,"customer",Catalog.model_validate({**spec,"offset":1,
                   "expected_catalog_sha256":first["catalog_sha256"]}),"owner")
    assert second["entries"]!=first["entries"] and second["catalog_sha256"]==first["catalog_sha256"]
    assert "case_id" not in second["entries"][0] and "event_id" not in second["entries"][0]
    with pytest.raises(ValidationError):
        Catalog.model_validate({**spec,"offset":1})
    with pytest.raises(JobError,match="conformance_catalog_changed"):
        catalog(reviewed,"customer",Catalog.model_validate({**spec,"expected_catalog_sha256":"a"*64}),"owner")


def test_review_and_full_worklist_with_exact_pins(linked):
    reviewed,req,ga,gb,sa,sb=linked
    spec={**req,"rules":selected_rules(ga,gb,sa,sb),"confirm_conformance_review":True}
    result=review(reviewed,"customer",Review.model_validate(spec),"owner")
    assert result["summary"]["group_outcomes"]["not_satisfied"]==1
    assert result["summary"]["rule_outcomes"]["satisfied"]==3
    assert result["summary"]["rule_outcomes"]["not_satisfied"]==1
    assert result["summary"]["evaluations"]==4
    assert not result["actual_process_compliance_verified"]
    reverse={**spec,"snapshots":list(reversed(spec["snapshots"])),"rules":list(reversed(spec["rules"]))}
    assert review(reviewed,"customer",Review.model_validate(reverse),"owner")==result
    filtered=review(reviewed,"customer",Review.model_validate({**spec,"filter_status":"satisfied",
        "expected_conformance_sha256":result["conformance_sha256"]}),"owner")
    assert filtered["groups"]==[] and filtered["summary"]==result["summary"]
    request={**spec,"expected_conformance_sha256":result["conformance_sha256"],"confirm_worklist_export":True}
    exported=worklist(reviewed,"customer",Worklist.model_validate(request),"owner")
    assert exported["exception_count"]==1
    rows=list(csv.DictReader(StringIO(worklist_csv(exported))))
    assert rows[0]["rule_id"]=="forbidden_b" and rows[0]["status"]=="not_satisfied"
    assert rows[0]["conformance_sha256"]==result["conformance_sha256"]
    assert "Private label" not in worklist_csv(exported)
    for e in [*sa.events,*sb.events]:
        assert e.event_id not in worklist_csv(exported) and e.case_id not in worklist_csv(exported)
    with pytest.raises(JobError,match="explicit_conformance_worklist"):
        worklist(reviewed,"customer",Worklist.model_validate({**request,"confirm_worklist_export":False}),"owner")


def test_unknown_selectors_rule_shape_and_stale_review_fail_closed(linked):
    reviewed,req,ga,gb,sa,sb=linked
    rules=selected_rules(ga,gb,sa,sb)
    spec={**req,"rules":rules,"confirm_conformance_review":True}
    for changes in ({"rules":[rules[0],rules[0]]},
                    {"rules":[{**rules[0],"rule_id":"-formula"}]},
                    {"rules":[{**rules[0],"minimum":1}]},
                    {"rules":[{**rules[0],"kind":"precedes","right":rules[0]["left"]}]}):
        with pytest.raises(ValidationError):
            Review.model_validate({**spec,**changes})
    bad={**rules[0],"left":selector(ga["job_id"],"nonexistent")}
    with pytest.raises(JobError,match="selector_not_observed"):
        review(reviewed,"customer",Review.model_validate({**spec,"rules":[bad]}),"owner")
    with pytest.raises(JobError,match="cross_source_review_changed"):
        review(reviewed,"customer",Review.model_validate({**spec,"expected_review_sha256":"e"*64}),"owner")
    with pytest.raises(JobError,match="conformance_review_changed"):
        review(reviewed,"customer",Review.model_validate({**spec,"expected_conformance_sha256":"f"*64}),"owner")
    with pytest.raises(JobError,match="explicit_conformance_rule_review"):
        review(reviewed,"customer",Review.model_validate({**spec,"confirm_conformance_review":False}),"owner")


def test_revoked_publication_blocks_catalog_and_worklist(linked):
    reviewed,req,ga,gb,sa,sb=linked
    spec={**req,"rules":selected_rules(ga,gb,sa,sb),"confirm_conformance_review":True}
    r=review(reviewed,"customer",Review.model_validate(spec),"owner")
    reviewed.publications.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        catalog(reviewed,"customer",Catalog.model_validate({**req,"confirm_rule_catalog":True}),"owner")
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        worklist(reviewed,"customer",Worklist.model_validate({**spec,
            "expected_conformance_sha256":r["conformance_sha256"],"confirm_worklist_export":True}),"owner")
