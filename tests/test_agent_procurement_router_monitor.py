"""Multi-window monitoring on the existing frozen pilot fixture."""
from copy import deepcopy

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_router_pilot import review as pilot_review, decide as pilot_decide
from shared.agent_procurement_router_monitor import freeze, review, decide, operation, SNAPSHOT
from test_agent_procurement_router_pilot import pilot, receipt_request, version_change, rehearsal, trial, study
from test_agent_procurement_operating_cost import amount


@pytest.fixture
def monitor(pilot):
    packet,u,p,c=pilot
    result=pilot_review(*pilot);req=receipt_request(result)
    initial={"upstream":u,"pilot_plan":p,"captures":c,"request":req,"receipt":pilot_decide(*pilot,req)}
    req={"monitor_id":"monitor-1","coordinator_alias":"monitor-owner",
         "external_monitoring_approval_sha256":digest("monitor-approval"),
         "maximum_reporting_delay_ms":100,"confirmed":True,
         "windows":[{"window_id":"w"+str(i+1),"start_ms":2000+i*1000,"end_exclusive_ms":3000+i*1000,
                     "allocation_seed_sha256":digest(["monitor-seed",i]),
                     "cases":[{"case_token_sha256":digest(["monitor",i,cat,k]),"reviewed_category":cat}
                               for cat in ("normal","exception","missing_facts") for k in range(4)]}
                    for i in range(2)]}
    plan=freeze(packet,initial,req)
    snapshot={"format":SNAPSHOT,"monitor_plan_sha256":plan["monitor_plan_sha256"],"as_of_ms":4000,"windows":[]}
    for w in plan["windows"]:
        p=w["plan"];obs=[]
        for s in p["slots"]:
            cid=s["intended_candidate_id"]
            obs.append({"slot_id":s["slot_id"],"case_token_sha256":s["case_token_sha256"],
                "actual_router_configuration_sha256":s["expected_router_configuration_sha256"],
                "actual_destination":"agent" if cid else "manual","actual_candidate_id":cid,
                "actual_candidate_configuration_sha256":p["configured_candidates"].get(cid),
                "observed_at_ms":p["request"]["window_start_ms"]+500,"status":"completed","business_outcome":"success",
                "critical_incident":False,"forbidden_action":False,"used_fallback":False,
                "direct_cost_microunits":100 if s["arm"]=="incumbent" else 60,"human_person_ms":36000,
                "elapsed_ms":500,"source_evidence_sha256":digest(["monitor-source",s["slot_id"]])})
        snapshot["windows"].append({"window_id":w["window_id"],"observations":obs})
    return packet,initial,plan,snapshot


def pin(result,snapshot):
    return {"snapshot":deepcopy(snapshot),"expected_snapshot_sha256":result["snapshot_sha256"]}


def decision(result,disposition="refer_for_continued_monitoring_review"):
    return {"disposition":disposition,"reviewer_alias":"reviewer-b","confirmed":True,
            "expected_monitor_plan_sha256":result["monitor_plan_sha256"],"expected_report_sha256":result["report_sha256"]}


def test_progress_from_open_window_to_complete_with_retained_pin(monitor):
    packet,initial,plan,final=monitor
    assert freeze(packet,initial,plan["request"])==plan
    assert plan["scheduled_cases"]==24
    first=deepcopy(final);first["as_of_ms"]=2999;first["windows"].pop()
    early=review(packet,initial,plan,first,None)
    assert early["recommended_review"]=="hold"
    assert early["started_window_cases"]==12 and early["windows"][1]["state"]=="future"
    assert not early["prior_checkpoint_extension_verified"]
    result=review(*monitor,pin(early,first))
    assert result["recommended_review"]=="continued_monitoring_review"
    assert result["prior_checkpoint_extension_verified"]
    assert result["completed_passing_windows"]==2
    assert result["recorded_cases"]==24
    assert len(result["closed_prefix_checks"])==2
    for cat in result["cumulative_category_reviews"]:
        assert cat["incumbent"]["cases"]==cat["challenger"]["cases"]==4
        assert amount(cat["mean_variable_cost_savings"]["lower_microunits"])==40
    req=decision(result)
    receipt=decide(*monitor,pin(early,first),req)
    assert not receipt["deployment_changed"] and not receipt["execution_authorized"]
    bundle={"initial":initial,"monitor_plan":plan,"snapshot":final,"previous_checkpoint":pin(early,first)}
    assert operation(packet,"router-monitor-review",bundle)==result
    assert operation(packet,"router-monitor-decide",{**bundle,"request":req})==receipt


def test_overdue_and_late_record_visible(monitor):
    packet,initial,plan,final=monitor
    prior=deepcopy(final);prior["as_of_ms"]=3200;prior["windows"].pop()
    prior["windows"][0]["observations"].pop()
    r=review(packet,initial,plan,prior,None)
    assert r["windows"][0]["state"]=="overdue"
    assert r["windows"][1]["state"]=="open"
    assert r["recorded_cases"]==11
    result=review(*monitor,pin(r,prior))
    assert result["overdue_windows_at_previous_checkpoint"]==["w1"]
    assert result["completed_passing_windows"]==2


def test_earlier_bad_window_not_hidden_by_later_passing_window(monitor):
    packet,initial,plan,final=monitor
    slots={s["slot_id"]:s for w in plan["windows"] for s in w["plan"]["slots"]}
    row=next(o for o in final["windows"][0]["observations"] if slots[o["slot_id"]]["arm"]=="challenger")
    row["business_outcome"]="failure"
    result=review(*monitor,None)
    assert result["windows"][0]["state"]=="rollback_review"
    assert result["windows"][1]["state"]=="complete_pass"
    assert result["recommended_review"]=="rollback_review"
    assert any(t["window_id"]=="w1" for t in result["rollback_review_triggers"])
    with pytest.raises(ValueError):decide(*monitor,None,decision(result))
    assert not decide(*monitor,None,decision(result,"refer_for_rollback_review"))["deployment_changed"]


@pytest.mark.parametrize("mutation",["remove","rewrite","rewind","pin","reuse_source","initial_source","future","unknown_window"])
def test_snapshot_integrity_refusals(monitor,mutation):
    packet,initial,plan,final=monitor
    previous=deepcopy(final);previous["as_of_ms"]=2999;previous["windows"].pop()
    r=review(packet,initial,plan,previous,None)
    retained=pin(r,previous)
    if mutation=="remove":final["windows"][0]["observations"].pop()
    elif mutation=="rewrite":final["windows"][0]["observations"][0]["critical_incident"]=True
    elif mutation=="rewind":final["as_of_ms"]=2999;final["windows"].pop()
    elif mutation=="pin":retained["expected_snapshot_sha256"]="f"*64
    elif mutation=="reuse_source":final["windows"][1]["observations"][0]["source_evidence_sha256"]=final["windows"][0]["observations"][0]["source_evidence_sha256"]
    elif mutation=="initial_source":final["windows"][0]["observations"][0]["source_evidence_sha256"]=initial["captures"]["observations"][0]["source_evidence_sha256"]
    elif mutation=="future":final["as_of_ms"]=2999
    else:final["windows"][0]["window_id"]="unknown"
    with pytest.raises(ValueError):review(*monitor,retained)


@pytest.mark.parametrize("mutation",["overlap","gap","duplicate_case","initial_case","stale_receipt"])
def test_freeze_refuses_invalid_windows(monitor,mutation):
    packet,initial,plan,final=monitor
    req=deepcopy(plan["request"])
    if mutation=="overlap":req["windows"][1]["start_ms"]-=1
    elif mutation=="gap":req["windows"][1]["start_ms"]+=1
    elif mutation=="duplicate_case":req["windows"][1]["cases"][0]=deepcopy(req["windows"][0]["cases"][0])
    elif mutation=="initial_case":req["windows"][0]["cases"][0]=deepcopy(initial["pilot_plan"]["request"]["cases"][0])
    else:initial["receipt"]["receipt_sha256"]="f"*64
    with pytest.raises(ValueError):freeze(packet,initial,req)


def test_snapshot_order_canonical_and_stale_decision_blocked(monitor):
    packet,initial,plan,final=monitor
    r=review(*monitor,None)
    final["windows"].reverse()
    for w in final["windows"]:w["observations"].reverse()
    assert review(*monitor,None)==r
    req=decision(r);req["expected_report_sha256"]="f"*64
    with pytest.raises(ValueError):decide(*monitor,None,req)
