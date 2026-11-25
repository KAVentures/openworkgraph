"""Outcome rehearsal counts exact state, unintended actions and unresolved runs."""
from copy import deepcopy

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_substitution import inspect
from shared.agent_procurement_outcomes import freeze,challenge,review,decide
from test_agent_procurement_substitution import preflight
from test_agent_procurement_selection import selection
from test_agent_procurement_trials import trial
from test_agent_procurement import study


@pytest.fixture
def rehearsal(preflight):
    packet,bundle,receipt,old,new,req=preflight
    pf=inspect(*preflight)
    def snap(state,other="untouched"):
        return {"records":[{"record_id":"target","fields":{"state":state}},
                           {"record_id":"other","fields":{"state":other}}]}
    cases=[{"case_id":"case_1","category":"normal","starting_state":snap("open"),
            "expected_final_state":snap("closed"),"permitted_actions":[{"operation_id":"close","record_id":"target"}],"reviewed":True},
           {"case_id":"case_2","category":"missing_facts","starting_state":snap("pending"),
            "expected_final_state":snap("pending"),"permitted_actions":[],"reviewed":True}]
    design={"format":"openworkgraph.procurement-outcome-design.v1",
            "expected_selection_receipt_sha256":receipt["receipt_sha256"],
            "expected_preflight_sha256":pf["preflight_sha256"],"owner_alias":"owner",
            "sandbox_configuration_sha256":digest("sandbox"),"cases":cases,
            "policy":{"minimum_distinct_cases":2,"require_all_categories":False},"confirmed":True}
    plan=freeze(packet,bundle,pf,design)
    observations=[]
    for slot in plan["slots"]:
        case=next(c for c in cases if c["case_id"]==slot["case_id"])
        observations.append({**{k:slot[k] for k in ("case_id","arm","slot_sha256","configuration_sha256")},
          "sandbox_configuration_sha256":design["sandbox_configuration_sha256"],"observer_alias":"observer",
          "isolated_run_id":slot["case_id"]+"-"+slot["arm"],"started_at":"2026-01-01T00:00:00Z",
          "ended_at":"2026-01-01T00:00:01Z","status":"completed","initial_snapshot":deepcopy(case["starting_state"]),
          "final_snapshot":deepcopy(case["expected_final_state"]),"observed_actions":deepcopy(case["permitted_actions"]),
          "action_capture_complete":True})
    ledger={"format":"openworkgraph.procurement-outcome-observations.v1","plan_sha256":plan["plan_sha256"],"observations":observations}
    return packet,bundle,pf,plan,ledger


def rollout(result):
    return {"decision":"request_separate_rollout_review","reviewer_alias":"reviewer",
            "expected_outcome_review_sha256":result["outcome_review_sha256"],
            "expected_plan_sha256":result["plan_sha256"],"rollback_plan_sha256":digest("rollback"),"confirmed":True}


def test_blind_challenge_and_clean_rehearsal(rehearsal):
    packet,bundle,pf,plan,ledger=rehearsal
    with pytest.raises(ValueError):challenge(packet,bundle,pf,plan)
    c=challenge(packet,bundle,pf,plan,confirmed=True)
    assert set(c["cases"][0])=={"case_id","starting_state"}
    assert "expected_final_state" not in c["cases"][0]
    result=review(*rehearsal)
    assert result["eligible_to_request_separate_rollout_review"]
    assert result["arms"]["candidate"]["success_bounds"]["lower"]==2
    receipt=decide(*rehearsal,rollout(result))
    assert receipt["separate_rollout_review_requested"]
    assert not receipt["production_rollout_authorized"] and not receipt["deployment_changed"]
    assert not result["independent_real_business_outcomes_verified"]


@pytest.mark.parametrize("mutation",["unintended_state","forbidden_action","incomplete_actions","missing","timeout"])
def test_harm_and_unknown_block_rollout(rehearsal,mutation):
    packet,bundle,pf,plan,ledger=rehearsal
    obs=ledger["observations"][1]
    if mutation=="unintended_state":obs["final_snapshot"]["records"][1]["fields"]["state"]="changed"
    elif mutation=="forbidden_action":obs["observed_actions"].append({"operation_id":"delete","record_id":"other"})
    elif mutation=="incomplete_actions":obs["action_capture_complete"]=False
    elif mutation=="missing":ledger["observations"].pop(1)
    elif mutation=="timeout":obs.update(status="timeout",final_snapshot=None)
    result=review(*rehearsal)
    assert not result["eligible_to_request_separate_rollout_review"]
    with pytest.raises(ValueError,match="passing outcome"):decide(*rehearsal,rollout(result))
    req=rollout(result);req["decision"]="retain_baseline"
    assert not decide(*rehearsal,req)["separate_rollout_review_requested"]


@pytest.mark.parametrize("mutation",["wrong_initial","wrong_slot","wrong_config","duplicate","reused_run","wrong_plan"])
def test_exact_observation_binding(rehearsal,mutation):
    packet,bundle,pf,plan,ledger=rehearsal
    obs=ledger["observations"][1]
    if mutation=="wrong_initial":obs["initial_snapshot"]["records"][0]["fields"]["state"]="already closed"
    elif mutation=="wrong_slot":obs["slot_sha256"]="f"*64
    elif mutation=="wrong_config":obs["configuration_sha256"]="f"*64
    elif mutation=="duplicate":ledger["observations"].append(deepcopy(obs))
    elif mutation=="reused_run":obs["isolated_run_id"]=ledger["observations"][0]["isolated_run_id"]
    elif mutation=="wrong_plan":ledger["plan_sha256"]="f"*64
    with pytest.raises(ValueError):review(*rehearsal)


def test_stale_decision_and_rollback_required(rehearsal):
    result=review(*rehearsal);req=rollout(result)
    req["rollback_plan_sha256"]=None
    with pytest.raises(ValueError,match="rollback"):decide(*rehearsal,req)
    req=rollout(result);req["reviewer_alias"]="OWNER"
    with pytest.raises(ValueError,match="distinct"):decide(*rehearsal,req)
    req=rollout(result)
    rehearsal[-1]["observations"][0]["observer_alias"]="new-observer"
    with pytest.raises(ValueError,match="exact current"):decide(*rehearsal,req)


def test_preflight_pin_and_plan_tampering_refused(rehearsal):
    packet,bundle,pf,plan,ledger=rehearsal
    changed=deepcopy(pf);changed["deployment_changed"]=True
    with pytest.raises(ValueError):review(packet,bundle,changed,plan,ledger)
    changed=deepcopy(plan);changed["slots"][0]["arm"]="candidate"
    with pytest.raises(ValueError):review(packet,bundle,pf,changed,ledger)
