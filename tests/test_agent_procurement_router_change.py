"""Paired version change control, using the existing frozen router fixture."""
from copy import deepcopy

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_router_change import freeze, assess, decide, operation
from test_agent_procurement_router_rehearsal import rehearsal, trial, study
from test_agent_procurement_operating_cost import amount


@pytest.fixture
def version_change(rehearsal):
    packet,d,h,rp,c=rehearsal
    req={"incumbent_configuration_sha256":rp["request"]["router_configuration_sha256"],
         "challenger_configuration_sha256":digest("router-v2"),"portfolio_id":"mixed",
         "router_policy":rp["request"]["policy"],"comparison_policy":{},
         "nominator_alias":"operator-a","incremental_annual_switch_cost_microunits":10,
         "incremental_one_time_switch_cost_microunits":20,
         "confirm_manual_fallback_on_known_router_failure":True,
         "confirm_non_overlapping_router_measurements":True,
         "confirm_non_overlapping_incremental_switch_cost":True,"confirmed":True}
    plan=freeze(packet,d,h["plan"],req)
    assert plan["incumbent_plan"]==rp
    candidate=deepcopy(c)
    candidate["router_plan_sha256"]=plan["challenger_plan"]["router_plan_sha256"]
    candidate["router_configuration_sha256"]=req["challenger_configuration_sha256"]
    for row,slot in zip(candidate["predictions"],plan["challenger_plan"]["slots"]):
        row.update(slot,direct_cost_microunits=0,human_person_ms=0)
    return packet,d,h,plan,{"incumbent":c,"challenger":candidate}


def decision(result,disposition="refer_for_controlled_change_review"):
    return {"disposition":disposition,"reviewer_alias":"reviewer-b","confirmed":True,
            "expected_plan_sha256":result["change_plan_sha256"],"expected_report_sha256":result["report_sha256"]}


def test_paired_savings_and_receipt(version_change):
    packet,d,h,p,c=version_change
    result=assess(*version_change)
    assert result["eligible_for_controlled_change_review"]
    assert result["observed_counts"]["changed_destination"]=={"lower":0,"upper":0}
    assert result["observed_counts"]["success_change"]=={"lower":0,"upper":0}
    assert amount(result["annual_paired_variable_savings"]["lower_microunits"])==120
    assert amount(result["annual_recurring_switch_savings"]["upper_microunits"])==110
    assert amount(result["first_year_switch_savings"]["lower_microunits"])==90
    assert amount(result["annual_paired_released_person_ms"]["lower"])==2160000
    receipt=decide(*version_change,decision(result))
    assert receipt["referred_configuration_sha256"]==p["request"]["challenger_configuration_sha256"]
    assert not receipt["deployment_changed"] and not receipt["execution_authorized"]
    bundle={"discovery":d,"holdout":h,"change_plan":p,"captures":c}
    assert result==operation(packet,"router-change-assess",bundle)
    assert receipt==operation(packet,"router-change-decide",{**bundle,"request":decision(result)})


def test_same_unknown_arm_cancels_but_absolute_gates_remain(version_change):
    packet,d,h,p,c=version_change
    h["request"]["interventions"]=[]
    h["request"]["manual"]=[]
    result=assess(*version_change)
    for k in ("changed_destination","success_change","new_outcome_regression","new_critical","new_non_delivery"):
        assert result["observed_counts"][k]=={"lower":0,"upper":0}
    assert amount(result["first_year_switch_savings"]["lower_microunits"])==90
    assert not result["eligible_for_controlled_change_review"]
    assert result["checks"]["challenger_absolute_router_gates"]!="pass"


def test_cheaper_wrong_route_cannot_hide_regression(version_change):
    packet,d,h,p,c=version_change
    row=next(x for x in c["challenger"]["predictions"] if x["predicted_category"]=="missing_facts")
    row["predicted_category"]="normal"
    slot=next(s for s in h["plan"]["slots"] if s["candidate_id"]=="cheap" and s["case_id"]==row["case_id"] and s["repeat"]==row["repeat"])
    next(x for x in h["request"]["interventions"] if x["slot_id"]==slot["slot_id"])["observed_business_outcome"]="failure"
    result=assess(*version_change)
    assert result["observed_counts"]["new_manual_leakage"]=={"lower":1,"upper":1}
    assert result["observed_counts"]["new_outcome_regression"]=={"lower":1,"upper":1}
    assert result["checks"]["new_manual_leakage"]=="fail"
    assert result["checks"]["new_outcome_regression"]=="fail"
    assert amount(result["first_year_switch_savings"]["lower_microunits"])>0
    with pytest.raises(ValueError,match="frozen checks"):decide(*version_change,decision(result))
    retained=decide(*version_change,decision(result,"retain_incumbent"))
    assert retained["referred_configuration_sha256"] is None


def test_missing_prediction_cannot_become_success_or_free(version_change):
    packet,d,h,p,c=version_change
    c["challenger"]["predictions"].pop()
    result=assess(*version_change)
    assert result["observed_counts"]["changed_destination"]=={"lower":0,"upper":1}
    assert result["checks"]["both_router_capture_coverage"]=="unknown"
    assert result["first_year_switch_savings"]["lower_microunits"] is None
    assert not result["eligible_for_controlled_change_review"]


def test_separate_router_measurement_unknown_even_on_same_path(version_change):
    packet,d,h,p,c=version_change
    c["challenger"]["predictions"][0]["direct_cost_microunits"]=None
    result=assess(*version_change)
    assert result["observed_counts"]["changed_destination"]["upper"]==0
    assert result["first_year_switch_savings"]["lower_microunits"] is None
    assert result["checks"]["first_year_savings"]=="unknown"


def test_switch_cost_can_block_an_otherwise_good_router(version_change):
    packet,d,h,p,c=version_change
    req=deepcopy(p["request"]);req["incremental_one_time_switch_cost_microunits"]=1000
    p=freeze(packet,d,h["plan"],req)
    result=assess(packet,d,h,p,c)
    assert result["checks"]["first_year_savings"]=="fail"
    assert not result["eligible_for_controlled_change_review"]


@pytest.mark.parametrize("kind",["same_config","plan","swapped_ledgers","wrong_slot","changed_discovery","stale_report","same_reviewer","no_confirmation"])
def test_invalid_or_stale_change_refused(version_change,kind):
    packet,d,h,p,c=version_change
    if kind=="same_config":
        req=deepcopy(p["request"]);req["challenger_configuration_sha256"]=req["incumbent_configuration_sha256"]
        with pytest.raises(ValueError):freeze(packet,d,h["plan"],req)
        return
    if kind in ("stale_report","same_reviewer","no_confirmation"):
        req=decision(assess(*version_change))
        if kind=="stale_report":req["expected_report_sha256"]="f"*64
        elif kind=="same_reviewer":req["reviewer_alias"]="OPERATOR-A"
        else:req["confirmed"]=False
        with pytest.raises(ValueError):decide(*version_change,req)
        return
    if kind=="plan":p["paired_attempts"]+=1
    elif kind=="swapped_ledgers":c["incumbent"],c["challenger"]=c["challenger"],c["incumbent"]
    elif kind=="wrong_slot":c["challenger"]["predictions"][0]["router_slot_id"]="f"*64
    elif kind=="changed_discovery":d["request"]["manual"][0]["routine_ms"]+=1
    with pytest.raises(ValueError):assess(*version_change)
