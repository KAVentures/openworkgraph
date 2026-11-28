"""Authenticated organization procurement governance, with real catalog revisions."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from gateway.process_procurement import SharedProcurement,Submit,Decision,Remove,Handoff,namespace
from gateway.process_collaboration import SharedProcessWrite,ProcessMember,ProcessDeleteWrite,ProcessCollaborationError
from shared.agent_procurement import digest
from shared.agent_procurement_trials import freeze
from shared.agent_procurement_selection import nominate
from test_process_collaboration import store,person,grant,review,app
from test_agent_procurement import study
from test_agent_procurement_trials import capture,ledger
from test_process_shadow_review import generated_report


def evidence(packet,req,tmp_path):
    real=generated_report(packet,tmp_path/"org-runtime")
    req=deepcopy(req)
    for c in req["candidates"]:
        c["report"].update(process_reference=real["process_reference"],spec_sha256=real["spec_sha256"])
    design={"format":"openworkgraph.procurement-trial-design.v1","reference_report":req["candidates"][0]["report"],
            "baseline_candidate_id":"baseline","currency":"USD","repeats":2,"schedule_seed":17,"confirmed":True,
            "gates":{"minimum_match_basis_points":5000},
            "candidates":[{**{k:c[k] for k in ("candidate_id","provider","model","runtime")},
                           "configuration_sha256":digest(c["candidate_id"])} for c in req["candidates"][:2]]}
    dp=freeze(packet,design)
    held=deepcopy(req)
    for c in held["candidates"]:
        c["report"]["suite_sha256"]="e"*64
        for row in c["report"]["cases"]:row["case_sha256"]=digest(["holdout",row["case_id"]])
    hd=deepcopy(design);hd["reference_report"]=held["candidates"][0]["report"]
    hp=freeze(packet,hd)
    dc=ledger(dp,[capture(req,cid,n) for cid in ("baseline","cheap") for n in (1,2)])
    hc=ledger(hp,[capture(held,cid,n) for cid in ("baseline","cheap") for n in (1,2)])
    request={"candidate_id":"cheap","nominator_alias":"PRIVATE_ALIAS","confirmed":True,
             "policy":{"minimum_discovery_cases":3,"minimum_holdout_cases":3}}
    nomination=nominate(packet,dp,dc,hp,request)
    return {"discovery_plan":dp,"discovery_captures":dc,"holdout_plan":hp,"holdout_captures":hc,"nomination":nomination}


@pytest.fixture
def submitted(store,study,tmp_path):
    original,req=study
    first=store.save(person(),SharedProcessWrite(title="Shared evaluation",process_model=original["process_model"],
                  user_confirmed=True,organization_sharing_confirmed=True))
    process=first["process"];wid=process["workflow_id"]
    grant(store,wid,members=[ProcessMember(actor_id="bob",role="reviewer")])
    store.review(person("bob"),wid,review(process))
    packet={k:process[k] for k in ("workflow_id","revision","content_sha256","process_model")}
    packet.update(format="openworkgraph.versioned-process-export.v1",execution_authorization=False,
                  organization_catalog_reference={"organization_id":"acme"})
    bundle=evidence(packet,req,tmp_path)
    gov=SharedProcurement(store)
    body=Submit(process_revision=1,expected_procurement_revision=0,expected_access_revision=2,
                bundle=json.dumps(bundle),user_confirmed=True,organization_sharing_confirmed=True)
    gov.submit(person(),wid,body)
    return gov,wid,body,bundle,process


def decision(gov,wid,bundle):
    item=gov.detail(person(),wid)["artifact"]
    return Decision(expected_procurement_revision=item["revision"],expected_access_revision=item["submitted_access_revision"],
                    expected_assessment_sha256=item["summary"]["assessment_sha256"],decision="approve_rehearsal_review",
                    bundle=json.dumps(bundle),user_confirmed=True)


def test_distinct_authenticated_actor_and_minimized_persistence(submitted):
    gov,wid,body,bundle,process=submitted
    d=decision(gov,wid,bundle)
    with pytest.raises(ProcessCollaborationError,match="different authenticated"):
        gov.decide(person(),wid,d)
    assert gov.detail(person("bob"),wid)["may_decide"]
    gov.decide(person("bob"),wid,d)
    result=gov.detail(person("reader"),wid)
    assert result["current_approved_rehearsal_review"]
    assert result["artifact"]["decision_actor_id"]=="bob"
    assert not result["execution_authorized"] and not result["production_rollout_authorized"]
    assert not result["raw_trial_bundles_retained"]
    assert "PRIVATE_ALIAS" not in json.dumps(result)
    with gov.db.connect() as c:
        records=gov.q(c,"SELECT payload FROM workflow_knowledge WHERE owner=?",(namespace("acme"),)).fetchall()
    assert len(records)==2
    assert all("PRIVATE_ALIAS" not in r[0] and "case_sha256" not in r[0] and "discovery_captures" not in r[0] for r in records)
    assert gov.inventory(person("reader"))["items"][0]["current_approved_rehearsal_review"]
    assert not gov.detail(person(),wid,revision=1)["current_approved_rehearsal_review"]


def test_tenant_scope_and_revoked_reviewer(submitted):
    gov,wid,body,bundle,process=submitted
    with pytest.raises(ProcessCollaborationError) as error:gov.detail(person(org="foreign"),wid)
    assert error.value.status==404
    assert gov.inventory(person(org="foreign"))["items"]==[]
    with pytest.raises(ProcessCollaborationError) as error:
        gov.inventory(person(scopes=frozenset({"org:evidence:read"})))
    assert error.value.status==403
    d=decision(gov,wid,bundle)
    grant(gov.store,wid,members=[],expected=2)
    with pytest.raises(ProcessCollaborationError) as error:gov.decide(person("bob"),wid,d)
    assert error.value.status==403
    assert "process_access_changed" in gov.detail(person(),wid)["blockers"]


def test_modified_evidence_and_stale_design(submitted):
    gov,wid,body,bundle,process=submitted
    d=decision(gov,wid,bundle)
    changed=deepcopy(bundle);changed["holdout_captures"]["captures"][0]["attempts"][0]["elapsed_ms"]=123
    with pytest.raises(ProcessCollaborationError,match="evidence changed"):
        gov.decide(person("bob"),wid,d.model_copy(update={"bundle":json.dumps(changed)}))
    gov.store.save(person(),SharedProcessWrite(title="New revision",process_model=process["process_model"],
                   expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)
    with pytest.raises(ProcessCollaborationError,match="Current active"):gov.decide(person("bob"),wid,d)
    assert "design_not_current_reviewed" in gov.detail(person(),wid)["blockers"]


def test_root_deletion_erases_procurement_history(submitted):
    gov,wid,body,bundle,process=submitted
    gov.store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    with gov.db.connect() as c:
        assert gov.q(c,"SELECT COUNT(*) FROM workflow_knowledge WHERE owner=?",(namespace("acme"),)).fetchone()[0]==0
    assert gov.inventory(person())["items"]==[]


def test_http_auth_and_portal(tmp_path):
    with TestClient(app(tmp_path)) as client:
        path="/v1/human/process-procurement-portfolio"
        assert client.get(path).status_code==401
        assert client.get(path,headers={"Authorization":"Bearer no-process"}).status_code==403
        response=client.get(path,headers={"Authorization":"Bearer reader"})
        assert response.status_code==200 and response.headers["cache-control"]=="no-store"
        assert response.json()["items"]==[]
        assert "procurementInventory" in client.get("/processes").text
        assert "owgSharedProcurement" in client.get("/process-procurement-client.js").text


def handoff_inputs(submitted):
    from shared.agent_procurement_selection import operation
    gov,wid,body,bundle,process=submitted
    gov.decide(person("bob"),wid,decision(gov,wid,bundle))
    item=gov.detail(person(),wid)["artifact"]
    packet={k:process[k] for k in ("workflow_id","revision","content_sha256","process_model")}
    packet.update(format="openworkgraph.versioned-process-export.v1",execution_authorization=False,
                  organization_catalog_reference={"organization_id":"acme"})
    value={**bundle,"request":{"decision":"select_for_substitution_rehearsal","reviewer_alias":"reviewer",
           "expected_assessment_sha256":item["summary"]["assessment_sha256"],
           "expected_nomination_sha256":item["summary"]["nomination_sha256"],"confirmed":True}}
    receipt=operation(packet,"decide",value)
    request=Handoff(expected_procurement_revision=item["revision"],expected_access_revision=item["submitted_access_revision"],
                    expected_approval_sha256=item["content_sha256"],expected_selection_receipt_sha256=receipt["receipt_sha256"],
                    bundle=json.dumps(value),user_confirmed=True)
    return gov,wid,request,packet,value


def test_shared_handoff_matches_exact_approved_selection(submitted):
    gov,wid,request,packet,value=handoff_inputs(submitted)
    checked=gov.check_handoff(person("reader"),wid,request)
    assert checked["current_shared_rehearsal_approval_at_inspection"]
    assert checked["selection_receipt_sha256"]==request.expected_selection_receipt_sha256
    assert checked["candidate_id"]=="cheap"
    assert not checked["execution_authorized"] and not checked["result_is_transferable_authorization"]
    assert "PRIVATE_ALIAS" not in json.dumps(checked)
    assert checked["handoff_check_sha256"]==digest({k:v for k,v in checked.items() if k!="handoff_check_sha256"})
    with pytest.raises(ProcessCollaborationError):gov.check_handoff(person(org="foreign"),wid,request)
    with pytest.raises(ProcessCollaborationError):gov.check_handoff(person("reader"),wid,request.model_copy(update={"expected_selection_receipt_sha256":"f"*64}))
    with pytest.raises(ProcessCollaborationError):gov.check_handoff(person("reader"),wid,request.model_copy(update={"user_confirmed":False}))
    assert gov.detail(person(),wid)["latest_revision"]==2


def test_shared_handoff_rejects_retain_and_access_change(submitted):
    from shared.agent_procurement_selection import operation
    gov,wid,request,packet,value=handoff_inputs(submitted)
    value["request"]["decision"]="retain_baseline"
    alternate=operation(packet,"decide",value)
    with pytest.raises(ProcessCollaborationError,match="does not match"):
        gov.check_handoff(person("reader"),wid,request.model_copy(update={"bundle":json.dumps(value),"expected_selection_receipt_sha256":alternate["receipt_sha256"]}))
    grant(gov.store,wid,members=[],expected=2)
    with pytest.raises(ProcessCollaborationError,match="Current authenticated"):
        gov.check_handoff(person("reader"),wid,request)


def test_reapproved_design_does_not_revive_old_procurement_approval(submitted):
    gov,wid,body,bundle,process=submitted
    gov.decide(person("bob"),wid,decision(gov,wid,bundle))
    assert gov.detail(person(),wid)["current_approved_rehearsal_review"]
    gov.store.review(person("bob"),wid,review(process,"changes_requested",1))
    assert not gov.detail(person(),wid)["current_approved_rehearsal_review"]
    gov.store.review(person("bob"),wid,review(process,"reviewed_design",2))
    view=gov.detail(person(),wid)
    assert not view["current_approved_rehearsal_review"]
    assert "design_review_history_changed" in view["blockers"]
    gov.submit(person(),wid,body.model_copy(update={"expected_procurement_revision":2}))
    gov.decide(person("bob"),wid,decision(gov,wid,bundle))
    assert gov.detail(person(),wid)["current_approved_rehearsal_review"]
