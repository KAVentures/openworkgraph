"""Real R18 monitoring inventory and R26 outcome-decision integration."""
from copy import deepcopy

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_outcomes import review,decide
from shared.process_agent_project import build_runtime_spec
from server import automation_monitoring as monitoring
from server import automation_release_inventory as releases
from server import agent_procurement_deployment as bridge
from test_agent_procurement_outcomes import rehearsal,rollout
from test_agent_procurement_substitution import preflight
from test_agent_procurement_selection import selection
from test_agent_procurement_trials import trial
from test_agent_procurement import study


@pytest.fixture
def readiness(rehearsal,tmp_path,monkeypatch):
    monkeypatch.setattr(monitoring,"DATA_DIR",tmp_path/"monitoring")
    packet,bundle,pf,plan,ledger=rehearsal
    spec=build_runtime_spec(packet);sha=monitoring.spec_digest(spec)
    def deployment(arm,stage):
        doc={"format":monitoring.FORMAT,"deployment":{"name":arm,"runtime":"other","workflow_spec_sha256":sha},
             "runs":[{"run_id":"run1","started_at":"2026-01-01T00:00:00Z","finished_at":"2026-01-01T00:00:01Z",
                       "status":"succeeded","steps":[{"step_id":"a","activity":"PRIVATE_STEP","status":"completed","at":"2026-01-01T00:00:00Z"}]}]}
        dep=monitoring.import_report(doc)["deployment"]["deployment_id"]
        monitoring.attach_design(dep,spec)
        pin=pf["artifact_bindings"][arm]["manifest_sha256"]
        releases.record(dep,{"expected_revision":0,"expected_reported_spec_sha256":sha,"expected_design_sha256":sha,
                             "stage":stage,"release_label":"v1","artifact_sha256":pin,"user_confirmed":True})
        return {"deployment_id":dep,"release_revision":1,"manifest_sha256":pin},doc
    b,bd=deployment("baseline","production_reported");c,cd=deployment("candidate","pilot_reported")
    rb={"format":"openworkgraph.procurement-rollback-plan.v1","baseline":b,"candidate":c,"owner_alias":"rollback-owner",
        "baseline_configuration_sha256":pf["artifact_bindings"]["baseline"]["configuration_sha256"],
        "candidate_configuration_sha256":pf["artifact_bindings"]["candidate"]["configuration_sha256"],
        "stop_candidate_before_restore":True,"reconcile_inflight_work_before_restore":True,"preserve_evidence":True,
        "require_separate_execution_authorization":True}
    result=review(*rehearsal);req=rollout(result);req["rollback_plan_sha256"]=digest(rb)
    receipt=decide(*rehearsal,req)
    rollout_bundle={"decision_bundle":bundle,"preflight":pf,"outcome_plan":plan,"observations":ledger,"request":req}
    request={"expected_rollout_receipt_sha256":receipt["receipt_sha256"],"baseline":b,"candidate":c,
             "window_start":"2026-01-01T00:00:00Z","window_end":"2026-01-02T00:00:00Z",
             "policy":{"minimum_baseline_runs":1,"minimum_candidate_runs":1,"maximum_candidate_p95_ratio_basis_points":10000}}
    return packet,rollout_bundle,request,rb,bd,cd


def decision(result):
    return {"decision":"refer_to_release_owner","reviewer_alias":"release-reviewer",
            "expected_deployment_review_sha256":result["deployment_review_sha256"],"confirmed":True}


def test_real_inventory_referral_and_no_mutation(readiness):
    args=readiness[:4];result=bridge.inspect(*args)
    assert result["eligible_for_release_owner_referral"]
    assert result["candidate"]["runs"]==1 and result["candidate"]["p95_elapsed_ms"]==1000
    assert "PRIVATE_STEP" not in str(result)
    receipt=bridge.decide(*args,decision(result))
    assert receipt["release_owner_referral_requested"] and not receipt["production_rollout_authorized"]
    assert not receipt["release_inventory_mutated"] and not receipt["execution_authorized"]
    assert all(releases.detail(p["deployment_id"])["revision"]==1 for p in (args[2]["baseline"],args[2]["candidate"]))


def test_changed_step_invalidates_receipt_with_same_rates(readiness):
    packet,bundle,req,rb,bd,cd=readiness
    result=bridge.inspect(packet,bundle,req,rb)
    cd["runs"][0]["steps"][0]["activity"]="changed"
    monitoring.import_report(cd,req["candidate"]["deployment_id"])
    newer=bridge.inspect(packet,bundle,req,rb)
    assert newer["candidate"]["status_counts"]==result["candidate"]["status_counts"]
    assert newer["candidate"]["step_population_sha256"]!=result["candidate"]["step_population_sha256"]
    with pytest.raises(ValueError,match="changed"):bridge.decide(packet,bundle,req,rb,decision(result))


@pytest.mark.parametrize("status",["failed","handed_to_human","running","cancelled"])
def test_unsuccessful_pilot_blocks_positive_referral(readiness,status):
    packet,bundle,req,rb,bd,cd=readiness
    cd["runs"][0]["status"]=status
    if status=="running":cd["runs"][0]["finished_at"]=None
    monitoring.import_report(cd,req["candidate"]["deployment_id"])
    result=bridge.inspect(packet,bundle,req,rb)
    assert not result["eligible_for_release_owner_referral"]
    with pytest.raises(ValueError,match="does not pass"):bridge.decide(packet,bundle,req,rb,decision(result))
    d=decision(result);d["decision"]="retain_baseline"
    assert not bridge.decide(packet,bundle,req,rb,d)["release_owner_referral_requested"]


def test_changed_release_and_rollback_document_refused(readiness):
    packet,bundle,req,rb,bd,cd=readiness
    changed=deepcopy(rb);changed["owner_alias"]="changed"
    with pytest.raises(ValueError,match="rollback document"):bridge.inspect(packet,bundle,req,changed)
    ident=req["candidate"]["deployment_id"];inv=releases.detail(ident)
    releases.record(ident,{"expected_revision":1,"expected_reported_spec_sha256":inv["expected_reported_spec_sha256"],
                           "expected_design_sha256":inv["expected_design_sha256"],"stage":"paused_reported","release_label":"v2","user_confirmed":True})
    with pytest.raises(ValueError,match="release revision"):bridge.inspect(packet,bundle,req,rb)


def test_unattributed_reports_and_half_open_window(readiness):
    packet,bundle,req,rb,bd,cd=readiness
    extra=deepcopy(cd);del extra["deployment"]["workflow_spec_sha256"]
    extra["runs"][0]["run_id"]="unattributed"
    monitoring.import_report(extra,req["candidate"]["deployment_id"])
    outside=deepcopy(cd);outside["runs"][0].update(run_id="outside",started_at="2026-01-02T00:00:00Z",
                                                 finished_at="2026-01-02T00:00:01Z",steps=[])
    monitoring.import_report(outside,req["candidate"]["deployment_id"])
    result=bridge.inspect(packet,bundle,req,rb)
    assert result["candidate"]["runs"]==2 and result["candidate"]["unattributed_runs"]==1
    assert result["checks"]["complete_exact_spec_attribution"]=="unknown"
    assert not result["eligible_for_release_owner_referral"]


def test_distinct_reviewer_and_future_window(readiness):
    result=bridge.inspect(*readiness[:4]);d=decision(result);d["reviewer_alias"]="ROLLBACK-OWNER"
    with pytest.raises(ValueError,match="distinct"):bridge.decide(*readiness[:4],d)
    packet,bundle,req,rb=readiness[:4];req["window_end"]="2099-01-02T00:00:00Z"
    with pytest.raises(ValueError):bridge.inspect(packet,bundle,req,rb)
