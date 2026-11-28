"""Frozen router rehearsal using actual procurement and generated-suite fixtures."""
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_trials import freeze as trial_freeze, review as trial_review
from shared.agent_procurement_router_rehearsal import freeze, review, challenge, operation, CAPTURES
from shared.agent_procurement_replay import prepare
from test_agent_procurement_operating_cost import baseline, _portfolio_request, amount
from test_agent_procurement_trials import trial, capture, ledger, study
from test_agent_procurement_replay import inputs


@pytest.fixture
def rehearsal(trial):
    packet,dp,dc,dreq=baseline(trial)
    _portfolio_request(dreq)
    _,original,design,_=trial
    held=deepcopy(original)
    for c in held["candidates"]:
        c["report"]["suite_sha256"]="b"*64
        for row in c["report"]["cases"]:
            row["case_sha256"]=digest(["router-holdout",row["case_id"]])
    hd=deepcopy(design);hd["reference_report"]=held["candidates"][0]["report"]
    hp=trial_freeze(packet,hd)
    hc=ledger(hp,[capture(held,cid,r) for cid in ("baseline","cheap") for r in (1,2)])
    hreq=deepcopy(dreq)
    hreq.update(plan_sha256=hp["plan_sha256"],ledger_sha256=trial_review(packet,hp,hc)["ledger_sha256"])
    discovery={"plan":dp,"captures":dc,"request":dreq}
    holdout={"plan":hp,"captures":hc,"request":hreq}
    request={"portfolio_id":"mixed","router_configuration_sha256":digest("router-v1"),
             "policy":{"minimum_distinct_cases_per_positive_category":1},
             "confirm_manual_fallback_on_known_router_failure":True,
             "confirm_non_overlapping_router_measurements":True,"confirmed":True}
    rp=freeze(packet,discovery,hp,request)
    categories={r["case_id"]:r["category"] for r in hp["basis"]["reference_report"]["cases"]}
    predictions=[{**slot,"status":"classified","predicted_category":categories[slot["case_id"]],
                  "direct_cost_microunits":1,"human_person_ms":36000,"elapsed_ms":500} for slot in rp["slots"]]
    captures={"format":CAPTURES,"router_plan_sha256":rp["router_plan_sha256"],
              "router_configuration_sha256":request["router_configuration_sha256"],"predictions":predictions}
    return packet,discovery,holdout,rp,captures


def test_perfect_router_reconciles_cost_and_capacity(rehearsal):
    packet,d,h,rp,c=rehearsal
    result=review(*rehearsal)
    assert result["gate_status"]=="pass"
    assert result["observed_counts"]["accuracy"]=={"lower":6,"upper":6}
    assert result["observed_counts"]["ai_assigned"]=={"lower":4,"upper":4}
    assert result["observed_counts"]["manual_leakage"]=={"lower":0,"upper":0}
    assert amount(result["annual_cost"]["lower_microunits"])==4930
    assert amount(result["recurring_savings"]["lower_microunits"])==1170
    assert amount(result["first_year_savings"]["upper_microunits"])==1050
    assert amount(result["annual_released_person_ms"]["lower"])==69840000
    assert result["configured_candidate_ids"]==["baseline","cheap"]
    assert amount(result["annual_cost_difference_from_ideal_path"]["lower_microunits"])==0
    assert amount(result["annual_cost_difference_from_ideal_including_router"]["upper_microunits"])==120
    assert result["confusion_counts"]["normal"]["normal"]==2
    assert result["router_latency"]["p95_upper_ms"]==500
    assert result["manual_fixed_cost_retained"]
    assert not result["actual_routed_execution_observed"] and not result["execution_authorized"]
    assert result==operation(packet,"router-review",{"discovery":d,"holdout":h,"router_plan":rp,"captures":c})


def test_manual_leakage_and_exception_regression_visible(rehearsal):
    packet,d,h,rp,c=rehearsal
    target=next(p for p in c["predictions"] if p["predicted_category"]=="missing_facts")
    target["predicted_category"]="normal"
    result=review(packet,d,h,rp,c)
    assert result["observed_counts"]["manual_leakage"]=={"lower":1,"upper":1}
    assert result["checks"]["manual_leakage"]=="fail"
    assert result["gate_status"]=="fail"
    target_row=next(r for r in result["rows"] if r["router_slot_id"]==target["router_slot_id"])
    assert target_row["possible_candidate_ids"]==["cheap"] and not target_row["manual_path_possible"]
    # Business outcome regression belongs to the actual alternative, not the
    # originally intended destination or the router's claimed score.
    slot=next(s for s in h["plan"]["slots"] if s["candidate_id"]=="cheap" and s["case_id"]==target["case_id"] and s["repeat"]==target["repeat"])
    next(x for x in h["request"]["interventions"] if x["slot_id"]==slot["slot_id"])["observed_business_outcome"]="failure"
    result=review(packet,d,h,rp,c)
    assert result["observed_counts"]["regression"]["lower"]==1


def test_router_failure_and_missing_capture_not_conflated(rehearsal):
    packet,d,h,rp,c=rehearsal
    p=c["predictions"][0];p.update(status="timeout",predicted_category=None)
    removed=c["predictions"].pop(1)
    result=review(packet,d,h,rp,c)
    timeout=next(r for r in result["rows"] if r["router_slot_id"]==p["router_slot_id"])
    missing=next(r for r in result["rows"] if r["router_slot_id"]==removed["router_slot_id"])
    assert timeout["manual_path_possible"] and timeout["possible_candidate_ids"]==[]
    assert amount(timeout["paired_savings"]["lower_microunits"])==-2
    assert timeout["fallback"]=={"lower":1,"upper":1}
    assert amount(timeout["cost_difference_from_ideal_path"]["lower_microunits"])==45
    assert result["confusion_counts"]["normal"]["timeout"]==1
    assert result["confusion_counts"]["normal"]["not_recorded"]==1
    assert missing["route_unresolved"] and missing["manual_path_possible"]
    assert missing["possible_candidate_ids"]==["baseline","cheap"]
    assert missing["fallback"]=={"lower":0,"upper":1}
    assert missing["router_cost"]["upper_microunits"] is None
    assert result["recorded_attempts"]==5 and result["scheduled_attempts"]==6
    assert result["checks"]["router_capture_coverage"]=="unknown"
    assert not result["financial_evidence_complete"]


def test_missing_router_measurement_cannot_establish_positive_savings(rehearsal):
    packet,d,h,rp,c=rehearsal
    c["predictions"][0]["direct_cost_microunits"]=None
    result=review(packet,d,h,rp,c)
    assert result["first_year_savings"]["lower_microunits"] is None
    assert result["annual_cost"]["upper_microunits"] is None
    assert result["checks"]["complete_financial_evidence"]=="unknown"
    assert result["gate_status"]=="unknown"


@pytest.mark.parametrize("kind",["plan","config","duplicate","unknown_slot","bad_status","wrong_category",
                                  "changed_discovery","changed_rates","changed_scenario","withdrawn_category"])
def test_router_rehearsal_refuses_unbound_or_changed_inputs(rehearsal,kind):
    packet,d,h,rp,c=rehearsal
    if kind=="plan":c["router_plan_sha256"]="f"*64
    elif kind=="config":c["router_configuration_sha256"]="f"*64
    elif kind=="duplicate":c["predictions"].append(deepcopy(c["predictions"][0]))
    elif kind=="unknown_slot":c["predictions"][0]["router_slot_id"]="f"*64
    elif kind=="bad_status":c["predictions"][0]["status"]="made_up"
    elif kind=="wrong_category":c["predictions"][0]["predicted_category"]="made_up"
    elif kind=="changed_discovery":d["request"]["manual"][0]["routine_ms"]+=1
    elif kind=="changed_rates":h["request"]["rates"]["routine_hourly_microunits"]+=1
    elif kind=="changed_scenario":h["request"]["scenario"]["annual_cases"]+=1
    elif kind=="withdrawn_category":c["predictions"][0]["status"]="withdrawn"
    with pytest.raises(ValueError):review(packet,d,h,rp,c)


def test_freeze_requires_unique_disjoint_declared_inputs_and_coverage(rehearsal):
    packet,d,h,rp,c=rehearsal
    with pytest.raises(ValueError):freeze(packet,d,d["plan"],rp["request"])
    req=deepcopy(rp["request"]);req["policy"]["minimum_distinct_cases_per_positive_category"]=2
    with pytest.raises(ValueError,match="coverage"):freeze(packet,d,h["plan"],req)
    req=deepcopy(rp["request"]);req["confirmed"]=False
    with pytest.raises(ValueError,match="confirmation"):freeze(packet,d,h["plan"],req)


def test_actual_generated_input_only_router_challenge(inputs):
    packet,suite,design=inputs
    dp,_=prepare(packet,suite,design,confirm_export_inputs=True)
    held=deepcopy(suite)
    held["cases"][0]["input"]={"text":"PRIVATE_HOLDOUT_INPUT"}
    # Distinct second input without changing reviewed expected status.
    held["cases"][1]["input"]={"text":""}
    hp,_=prepare(packet,held,design,confirm_export_inputs=True)
    dc=ledger(dp,[])
    req={"format":"openworkgraph.procurement-operating-cost.v1","plan_sha256":dp["plan_sha256"],
         "ledger_sha256":trial_review(packet,dp,dc)["ledger_sha256"],"currency":"USD",
         "rates":{k+"_hourly_microunits":0 for k in ("routine","review","rework","fallback","escalation")},
         "manual":[],"interventions":[],"confirm_non_overlapping_cost_boundary":True,
         "scenario":{"annual_cases":20,"manual_annual_microunits":0,
                     "candidate_fixed_costs":[{"candidate_id":cid,"annual_microunits":0,"one_time_microunits":0} for cid in ("current","other")],
                     "portfolio":{"category_volumes":[{"category":cat,"annual_cases":10 if cat!="exception" else 0}
                                                       for cat in ("normal","exception","missing_facts")],
                         "portfolios":[{"portfolio_id":"test","assignments":[{"category":cat,"candidate_id":"other" if cat=="normal" else None}
                                                                            for cat in ("normal","exception","missing_facts")],
                             "routing_annual_microunits":0,"routing_one_time_microunits":0,
                             "routing_cost_per_ai_case_microunits":0,"routing_human_ms_per_ai_case":0}],
                         "confirm_conditional_category_assignment":True,"confirm_non_overlapping_routing_costs":True}}}
    discovery={"plan":dp,"captures":dc,"request":req}
    request={"portfolio_id":"test","router_configuration_sha256":digest("router"),
             "policy":{"minimum_distinct_cases_per_positive_category":1,"minimum_repeats":1},
             "confirm_manual_fallback_on_known_router_failure":True,
             "confirm_non_overlapping_router_measurements":True,"confirmed":True}
    rp=freeze(packet,discovery,hp,request)
    with pytest.raises(ValueError,match="confirmation"):challenge(packet,discovery,hp,rp,held)
    ch=challenge(packet,discovery,hp,rp,held,confirm_export_inputs=True)
    assert ch["cases"][0]["input"]["text"]=="PRIVATE_HOLDOUT_INPUT"
    assert set(ch["cases"][0])=={"case_id","input","input_sha256"}
    assert "assignments" not in ch and "policy" not in ch and "basis" not in ch
    assert "accepted_routes" not in json.dumps(ch)
    assert not ch["expected_case_answers_included"] and not ch["execution_authorized"]
    assert "PRIVATE_HOLDOUT_INPUT" not in json.dumps(rp)
    wrong=deepcopy(held);wrong["cases"][0]["input"]["text"]="different"
    with pytest.raises(ValueError):challenge(packet,discovery,hp,rp,wrong,confirm_export_inputs=True)


def test_latency_gate_and_unknown_identical_path_cost_cancellation(rehearsal):
    packet,d,h,rp,c=rehearsal
    req=deepcopy(rp["request"]);req["policy"]["maximum_router_p95_elapsed_ms"]=400
    rp=freeze(packet,d,h["plan"],req);c["router_plan_sha256"]=rp["router_plan_sha256"]
    result=review(packet,d,h,rp,c)
    assert result["checks"]["router_p95_latency"]=="fail"
    # Same intended and predicted destination, but missing intervention cost.
    h["request"]["interventions"]=[]
    result=review(packet,d,h,rp,c)
    assert amount(result["annual_cost_difference_from_ideal_path"]["lower_microunits"])==0
    assert amount(result["annual_cost_difference_from_ideal_path"]["upper_microunits"])==0
    assert result["annual_cost"]["upper_microunits"] is None
