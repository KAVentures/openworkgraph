"""Controlled pilot evidence against a real frozen change-referral fixture."""
from copy import deepcopy
from fractions import Fraction

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_router_change import assess, decide as change_decide
from shared.agent_procurement_router_pilot import freeze, review, decide, operation, LEDGER
from test_agent_procurement_router_change import version_change, decision, rehearsal, trial, study
from test_agent_procurement_operating_cost import amount


@pytest.fixture
def pilot(version_change):
    packet,d,h,p,c=version_change
    result=assess(*version_change)
    req=decision(result)
    upstream={"discovery":d,"holdout":h,"change_plan":p,"captures":c,"request":req,
              "receipt":change_decide(*version_change,req)}
    request={"pilot_id":"pilot-1","coordinator_alias":"coordinator","window_start_ms":1000,
             "window_end_exclusive_ms":2000,"allocation_seed_sha256":digest("seed"),
             "cases":[{"case_token_sha256":digest([cat,i]),"reviewed_category":cat}
                      for cat in ("normal","exception","missing_facts") for i in range(4)],
             "policy":{"minimum_cases_per_arm_per_category":2},
             "rollback_target_configuration_sha256":p["request"]["incumbent_configuration_sha256"],
             "external_controlled_pilot_approval_sha256":digest("external approval"),
             "confirm_unique_cases_no_duplicate_execution":True,"confirm_non_overlapping_cost_measurements":True,
             "confirmed":True}
    plan=freeze(packet,upstream,request)
    captures={"format":LEDGER,"pilot_plan_sha256":plan["pilot_plan_sha256"],"as_of_ms":2000,"observations":[]}
    for s in plan["slots"]:
        cid=s["intended_candidate_id"]
        captures["observations"].append({"slot_id":s["slot_id"],"case_token_sha256":s["case_token_sha256"],
            "actual_router_configuration_sha256":s["expected_router_configuration_sha256"],
            "actual_destination":"agent" if cid else "manual","actual_candidate_id":cid,
            "actual_candidate_configuration_sha256":plan["configured_candidates"].get(cid),
            "observed_at_ms":1500,"status":"completed","business_outcome":"success",
            "critical_incident":False,"forbidden_action":False,"used_fallback":False,
            "direct_cost_microunits":100 if s["arm"]=="incumbent" else 60,"human_person_ms":36000,
            "elapsed_ms":500,"source_evidence_sha256":digest(["source",s["slot_id"]])})
    return packet,upstream,plan,captures


def receipt_request(result,disposition="refer_for_expanded_pilot_review"):
    return {"disposition":disposition,"reviewer_alias":"reviewer","confirmed":True,
            "expected_pilot_plan_sha256":result["pilot_plan_sha256"],"expected_report_sha256":result["report_sha256"]}


def test_balanced_deterministic_pilot_and_positive_referral(pilot):
    packet,u,p,c=pilot
    assert freeze(packet,u,p["request"])==p
    assert len({x["case_token_sha256"] for x in p["slots"]})==12
    result=review(*pilot)
    assert result["recommended_review"]=="expanded_pilot_review"
    for group in result["category_reviews"]:
        assert group["incumbent"]["cases"]==group["challenger"]["cases"]==2
        assert amount(group["mean_variable_cost_savings"]["lower_microunits"])==40
        assert group["status"]=="pass"
    req=receipt_request(result)
    r=decide(*pilot,req)
    assert not r["deployment_changed"] and not r["execution_authorized"]
    bundle={"upstream":u,"pilot_plan":p,"captures":c}
    assert operation(packet,"router-pilot-review",bundle)==result
    assert operation(packet,"router-pilot-decide",{**bundle,"request":req})==r


def test_open_window_or_missing_observation_holds(pilot):
    packet,u,p,c=pilot
    c["as_of_ms"]=1600
    assert review(*pilot)["recommended_review"]=="hold"
    c["as_of_ms"]=2000
    c["observations"].pop()
    r=review(*pilot)
    assert not r["eligible_for_expanded_pilot_review"]
    assert r["checks"]["all_cases_terminal"]=="unknown"
    assert r["recorded_cases"]==11 and r["scheduled_cases"]==12
    with pytest.raises(ValueError):decide(*pilot,receipt_request(r))


@pytest.mark.parametrize("event",["critical","forbidden","wrong_version","wrong_agent","manual_leakage","route_deviation"])
def test_known_adverse_signal_triggers_rollback_before_window_end(pilot,event):
    packet,u,p,c=pilot
    c["as_of_ms"]=1600
    by_slot={s["slot_id"]:s for s in p["slots"]}
    if event=="manual_leakage":
        row=next(x for x in c["observations"] if x["actual_destination"]=="manual")
        cid=next(iter(p["configured_candidates"]))
        row.update(actual_destination="agent",actual_candidate_id=cid,
                   actual_candidate_configuration_sha256=p["configured_candidates"][cid])
    else:
        row=next(x for x in c["observations"] if x["actual_destination"]=="agent")
        if event=="critical":row["critical_incident"]=True
        elif event=="forbidden":row["forbidden_action"]=True
        elif event=="wrong_version":row["actual_router_configuration_sha256"]="f"*64
        elif event=="wrong_agent":row["actual_candidate_configuration_sha256"]="f"*64
        else:row.update(actual_destination="manual",actual_candidate_id=None,actual_candidate_configuration_sha256=None)
    r=review(*pilot)
    assert r["recommended_review"]=="rollback_review" and r["rollback_review_triggers"]
    assert not r["eligible_for_expanded_pilot_review"]
    assert not decide(*pilot,receipt_request(r,"refer_for_rollback_review"))["deployment_changed"]


def test_unknown_version_cannot_establish_success_or_complete_cost(pilot):
    _,_,_,c=pilot
    c["observations"][0]["actual_router_configuration_sha256"]=None
    r=review(*pilot)
    row=next(x for x in r["rows"] if x["slot_id"]==c["observations"][0]["slot_id"])
    assert row["success"]=={"lower":0,"upper":1}
    assert row["variable_cost"]["upper_microunits"] is None
    assert r["checks"]["all_version_attribution"]=="unknown"


@pytest.mark.parametrize("kind",["wrong_pin","duplicate","wrong_case","future","outside","duplicate_source","bad_outcome"])
def test_invalid_capture_refused(pilot,kind):
    _,_,_,c=pilot
    if kind=="wrong_pin":c["pilot_plan_sha256"]="f"*64
    elif kind=="duplicate":c["observations"].append(deepcopy(c["observations"][0]))
    elif kind=="wrong_case":c["observations"][0]["case_token_sha256"]="f"*64
    elif kind=="future":c["as_of_ms"]=1400
    elif kind=="outside":c["observations"][0]["observed_at_ms"]=2000
    elif kind=="duplicate_source":c["observations"][0]["source_evidence_sha256"]=c["observations"][1]["source_evidence_sha256"]
    elif kind=="bad_outcome":c["observations"][0]["status"]="failed"
    with pytest.raises(ValueError):review(*pilot)


def test_wrong_rollback_target_duplicate_case_or_stale_receipt_refused(pilot):
    packet,u,p,c=pilot
    for mutation in ("target","duplicate","receipt"):
        req=deepcopy(p["request"]);up=deepcopy(u)
        if mutation=="target":req["rollback_target_configuration_sha256"]="f"*64
        elif mutation=="duplicate":req["cases"].append(deepcopy(req["cases"][0]))
        else:up["receipt"]["receipt_sha256"]="f"*64
        with pytest.raises(ValueError):freeze(packet,up,req)


def test_decision_pins_and_reviewer_separation(pilot):
    result=review(*pilot)
    req=receipt_request(result);req["reviewer_alias"]="COORDINATOR"
    with pytest.raises(ValueError):decide(*pilot,req)
    req=receipt_request(result);req["expected_report_sha256"]="f"*64
    with pytest.raises(ValueError):decide(*pilot,req)
