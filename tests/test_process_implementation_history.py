"""Independent implementation-history cent arithmetic and retained audit tests."""
from datetime import datetime,timezone
from copy import deepcopy
from decimal import Decimal
import random

import pytest
from pydantic import ValidationError

from shared import process_implementation_history as h
from gateway.process_implementation_plans import ImplementationPlans,namespace
from gateway.process_collaboration import ProcessCollaborationError as Error
from test_process_implementation_plans import setup,baseline_save,freeze,reports
from test_process_collaboration import store,person

TODAY=datetime.now(timezone.utc).date().isoformat()


def fmt(cents):
    return str(Decimal(cents)/100)


def synthetic(phase_ids,rng,revision):
    phases=[];reports=[]
    for k in phase_ids:
        phases.append({"id":k,"name":k,"cost":fmt(rng.randrange(10000)),"effort_hours":fmt(rng.randrange(1000)),
                       "starts_on":"2024-01-01","finishes_on":"2024-01-05"})
        if rng.randrange(4):
            amounts={key:fmt(rng.randrange(10000)) if rng.randrange(4) else None for key in
                     ("cost_to_date","remaining_cost_estimate","effort_hours_to_date","remaining_effort_hours_estimate")}
            reports.append({"phase_id":k,"status":"in_progress","started_on":None,"finished_on":None,**amounts})
    return {"revision":revision,"content_sha256":str(revision)*64,"baseline_sha256":str(revision)*64,
            "assessed_in_revision":revision,"baseline":{"currency":"USD","opportunity":{},"phases":phases},
            "reports":reports,"report_attributions":[],"review":None,"saved_at":"2024-01-01T00:00:00Z",
            "baseline_author_actor_id":"a","author_actor_id":"a"}


def test_randomized_independent_integer_cent_scope_bridges_and_partial_bounds():
    rng=random.Random(1565)
    for _ in range(200):
        a,b=[synthetic([str(i) for i in range(12) if rng.random()<.7] or ["0"],rng,r) for r in (1,2)]
        result=h.transition(a,b)
        for metric,keys in h.METRICS.items():
            def oracle(record):
                reports={r["phase_id"]:r for r in record["reports"]}
                out={}
                for p in record["baseline"]["phases"]:
                    r=reports.get(p["id"],{})
                    def cents(v):return int(Decimal(v)*100) if v is not None else None
                    x,y=cents(r.get(keys[1])),cents(r.get(keys[2]))
                    out[p["id"]]={"baseline":cents(p[keys[0]]),"actual":x,"remaining":y,
                                  "forecast":x+y if x is not None and y is not None else None}
                return out
            old,new=oracle(a),oracle(b)
            bridge=result[metric+"_bridge"]["amounts"]
            for field,v in bridge.items():
                retained=added=removed=0;unknown=[]
                for k in sorted(old.keys()|new.keys()):
                    x=old[k][field] if k in old else 0
                    y=new[k][field] if k in new else 0
                    if x is None or y is None:unknown.append(k)
                    elif k not in old:added+=y
                    elif k not in new:removed-=x
                    else:retained+=y-x
                assert v["unresolved_phase_ids"]==unknown
                assert Decimal(v["retained_phase_change"])*100==retained
                assert Decimal(v["added_phase_amount"])*100==added
                assert Decimal(v["removed_phase_amount_signed"])*100==removed
                assert v["complete_change"] is None if unknown else Decimal(v["complete_change"])*100==retained+added+removed
            for record,expected in [(a,old),(b,new)]:
                s=h.metric_snapshot(record,keys)
                for field in ("baseline","actual","remaining","forecast"):
                    known=[x[field] for x in expected.values() if x[field] is not None]
                    lower=sum(known) if field!="forecast" else sum((x["actual"] or 0)+(x["remaining"] or 0) for x in expected.values())
                    assert Decimal(s[field]["nonnegative_known_lower_bound"])*100==lower
                    assert s[field]["unknown_phases"]==len(expected)-len(known)
                    assert s[field]["complete_total"] is None if len(known)!=len(expected) else Decimal(s[field]["complete_total"])*100==lower


def request(plans,wid,before,after,day=TODAY):
    inv=plans.history_inventory(person(),wid)
    return h.ReviewRequest(before_revision=before,after_revision=after,as_of_date=day,
                           expected_history_sha256=inv["history_sha256"])


def test_real_history_approval_forecast_lower_bound_and_stale_pin(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r["workflow_id"]
    baseline_save(plans,r,baseline);freeze(plans,r)
    reports(plans,r,[{"phase_id":"analysis","cost_to_date":"2500","remaining_cost_estimate":"100"}],who="ed")
    req=request(plans,wid,1,3)
    result=plans.history_review(person(),wid,req)
    assert len(result["timeline"])==3
    assert result["comparison"]["stored_review_transition"]=="added"
    assert result["timeline"][2]["cost"]["forecast"]["complete_total"] is None
    assert result["timeline"][2]["cost"]["forecast"]["nonnegative_known_lower_bound"]=="2600.00"
    assert result["current_budget_review"]["known_lower_bound_exceeds_budget"]
    assert result["current_budget_review"]["minimum_excess_over_budget"]=="600.00"
    assert result["current_budget_review"]["headroom_not_established_if_unknown"]
    assert not result["execution_authorized"]
    reports(plans,r,[{"phase_id":"build","cost_to_date":"0","remaining_cost_estimate":"700"}])
    with pytest.raises(Error,match="history changed"):plans.history_review(person(),wid,req)
    new=plans.history_review(person(),wid,request(plans,wid,3,4))
    assert new["comparison"]["cost_bridge"]["amounts"]["forecast"]["complete_change"] is None
    assert new["timeline"][-1]["cost"]["forecast"]["complete_total"]=="3300.00"
    assert plans.history_review(person(),wid,request(plans,wid,1,2))["current_budget_review"] is None


def test_identical_rebaseline_clears_review_even_when_digest_unchanged(store):
    plans=ImplementationPlans(store);r,_,baseline=setup(store);wid=r["workflow_id"]
    baseline_save(plans,r,baseline);freeze(plans,r)
    baseline_save(plans,r,baseline,revision=2)
    result=plans.history_review(person(),wid,request(plans,wid,2,3))
    c=result["comparison"]
    assert c["baseline_reassessed"] and not c["baseline_content_changed"]
    assert c["stored_review_transition"]=="cleared"
    assert c["cost_bridge"]["amounts"]["baseline"]["complete_change"]=="0.00"


def test_calendar_excludes_future_record_and_future_report_dates():
    r=synthetic(["a","b"],random.Random(1),1)
    r["reports"]=[{"phase_id":"a","status":"in_progress","started_on":"2024-01-02","finished_on":None},
                  {"phase_id":"b","status":"completed","started_on":"2024-01-01","finished_on":"2024-02-01"}]
    r["report_attributions"]=[{"phase_id":"a","at":"2024-01-02T00:00:00Z"},{"phase_id":"b","at":"2024-01-03T00:00:00Z"}]
    review=h.calendar(r,"2024-01-10")
    a,b=review["phases"]
    assert a["days_past_planned_finish_without_terminal_report"]==5
    assert a["reported_start_delta_days"]==1
    assert b["flags"]==["reported_date_after_review_date"]
    assert b["reported_finish_delta_days"] is None
    earlier=h.calendar(r,"2023-12-31")
    assert all(not x["report_usable_for_date_review"] for x in earlier["phases"])
    assert earlier["baseline_saved_after_review_date"]


def test_history_corruption_is_not_silently_skipped(store):
    plans=ImplementationPlans(store);r,_,baseline=setup(store);wid=r["workflow_id"]
    baseline_save(plans,r,baseline);freeze(plans,r)
    with store.write_transaction() as c:
        plans.q(c,"DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=? AND revision=1",(namespace("acme"),wid))
    with pytest.raises(Error,match="incomplete"):plans.history_inventory(person(),wid)


@pytest.mark.parametrize("changes",[{"before_revision":True},{"after_revision":1},{"as_of_date":"9999-12-31"},
    {"as_of_date":"2024-02-30"},{"expected_history_sha256":"bad"},{"extra":True}])
def test_strict_history_request(changes):
    values={"before_revision":1,"after_revision":2,"as_of_date":"2024-01-01","expected_history_sha256":"a"*64,**changes}
    with pytest.raises(ValidationError):h.ReviewRequest.model_validate(values)
