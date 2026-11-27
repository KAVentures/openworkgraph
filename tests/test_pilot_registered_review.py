"""Exact prospective registration and durable review CAS with real workspace."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from evals import pilot_registered_review as registered
from evals import pilot_decision as decision
from evals import pilot_workspace as workspace
from evals import prospective_pilot_cohort as cohort
from test_pilot_workspace import open_study,update,completion,batch
from test_pilot_execution import start
from test_pilot_blinded_batches import bundle,sheet
from test_pilot_decision_bounds import thresholds


def register(db,state,**policy):
    return registered.register(db,thresholds(**policy),expected_head_sha256=state["head_sha256"],confirmed=True)


def preview(db,state,truth,batches):
    return registered.preview(db,truth,batches,expected_head_sha256=state["head_sha256"],
        expected_protocol_sha256=state["decision_protocol"]["protocol_sha256"],confirmed=True)


def record(db,state,truth,batches,value,**changes):
    return registered.record(db,truth,batches,**{
        "expected_head_sha256":state["head_sha256"],
        "expected_protocol_sha256":state["decision_protocol"]["protocol_sha256"],
        "expected_decision_sha256":value["decision_sha256"],
        "disposition":"collect_more_evidence","reason":"incomplete_review","confirmed":True,**changes})


def test_register_before_execution_and_freeze_single_policy(tmp_path):
    db,v,truth=open_study(tmp_path)
    assert registered.status(db,expected_head_sha256=v["head_sha256"])["registration_possible"]
    with pytest.raises(ValueError):
        registered.register(db,thresholds(),expected_head_sha256=v["head_sha256"],confirmed=False)
    assert workspace.status(db)==v
    v2=register(db,v)
    assert v2["revision"]==1 and v2["previous_head_sha256"]==v["head_sha256"]
    assert workspace.status(db)==v2
    assert v2["decision_protocol"]["initial_head_sha256"]==v["head_sha256"]
    assert v2["decision_reviews"]==[]
    with pytest.raises(workspace.Conflict):register(db,v2,min_enrolled=3)
    with pytest.raises(workspace.Conflict):register(db,v)
    report=preview(db,v2,truth,[])
    assert report["protocol_registered_in_workspace"]
    assert report["registered_head_sha256"]==v2["head_sha256"]
    changed=deepcopy(v2["decision_protocol"]);changed["thresholds"]["min_enrolled"]=3
    changed["protocol_sha256"]=cohort.digest({k:x for k,x in changed.items() if k!="protocol_sha256"})
    with pytest.raises(ValueError,match="durable registration"):
        decision.report(db,changed,truth,[],expected_protocol_sha256=changed["protocol_sha256"],
                        expected_head_sha256=v2["head_sha256"],confirmed=True)


def test_late_registration_and_existing_unregistered_workspace_unchanged(tmp_path):
    db,v,truth=open_study(tmp_path)
    row=v["schedule"]["episodes"][0]
    v=update(db,v,[start(v["plan"],row["episode_id"],row["arm_order"][0],1)])
    assert not registered.status(db,expected_head_sha256=v["head_sha256"])["registration_possible"]
    with pytest.raises(workspace.Conflict):register(db,v)
    assert "decision_protocol" not in workspace.status(db)
    with pytest.raises(ValueError,match="registered decision protocol"):
        registered.preview(db,truth,[],expected_head_sha256=v["head_sha256"],
            expected_protocol_sha256="a"*64,confirmed=True)


def test_concurrent_initial_registration_has_one_winner(tmp_path):
    db,v,truth=open_study(tmp_path)
    def attempt(n):
        try:return register(db,v,min_enrolled=n)
        except workspace.Conflict:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,(2,3)))
    wins=[r for r in results if r is not None]
    assert len(wins)==1 and workspace.status(db)==wins[0]


def test_review_recomputed_and_invalidated_by_next_mutation(tmp_path):
    db,v,truth=open_study(tmp_path)
    v=register(db,v);p=preview(db,v,truth,[])
    with pytest.raises(ValueError,match="digest mismatch"):
        record(db,v,truth,[],p,expected_decision_sha256="f"*64)
    with pytest.raises(ValueError,match="passing complete"):
        record(db,v,truth,[],p,disposition="continue_external_validation",reason="thresholds_met_followup_required")
    with pytest.raises(ValueError,match="explicit reviewed"):
        record(db,v,truth,[],p,confirmed=False)
    assert workspace.status(db)==v
    result=record(db,v,truth,[],p)
    updated=workspace.status(db)
    assert result["head_sha256"]==updated["head_sha256"]
    assert result["review"]["evidence_head_sha256"]==v["head_sha256"]
    assert result["review"]["decision_sha256"]==p["decision_sha256"]
    assert result["review"]["review_sha256"]==cohort.digest({k:x for k,x in result["review"].items() if k!="review_sha256"})
    assert not result["rollout_authorized"]
    status=registered.status(db,expected_head_sha256=updated["head_sha256"])
    assert status["latest_review_current"]
    with pytest.raises(workspace.Conflict):record(db,v,truth,[],p)
    p2=preview(db,updated,truth,[])
    with pytest.raises(workspace.Conflict,match="already current"):record(db,updated,truth,[],p2)
    row=updated["schedule"]["episodes"][0]
    changed=update(db,updated,[start(updated["plan"],row["episode_id"],row["arm_order"][0],1)])
    assert not registered.status(db,expected_head_sha256=changed["head_sha256"])["latest_review_current"]
    new=record(db,changed,truth,[],preview(db,changed,truth,[]))
    assert registered.status(db,expected_head_sha256=new["head_sha256"])["review_count"]==2


def test_complete_blinded_batches_and_retired_evidence(tmp_path):
    db,v,truth=open_study(tmp_path,2)
    v=register(db,v)
    materials=[]
    for row in v["schedule"]["episodes"]:
        for arm in row["arm_order"]:
            v,m=completion(db,v,row["episode_id"],arm,v["ledger"]["revision"]+1)
            materials.append(m)
    v,p,k=batch(tmp_path,db,v,truth,materials,"registered")
    submissions=[bundle(p,k,sheet(p))]
    report=preview(db,v,truth,submissions)
    assert report["canonical_scorer_invoked"] and report["summary"]["full_paired_coverage"]
    with pytest.raises(ValueError,match="contradicts"):
        record(db,v,truth,submissions,report)
    result=record(db,v,truth,submissions,report,disposition="collect_more_evidence",reason="external_validation_required")
    latest=workspace.status(db)
    v=workspace.retire_batch(db,k["batch_sha256"],expected_head_sha256=latest["head_sha256"],confirmed=True)
    assert not registered.status(db,expected_head_sha256=v["head_sha256"])["latest_review_current"]
    with pytest.raises(workspace.Conflict):preview(db,v,truth,submissions)
    assert not preview(db,v,truth,[])["summary"]["full_paired_coverage"]


@pytest.mark.parametrize("kind",["protocol","missing_registration","review","review_history"])
def test_corrupt_registration_or_review_history_refused(tmp_path,kind):
    db,v,truth=open_study(tmp_path);v=register(db,v)
    record(db,v,truth,[],preview(db,v,truth,[]))
    state=workspace.status(db)
    with sqlite3.connect(db) as conn:
        if kind=="missing_registration":
            conn.execute("UPDATE history SET operation='append' WHERE revision=1")
        elif kind=="review_history":
            conn.execute("UPDATE history SET operation='append' WHERE revision=2")
        else:
            if kind=="protocol":
                state["decision_protocol"]["thresholds"]["min_enrolled"]=3
                p=state["decision_protocol"]
                p["protocol_sha256"]=cohort.digest({k:x for k,x in p.items() if k!="protocol_sha256"})
            else:state["decision_reviews"][0]["decision_sha256"]="f"*64
            state=workspace._head(workspace._body(state))
            conn.execute("UPDATE workspace SET body=?",(cohort.canonical(state).decode(),))
            conn.execute("UPDATE history SET head=? WHERE revision=?",(state["head_sha256"],state["revision"]))
    with pytest.raises(ValueError):workspace.status(db)


def test_new_external_adjudication_can_supersede_review_without_other_mutation(tmp_path):
    db,v,truth=open_study(tmp_path,2)
    v=register(db,v)
    materials=[]
    for row in v["schedule"]["episodes"]:
        for arm in row["arm_order"]:
            v,m=completion(db,v,row["episode_id"],arm,v["ledger"]["revision"]+1)
            materials.append(m)
    v,p,k=batch(tmp_path,db,v,truth,materials,"external")
    incomplete=preview(db,v,truth,[])
    first=record(db,v,truth,[],incomplete)
    state=workspace.status(db)
    assert first["review"]["revision"]==state["revision"]
    submissions=[bundle(p,k,sheet(p))]
    complete=preview(db,state,truth,submissions)
    assert complete["summary"]["full_paired_coverage"]
    second=record(db,state,truth,submissions,complete,
                  disposition="collect_more_evidence",reason="external_validation_required")
    assert second["review"]["score_sha256"]!=first["review"]["score_sha256"]
    assert registered.status(db,expected_head_sha256=second["head_sha256"])["review_count"]==2
