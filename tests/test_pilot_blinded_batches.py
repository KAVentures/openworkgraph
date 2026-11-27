"""Real frozen pilot attempts, blinded batches and independent adjudication."""
from copy import deepcopy
from itertools import count
import json

import pytest

from evals import prospective_pilot_cohort as cohort
from evals import pilot_execution as execution
from evals import pilot_blinded_batches as batches
from test_pilot_execution import setup,kwargs,append,start,finish,disposition


def study(n=2,review_protocol="single"):
    truth=[{"episode_id":f"e{i}","prompt":f"Approved neutral prompt {i}",
            "correct_resources":["resource"],"correct_agent_runs":[],
            "ground_truth_verified_by":"truth-author","real_observed_work":False} for i in range(n)]
    p=cohort.prepare(truth,[{"episode_id":r["episode_id"],"team_id":"team",
        "model_id":"model","source_state_id":f"s{i}"} for i,r in enumerate(truth)],confirmed=True)
    p,s,l=setup(p,review_protocol=review_protocol)
    return p,s,l,truth


def complete(p,s,l,eid,arm,now):
    action=start(p,eid,arm,now)
    transport=cohort.digest(["transport",eid,arm])
    material={"episode_id":eid,"arm":arm,"session_id":action["session_id"],
              "model_id":action["model_id"],"source_state_id":action["source_state_id"],
              "transport_sha256":transport,"review_text":f"Approved neutral response {eid}",
              "review_material_approved":True}
    l=append(p,s,l,[action,finish(eid,arm,now+1,cohort.digest(material),transport)])
    return l,material


def prepare(p,s,l,truth,materials,namespace="a"):
    ids=count()
    return batches.prepare(p,s,l,truth,materials,**kwargs(p,s,l),
                           id_factory=lambda:namespace+str(next(ids)))


def sheet(packet,reviewer="reviewer",success=True):
    return [{"review_id":r["review_id"],"reviewer_id":reviewer,
             "selected_resources":["resource"],"selected_agent_runs":[],
             "clarification_questions":0,"wrong_assumptions":0,
             "continuation_success":success,"consequential_wrong_action":False}
            for r in packet["entries"]]


def bundle(packet,key,reviews):
    return {"packet":packet,"key":key,"expected_batch_sha256":key["batch_sha256"],"reviews":reviews}


def reconcile(p,s,l,truth,items,**extra):
    return batches.reconcile(p,s,l,truth,items,**kwargs(p,s,l),**extra)


def test_progressive_batches_across_exact_history_prefixes():
    p,s,l,truth=study()
    eid=s["episodes"][0]["episode_id"];order=s["episodes"][0]["arm_order"]
    l,m=complete(p,s,l,eid,order[0],1)
    packet,key=prepare(p,s,l,truth,[m])
    assert set(packet)=={"format","entries","limitations"}
    assert set(packet["entries"][0])=={"review_id","prompt","review_text"}
    assert "model_id" not in json.dumps(packet) and "transport_sha256" not in json.dumps(packet)
    first=bundle(packet,key,sheet(packet))
    l,m2=complete(p,s,l,eid,order[1],3)
    second_packet,second_key=prepare(p,s,l,truth,[m2],"b")
    second=bundle(second_packet,second_key,[])
    result=reconcile(p,s,l,truth,[first,second])
    assert result["completed_attempts"]==2 and result["assigned_for_review"]==2 and result["reviewed_attempts"]==1
    assert result["score"]["coverage"]["enrolled"]==2
    assert result["score"]["coverage"]["arm_dispositions"][order[1]]["review_pending"]==1
    assert result["score"]["canonical_report"] is None
    assert result==reconcile(p,s,l,truth,[second,first])
    assert not result["blinding_verified"] and not result["independent_transport_verified"]


def test_full_cohort_uses_canonical_existing_scorer():
    p,s,l,truth=study()
    materials=[]
    for row in s["episodes"]:
        for arm in row["arm_order"]:
            l,m=complete(p,s,l,row["episode_id"],arm,l["revision"]+1)
            materials.append(m)
    packet,key=prepare(p,s,l,truth,materials)
    rows=sheet(packet)
    result=reconcile(p,s,l,truth,[bundle(packet,key,rows)])
    score=result["score"]
    assert result["reviewed_attempts"]==6
    assert score["canonical_scorer_invoked"] and score["coverage"]["complete_coverage"]
    assert score["canonical_report"]["status"]=="pilot_only"
    assert score["canonical_report"]["episodes"]==2
    assert not result["product_effectiveness_verified"]


def test_failed_and_withdrawn_attempts_preserve_denominator():
    p,s,l,truth=study(1);row=s["episodes"][0];eid=row["episode_id"];a,b,c=row["arm_order"]
    l,m=complete(p,s,l,eid,a,1)
    l=append(p,s,l,[start(p,eid,b,3),disposition(eid,b,4,"model_failed"),
                   disposition(eid,c,5,"withdrawn")])
    result=reconcile(p,s,l,truth,[],review_dispositions=[{"episode_id":eid,"arm":a,"status":"withdrawn"}])
    coverage=result["score"]["coverage"]
    assert coverage["enrolled"]==1 and coverage["complete_paired_episodes"]==0
    assert coverage["arm_dispositions"]=={a:{"withdrawn":1},b:{"model_failed":1},c:{"withdrawn":1}}
    assert result["score"]["decision_withheld_for_incomplete_coverage"]
    packet,key=prepare(p,s,l,truth,[m])
    with pytest.raises(ValueError):
        reconcile(p,s,l,truth,[bundle(packet,key,[])],review_dispositions=[
            {"episode_id":eid,"arm":a,"status":"withdrawn"}])


@pytest.mark.parametrize("kind",["unapproved","changed_text","changed_session","wrong_transport",
                                 "not_completed","duplicate_material","extra"])
def test_material_must_match_exact_completed_attempt(kind):
    p,s,l,truth=study(1);row=s["episodes"][0];eid=row["episode_id"];a,b,c=row["arm_order"]
    l,m=complete(p,s,l,eid,a,1)
    if kind=="unapproved":m["review_material_approved"]=False
    elif kind=="changed_text":m["review_text"]="different"
    elif kind=="changed_session":m["session_id"]="different"
    elif kind=="wrong_transport":m["transport_sha256"]="0"*64
    elif kind=="not_completed":m["arm"]=b
    elif kind=="extra":m["raw_prompt"]="private"
    with pytest.raises(ValueError):prepare(p,s,l,truth,[m,m] if kind=="duplicate_material" else [m])


@pytest.mark.parametrize("kind",["packet","key","wrong_prefix","truth","duplicate_batch","extra_review",
                                 "duplicate_review","truth_author","missing_boolean","rehashed_material"])
def test_reconciliation_rejects_tamper_and_invalid_reviews(kind):
    p,s,l,truth=study(1);row=s["episodes"][0]
    l,m=complete(p,s,l,row["episode_id"],row["arm_order"][0],1)
    packet,key=prepare(p,s,l,truth,[m]);item=bundle(packet,key,sheet(packet));items=[item]
    if kind=="packet":packet["entries"][0]["review_text"]="changed"
    elif kind=="key":key["ledger_revision"]=1
    elif kind=="wrong_prefix":
        key["ledger_sha256"]="0"*64
        key["batch_sha256"]=cohort.digest({k:v for k,v in key.items() if k!="batch_sha256"})
        item["expected_batch_sha256"]=key["batch_sha256"]
    elif kind=="truth":truth[0]["prompt"]="changed"
    elif kind=="duplicate_batch":items.append(deepcopy(item))
    elif kind=="extra_review":item["reviews"][0]["review_id"]="unplanned"
    elif kind=="duplicate_review":item["reviews"].append(deepcopy(item["reviews"][0]))
    elif kind=="truth_author":item["reviews"][0]["reviewer_id"]=" TRUTH-AUTHOR "
    elif kind=="missing_boolean":item["reviews"][0]["continuation_success"]=None
    else:
        packet["entries"][0]["review_text"]="changed"
        key["packet_sha256"]=cohort.digest(packet)
        key["batch_sha256"]=cohort.digest({k:v for k,v in key.items() if k!="batch_sha256"})
        item["expected_batch_sha256"]=key["batch_sha256"]
    with pytest.raises(ValueError):reconcile(p,s,l,truth,items)


def adjudication_bundle(packet,key):
    first=sheet(packet,"reviewer-A");second=sheet(packet,"reviewer-B")
    second[0]["continuation_success"]=False
    public,basis,agreement=batches.prepare_adjudication(packet,first,second)
    item={"packet":packet,"key":key,"expected_batch_sha256":key["batch_sha256"],
          "adjudication":{"first_review":first,"second_review":second,"packet":public,
              "basis":basis,"expected_adjudication_sha256":basis["adjudication_sha256"],"resolutions":[]}}
    return item,agreement


def test_two_reviewers_and_partial_adjudication():
    p,s,l,truth=study(1,"dual_adjudication");row=s["episodes"][0];materials=[]
    for arm in row["arm_order"]:
        l,m=complete(p,s,l,row["episode_id"],arm,l["revision"]+1);materials.append(m)
    packet,key=prepare(p,s,l,truth,materials)
    item,agreement=adjudication_bundle(packet,key)
    assert agreement["agreement_entries"]==2 and agreement["disagreement_entries"]==1
    result=reconcile(p,s,l,truth,[item])
    assert result["reviewed_attempts"]==2 and result["score"]["canonical_report"] is None
    assert result["adjudications"][0]["unresolved_disagreements"]==1
    public=item["adjudication"]["packet"]
    assert len(batches.resolution_template(public))==1
    row=sheet(public,"reviewer-C",success=False)[0]
    item["adjudication"]["resolutions"]=[{**row,"resolution_reason":"Independent review of approved evidence"}]
    result=reconcile(p,s,l,truth,[item])
    assert result["reviewed_attempts"]==3 and result["score"]["canonical_scorer_invoked"]
    assert result["adjudications"][0]["resolved_disagreements"]==1
    assert result["adjudications"][0]["unresolved_disagreements"]==0


@pytest.mark.parametrize("kind",["same_reviewer","truth_author","changed_basis","changed_disagreement",
                                 "wrong_resolution","same_adjudicator"])
def test_adjudication_integrity(kind):
    p,s,l,truth=study(1,"dual_adjudication");row=s["episodes"][0]
    l,m=complete(p,s,l,row["episode_id"],row["arm_order"][0],1)
    packet,key=prepare(p,s,l,truth,[m]);item,_=adjudication_bundle(packet,key);a=item["adjudication"]
    if kind=="same_reviewer":a["second_review"][0]["reviewer_id"]=" reviewer-a "
    elif kind=="truth_author":a["first_review"][0]["reviewer_id"]="truth-author"
    elif kind=="changed_basis":a["basis"]["first_review_sha256"]="0"*64
    elif kind=="changed_disagreement":a["packet"]["entries"][0]["prompt"]="changed"
    else:
        row=sheet(a["packet"],"reviewer-A" if kind=="same_adjudicator" else "third")[0]
        if kind=="wrong_resolution":row["review_id"]="unknown"
        a["resolutions"]=[{**row,"resolution_reason":"reviewed"}]
    with pytest.raises(ValueError):reconcile(p,s,l,truth,[item])


def test_reviewer_template_cli_requires_no_private_data(tmp_path):
    p,s,l,truth=study(1);row=s["episodes"][0]
    l,m=complete(p,s,l,row["episode_id"],row["arm_order"][0],1)
    packet,key=prepare(p,s,l,truth,[m])
    f=tmp_path/"packet.json";o=tmp_path/"template.json"
    cohort._write(f,packet)
    assert batches.main(["template","--packet",str(f),"--out",str(o),"--confirm"])==0
    template=cohort._load(o)
    assert template[0]["reviewer_id"] is None and template[0]["continuation_success"] is None
    with pytest.raises(SystemExit):batches.main(["template","--packet",str(f),"--out",str(o),"--confirm"])


@pytest.mark.parametrize("protocol",["single","dual_adjudication"])
def test_cannot_switch_review_protocol_after_observing_results(protocol):
    p,s,l,truth=study(1,protocol);row=s["episodes"][0]
    l,m=complete(p,s,l,row["episode_id"],row["arm_order"][0],1)
    packet,key=prepare(p,s,l,truth,[m])
    if protocol=="single":
        item,_=adjudication_bundle(packet,key)
    else:
        item=bundle(packet,key,sheet(packet))
    with pytest.raises(ValueError):reconcile(p,s,l,truth,[item])
