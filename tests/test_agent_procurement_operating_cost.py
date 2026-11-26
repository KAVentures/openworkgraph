"""R26 operating-cost review against the actual frozen trial fixture."""
from copy import deepcopy
from fractions import Fraction

import pytest

from shared.agent_procurement_operating_cost import assess, operation
from shared.agent_procurement_trials import review
from test_agent_procurement_trials import trial, capture, ledger, study


def amount(value):
    return None if value is None else Fraction(int(value["numerator"]),int(value["denominator"]))


def baseline(trial):
    packet,req,_,plan=trial
    captures=ledger(plan,[capture(req,cid,r) for cid in ("baseline","cheap") for r in (1,2)])
    reviewed=review(packet,plan,captures)
    times=dict(routine_ms=3600000,review_ms=0,rework_ms=0,fallback_ms=0,escalation_ms=0,
               other_direct_cost_microunits=0,observed_business_outcome="success")
    manual=[dict(case_id=case,repeat=r,**times) for case in ("case_1","case_2","case_3") for r in (1,2)]
    interventions=[dict(slot_id=s["slot_id"],**{**times,"routine_ms":0,"review_ms":1800000})
                   for s in plan["slots"]]
    request={"format":"openworkgraph.procurement-operating-cost.v1",
             "plan_sha256":reviewed["plan_sha256"],"ledger_sha256":reviewed["ledger_sha256"],"currency":"USD",
             "rates":{k+"_hourly_microunits":100 for k in ("routine","review","rework","fallback","escalation")},
             "manual":manual,"interventions":interventions,"confirm_non_overlapping_cost_boundary":True,
             "scenario":{"annual_cases":60,"manual_annual_microunits":100,
                         "candidate_fixed_costs":[{"candidate_id":cid,"annual_microunits":200,"one_time_microunits":50}
                                                  for cid in ("baseline","cheap")]}}
    return packet,plan,captures,request


def candidate(report,cid="cheap"):
    return next(x for x in report["candidates"] if x["candidate_id"]==cid)


def test_full_cost_includes_human_and_provider_and_fixed(trial):
    packet,plan,captures,req=baseline(trial)
    result=assess(packet,plan,captures,req)
    cheap=candidate(result)
    assert amount(result["manual_baseline"]["total_cost"]["lower_microunits"])==600
    assert amount(cheap["candidate"]["total_cost"]["upper_microunits"])==330
    assert amount(cheap["paired_trial_savings"]["lower_microunits"])==270
    assert amount(cheap["annual_recurring_savings_scenario"]["lower_microunits"])==2600
    assert amount(cheap["first_year_savings_scenario"]["upper_microunits"])==2550
    assert cheap["candidate"]["human_touched_attempts"]=={"lower":6,"upper":6}
    assert cheap["candidate"]["declared_business_outcomes"]["declared_success"]==6
    assert len(cheap["rows"])==6 and not result["execution_authorized"]
    assert not result["realized_savings_verified"]
    assert result==operation(packet,{"plan":plan,"captures":captures,"request":req})


def test_missing_intervention_and_manual_do_not_become_free(trial):
    packet,plan,captures,req=baseline(trial)
    slot=next(s for s in plan["slots"] if s["candidate_id"]=="cheap")
    req["interventions"]=[x for x in req["interventions"] if x["slot_id"]!=slot["slot_id"]]
    req["manual"].pop()
    result=assess(packet,plan,captures,req)
    cheap=candidate(result)
    assert cheap["candidate"]["complete_cost_attempts"]==5
    assert cheap["candidate"]["total_cost"]["upper_microunits"] is None
    assert cheap["candidate"]["human_touched_attempts"]=={"lower":5,"upper":6}
    assert cheap["paired_trial_savings"]=={"lower_microunits":None,"upper_microunits":None}
    assert cheap["positive_first_year_lower_bound"] is None
    assert result["manual_baseline"]["attempts"]==6


def test_failure_and_withdrawal_cost_stay_in_population(trial):
    packet,plan,_,req=baseline(trial)
    _,original,_,_=trial
    captures=ledger(plan,[capture(original,"cheap",1,{"case_1":"timeout","case_2":"withdrawn"})])
    req["ledger_sha256"]=review(packet,plan,captures)["ledger_sha256"]
    result=assess(packet,plan,captures,req)
    cheap=candidate(result)
    assert cheap["candidate"]["attempts"]==6
    timeout=next(x for x in cheap["rows"] if x["trial_status"]=="timeout")
    withdrawn=next(x for x in cheap["rows"] if x["trial_status"]=="withdrawn")
    assert timeout["candidate_missing"]==[]
    assert "provider_runtime_cost_microunits" in withdrawn["candidate_missing"]
    assert cheap["candidate"]["provider_runtime_cost_lower_microunits"]==sum(
        a["cost_microunits"] for a in captures["captures"][0]["attempts"]
        if a["cost_microunits"] is not None)
    assert cheap["candidate"]["total_cost"]["upper_microunits"] is None
    assert cheap["trial_gate_status"]!="pass"


def test_fractional_microunits_and_one_sided_bounds(trial):
    packet,plan,captures,req=baseline(trial)
    for x in req["manual"]:x["routine_ms"]=1
    result=assess(packet,plan,captures,req)
    assert amount(result["manual_baseline"]["total_cost"]["lower_microunits"])==Fraction(1,6000)
    req["manual"][0]["routine_ms"]=None
    cheap=candidate(assess(packet,plan,captures,req))
    assert cheap["paired_trial_savings"]["lower_microunits"] is not None
    assert cheap["paired_trial_savings"]["upper_microunits"] is None


@pytest.mark.parametrize("kind",["plan","ledger","currency","duplicate_manual","duplicate_slot","unknown_slot",
                                  "unknown_manual","fixed_missing","fixed_duplicate","negative","bool",
                                  "confirmation","extra_field"])
def test_reject_incompatible_inputs(trial,kind):
    packet,plan,captures,req=baseline(trial)
    if kind=="plan":req["plan_sha256"]="f"*64
    elif kind=="ledger":req["ledger_sha256"]="f"*64
    elif kind=="currency":req["currency"]="EUR"
    elif kind=="duplicate_manual":req["manual"].append(deepcopy(req["manual"][0]))
    elif kind=="duplicate_slot":req["interventions"].append(deepcopy(req["interventions"][0]))
    elif kind=="unknown_slot":req["interventions"][0]["slot_id"]="f"*64
    elif kind=="unknown_manual":req["manual"][0]["case_id"]="case_999"
    elif kind=="fixed_missing":req["scenario"]["candidate_fixed_costs"].pop()
    elif kind=="fixed_duplicate":req["scenario"]["candidate_fixed_costs"].append(deepcopy(req["scenario"]["candidate_fixed_costs"][0]))
    elif kind=="negative":req["manual"][0]["routine_ms"]=-1
    elif kind=="bool":req["manual"][0]["routine_ms"]=True
    elif kind=="confirmation":req["confirm_non_overlapping_cost_boundary"]=False
    elif kind=="extra_field":req["manual"][0]["untrusted_text"]="not accepted"
    with pytest.raises(ValueError):assess(packet,plan,captures,req)


def test_changed_capture_requires_new_pin(trial):
    packet,plan,captures,req=baseline(trial)
    changed=deepcopy(captures)
    changed["captures"][0]["attempts"][0]["cost_microunits"]+=1
    with pytest.raises(ValueError,match="pin"):assess(packet,plan,changed,req)


def test_exact_money_encoding_survives_browser_json_integer_limits(trial):
    packet,plan,captures,req=baseline(trial)
    req["scenario"]["manual_annual_microunits"]=9007199254740993
    result=assess(packet,plan,captures,req)
    assert result["scenario"]["manual_annual_microunits"]=="9007199254740993"
    value=candidate(result)["annual_recurring_savings_scenario"]["lower_microunits"]
    assert isinstance(value["numerator"],str) and isinstance(value["denominator"],str)
    assert amount(value)==9007199254743493


def test_mixed_adoption_charges_full_incremental_cost_and_preserves_manual_fixed(trial):
    packet,plan,captures,req=baseline(trial)
    req["scenario"]["adoption"]={"adoption_case_counts":[30,60],"avoidable_manual_annual_microunits":100}
    cheap=candidate(assess(packet,plan,captures,req))
    a=cheap["conditional_adoption"];rows={x["adopted_cases"]:x for x in a["rows"]}
    assert amount(rows[0]["first_year_savings"]["lower_microunits"])==0
    assert amount(rows[30]["first_year_savings"]["lower_microunits"])==1100
    assert amount(rows[60]["first_year_savings"]["lower_microunits"])==2550
    assert rows[30]["manual_cases_remaining"]==30
    assert amount(rows[30]["annual_released_person_ms"]["lower"])==54000000
    assert a["first_year_positive_volume"]["partial_guaranteed_positive"]=={
        "empty":False,"minimum_cases":6,"maximum_cases":59}
    assert a["first_year_positive_volume"]["full_guaranteed_positive"] is True
    assert cheap["declared_outcome_review"]["status"]=="pass"


def test_default_adoption_does_not_assume_fixed_manual_savings(trial):
    packet,plan,captures,req=baseline(trial)
    cheap=candidate(assess(packet,plan,captures,req))
    a=cheap["conditional_adoption"]
    assert a["avoidable_manual_fixed_at_full_adoption_microunits"]=="0"
    assert amount(a["rows"][-1]["first_year_savings"]["lower_microunits"])==2450
    assert amount(cheap["first_year_savings_scenario"]["lower_microunits"])==2550


def test_outcome_regression_cannot_be_hidden_by_cost_savings(trial):
    packet,plan,captures,req=baseline(trial)
    slots={s["slot_id"]:s for s in plan["slots"]}
    selected=[x for x in req["interventions"] if slots[x["slot_id"]]["candidate_id"]=="cheap"]
    selected[0]["observed_business_outcome"]="failure"
    selected[1]["observed_business_outcome"]=None
    cheap=candidate(assess(packet,plan,captures,req))
    q=cheap["declared_outcome_review"]
    assert q["regression_count"]=={"lower":1,"upper":2}
    assert q["success_count_change"]=={"lower":-2,"upper":-1}
    assert q["status"]=="fail"
    assert q["checks"]["declared_paired_regressions"]=="fail"
    assert cheap["positive_first_year_lower_bound"] is True
    assert any(r["declared_outcome_comparison"]["regression"]["lower"] for r in cheap["rows"])


def test_partial_adoption_unknown_cost_and_capacity_not_zero(trial):
    packet,plan,captures,req=baseline(trial)
    req["interventions"].pop()
    result=assess(packet,plan,captures,req)
    affected=next(c for c in result["candidates"] if c["candidate"]["complete_cost_attempts"]==5)
    assert affected["conditional_adoption"]["first_year_positive_volume"]["partial_guaranteed_positive"] is None
    assert affected["conditional_adoption"]["rows"][-1]["annual_released_person_ms"]["lower"] is None
    assert affected["declared_outcome_review"]["status"]=="unknown"


@pytest.mark.parametrize("policy",[
    {"adoption_case_counts":[61]}, {"adoption_case_counts":[30,30]},
    {"adoption_case_counts":[30],"avoidable_manual_annual_microunits":101},
    {"adoption_case_counts":[True]}, {"adoption_case_counts":[30],"minimum_declared_success_basis_points":10001},
])
def test_bad_adoption_policy_refused(trial,policy):
    packet,plan,captures,req=baseline(trial)
    req["scenario"]["adoption"]=policy
    with pytest.raises(ValueError):assess(packet,plan,captures,req)


def test_cost_per_declared_success_keeps_failed_attempt_costs(trial):
    packet,plan,captures,req=baseline(trial)
    slots={s["slot_id"]:s for s in plan["slots"]}
    selected=[x for x in req["interventions"] if slots[x["slot_id"]]["candidate_id"]=="cheap"]
    selected[0]["observed_business_outcome"]="failure"
    selected[1]["observed_business_outcome"]=None
    cheap=candidate(assess(packet,plan,captures,req))
    metric=cheap["candidate"]["cost_per_declared_success"]
    assert amount(metric["lower_microunits"])==66
    assert amount(metric["upper_microunits"])==Fraction(165,2)
    assert metric["status"]=="bounded_or_unresolved"
    for row in selected:row["observed_business_outcome"]="failure"
    metric=candidate(assess(packet,plan,captures,req))["candidate"]["cost_per_declared_success"]
    assert metric=={"lower_microunits":None,"upper_microunits":None,"status":"no_possible_success"}
    selected[0]["observed_business_outcome"]="unresolved"
    metric=candidate(assess(packet,plan,captures,req))["candidate"]["cost_per_declared_success"]
    assert amount(metric["lower_microunits"])==330
    assert metric["upper_microunits"] is None


def test_category_reviews_reconcile_population_without_cherry_picking(trial):
    packet,plan,captures,req=baseline(trial)
    cheap=candidate(assess(packet,plan,captures,req))
    groups=cheap["category_reviews"]
    assert {g["category"] for g in groups}=={"normal","exception","missing_facts"}
    assert sum(g["distinct_cases"] for g in groups)==3
    assert sum(g["candidate"]["attempts"] for g in groups)==6
    assert sum(amount(g["paired_savings"]["lower_microunits"]) for g in groups)==270
    assert {r["case_category"] for r in cheap["rows"]}=={"normal","exception","missing_facts"}
    assert cheap["declared_outcome_review"]["minimum_declared_success_basis_points"]==9000
