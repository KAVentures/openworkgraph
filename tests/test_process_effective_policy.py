"""R11 exact effective dates, overlap/priority accounting and source review."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date,timedelta
import json
import random

import pytest
from pydantic import ValidationError

from shared import process_effective_policy as ep

PIN="a"*64
RECORD={
    "workflow_id":"refund_process",
    "revision":1,
    "content_sha256":PIN,
    "process_model":{
        "goal":"Approve refunds","baseline_source":"user_declared",
        "baseline_path":["Review","Approve"],
        "activities":[{"id":"review","label":"Review refund","kind":"decision"},
                      {"id":"approve","label":"Manager approval","kind":"activity"},
                      {"id":"deny","label":"Reject refund","kind":"activity"}],
        "transitions":[{"source":"review","target":"approve","rule_id":"big"},
                       {"source":"review","target":"deny","rule_id":"small"}],
        "entry_activity_id":"review","terminal_activity_ids":["approve","deny"],
        "business_rules":[{"id":"big","text":"Refund amount above 500 EUR needs manager approval"},
                          {"id":"small","text":"Refund amount of 500 EUR or less is paid directly",
                           "user_confirmed":True}]
    }
}


def family(key="refund",resolution="unique_active"):
    return {"family_id":key,"label":key,"resolution":resolution}


def version(key,start,end=None,*,family_id="refund",priority=None,text=None):
    return {"version_id":key,"family_id":family_id,"title":key,
            "text":text or "A refund amount above 500 EUR must get manager approval.",
            "effective_from":start,"effective_until":end,"priority":priority}


def payload(versions,families=None,start="2024-01-01",end="2024-02-01"):
    return {"expected_content_sha256":PIN,"start_date":start,"end_date_exclusive":end,
            "families":families or [family()],"versions":versions}


def date_review(p,day,previous=None,**extra):
    t=ep.timeline(RECORD,p)
    return ep.review(RECORD,{**p,"expected_timeline_sha256":t["timeline_sha256"],
                             "as_of":day,"compare_to":previous,**extra})


def test_half_open_revision_boundary_and_exact_days():
    p=payload([version("old","2023-01-01","2024-01-15"),
               version("new","2024-01-15")])
    result=ep.timeline(RECORD,p)
    assert result["window_days"]==31
    assert result["coverage_days"]=={"resolved_under_declared_assumptions":31,"incomplete":0,"ambiguous":0}
    assert [s["days"] for s in result["segments"]]==[14,17]
    assert [s["selected_version_ids"] for s in result["segments"]]==[["old"],["new"]]
    assert result["transitions"][0]["date"]=="2024-01-15"
    assert result["transitions"][0]["changed_families"][0]["entered_active"]==["new"]
    assert result["transitions"][0]["changed_families"][0]["left_active"]==["old"]
    assert date_review(p,"2024-01-15")["selected_segment"]["selected_version_ids"]==["new"]
    with pytest.raises(ValueError,match="inside"):
        date_review(p,"2024-02-01")


def test_gaps_ambiguity_and_missing_required_family():
    p=payload([version("old","2024-01-03","2024-01-12"),
               version("new","2024-01-10","2024-01-20")],
              [family(),family("finance")])
    r=ep.timeline(RECORD,p)
    assert r["coverage_days"]=={"resolved_under_declared_assumptions":0,"incomplete":29,"ambiguous":2}
    assert r["family_coverage"][0]["coverage_days"]["gap"]==13
    assert r["family_coverage"][1]["coverage_days"]["gap"]==31
    assert date_review(p,"2024-01-10")["status"]=="ambiguous"
    assert date_review(p,"2024-01-10")["clarification"] is None
    assert date_review(p,"2024-01-13")["status"]=="incomplete"


def test_explicit_priority_never_hides_overlap_and_ties_block():
    p=payload([version("base","2024-01-01",priority=1),
               version("override","2024-01-05","2024-01-20",priority=9),
               version("tie","2024-01-10","2024-01-12",priority=9)],
              [family(resolution="explicit_priority")])
    r=ep.timeline(RECORD,p)
    c=r["family_coverage"][0]["coverage_days"]
    assert c["overlap_resolved_by_declared_priority"]==13
    assert c["ambiguous_priority_tie"]==2
    assert c["unique_active"]==16
    row=date_review(p,"2024-01-05")["selected_segment"]["families"][0]
    assert row["selected_version_id"]=="override"
    assert row["shadowed_version_ids"]==["base"]
    assert date_review(p,"2024-01-10")["clarification"] is None


def test_independent_daily_oracle_for_random_intervals():
    rng=random.Random(92071)
    start=date(2024,1,1)
    for _ in range(120):
        fams=[family("a"),family("b","explicit_priority")]
        rows=[]
        for i in range(rng.randint(1,12)):
            f=rng.choice(fams)
            a=rng.randint(-10,36); b=a+rng.randint(1,30)
            rows.append(version("v"+str(i),(start+timedelta(days=a)).isoformat(),
                                (start+timedelta(days=b)).isoformat() if rng.random()<.8 else None,
                                family_id=f["family_id"],
                                priority=rng.randint(0,3) if f["resolution"]=="explicit_priority" else None))
        r=ep.timeline(RECORD,payload(rows,fams))
        expected=Counter()
        family_counts={f["family_id"]:Counter() for f in fams}
        for n in range(31):
            d=(start+timedelta(days=n)).isoformat()
            current=next(s for s in r["segments"] if s["start_date"]<=d<s["end_date_exclusive"])
            selected=[]
            for f,actual in zip(fams,current["families"]):
                active=[v for v in rows if v["family_id"]==f["family_id"] and
                        v["effective_from"]<=d and (not v["effective_until"] or d<v["effective_until"])]
                winner=None
                if not active: status="gap"
                elif len(active)==1: status="unique_active";winner=active[0]
                elif f["resolution"]=="unique_active": status="ambiguous_overlap"
                else:
                    top=[v for v in active if all(v["priority"]>=x["priority"] for x in active)]
                    if len(top)==1:status="overlap_resolved_by_declared_priority";winner=top[0]
                    else:status="ambiguous_priority_tie"
                family_counts[f["family_id"]][status]+=1
                assert actual["status"]==status
                assert actual["active_version_ids"]==sorted(v["version_id"] for v in active)
                assert actual["selected_version_id"]==(winner["version_id"] if winner else None)
                if winner:selected.append(winner["version_id"])
            statuses=[f["status"] for f in current["families"]]
            expected["ambiguous" if any(s.startswith("ambiguous") for s in statuses)
                     else "incomplete" if "gap" in statuses else "resolved_under_declared_assumptions"]+=1
            assert current["selected_version_ids"]==selected
        assert sum(s["days"] for s in r["segments"])==31
        assert all(r["coverage_days"][k]==expected[k] for k in r["coverage_days"])
        for f in r["family_coverage"]:
            assert all(v==family_counts[f["family_id"]][k] for k,v in f["coverage_days"].items())


def test_date_comparison_exact_questions_and_statement_delta():
    old=version("old","2024-01-01","2024-01-15")
    new=version("new","2024-01-15",text="A refund amount above 1000 EUR must get manager approval.")
    p=payload([old,new])
    r=date_review(p,"2024-01-15","2024-01-14",question_limit=1)
    c=r["clarification"];d=r["comparison"]
    assert c["question_count"]>1
    assert d["status"]=="compared"
    assert d["indexed_statement_occurrence_counts"]["added"]==1
    assert d["indexed_statement_occurrence_counts"]["removed"]==1
    assert d["question_ids_changed_evidence"]
    assert d["question_disappearance_is_not_resolution"]
    assert not r["prior_decisions_imported"]
    assert not r["model_changed"] and not r["execution_authorized"]
    second=date_review(p,"2024-01-15","2024-01-14",question_offset=1,
                       question_limit=1,expected_review_sha256=r["review_sha256"])
    assert second["review_sha256"]==r["review_sha256"]
    assert second["clarification"]["question_page"]["rows"][0]["question_id"]!=c["question_page"]["rows"][0]["question_id"]
    assert c["documents_sha256"]==r["selected_segment"]["documents_sha256"]


def test_unresolved_comparison_and_unchanged_generated_evidence():
    p=payload([version("old","2024-01-02","2024-01-10"),
               version("new","2024-01-10")])
    assert date_review(p,"2024-01-05","2024-01-01")["comparison"]["status"]=="comparison_date_unresolved"
    r=date_review(p,"2024-01-06","2024-01-05")["comparison"]
    assert not r["question_ids_new"] and not r["question_ids_changed_evidence"]
    assert len(r["question_ids_same_generated_evidence"])==r["candidate_question_count"]


def test_manifest_omits_text_and_exact_pin_detects_changes():
    secret="UNIQUE_PRIVATE_POLICY_TEXT. A refund amount must get manager approval."
    p=payload([version("one","2024-01-01",text=secret)])
    t=ep.timeline(RECORD,p)
    assert secret not in json.dumps(t) and "UNIQUE_PRIVATE_POLICY_TEXT" not in json.dumps(t)
    edited=deepcopy(p);edited["versions"][0]["text"]+=" "
    assert ep.timeline(RECORD,edited)["timeline_sha256"]!=t["timeline_sha256"]
    with pytest.raises(ValueError,match="timeline changed"):
        ep.review(RECORD,{**edited,"expected_timeline_sha256":t["timeline_sha256"],"as_of":"2024-01-01"})
    first=date_review(p,"2024-01-01")
    with pytest.raises(ValueError,match="date review changed"):
        date_review(p,"2024-01-02",question_offset=1,expected_review_sha256=first["review_sha256"])
    with pytest.raises(ValueError,match="first question page"):
        date_review(p,"2024-01-01",question_offset=1)
    p2=payload([version("one","2024-01-01"),version("outside","2025-01-01")])
    assert ep.timeline(RECORD,p2)["versions_never_active_in_window"]==["outside"]
    assert ep.timeline(RECORD,p2)["timeline_sha256"]==ep.timeline(RECORD,{**p2,"versions":list(reversed(p2["versions"]))})["timeline_sha256"]


@pytest.mark.parametrize("kind",["unknown","duplicate_version","duplicate_family","invalid_date","reverse_interval",
    "zero_window","too_long","missing_priority","unexpected_priority","bool_priority","unlisted_family","empty_text","wrong_pin","surrogate"])
def test_invalid_or_unpinned_inputs(kind):
    p=payload([version("one","2024-01-01")])
    if kind=="unknown":p["execute"]=True
    elif kind=="duplicate_version":p["versions"]*=2
    elif kind=="duplicate_family":p["families"]*=2
    elif kind=="invalid_date":p["versions"][0]["effective_from"]="2024-02-30"
    elif kind=="reverse_interval":p["versions"][0]["effective_until"]="2023-12-31"
    elif kind=="zero_window":p["end_date_exclusive"]=p["start_date"]
    elif kind=="too_long":p["end_date_exclusive"]="2031-01-01"
    elif kind=="missing_priority":p["families"][0]["resolution"]="explicit_priority"
    elif kind=="unexpected_priority":p["versions"][0]["priority"]=5
    elif kind=="bool_priority":p["versions"][0]["priority"]=True
    elif kind=="unlisted_family":p["versions"][0]["family_id"]="unknown"
    elif kind=="empty_text":p["versions"][0]["text"]=" "
    elif kind=="wrong_pin":p["expected_content_sha256"]="b"*64
    else:p["versions"][0]["text"]="\ud800"
    with pytest.raises((ValueError,ValidationError)):
        ep.timeline(RECORD,p)


def test_real_http_dashboard_session_read_only_and_exact_saved_revision(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import workflow_evidence_routes as routes,local_source_routes
    monkeypatch.setattr(routes,"_catalog_record",lambda *_:RECORD)
    monkeypatch.setattr(local_source_routes,"_require_dashboard",lambda r: (
        None if r.headers.get("authorization")=="OWG-Session local" else
        (_ for _ in ()).throw(__import__("fastapi").HTTPException(403,"session required"))))
    app=FastAPI()
    app.add_api_route("/timeline",routes.review_effective_policy_timeline,methods=["POST"])
    app.add_api_route("/date",routes.review_effective_policy_date,methods=["POST"])
    query="?workflow_id=refund_process&revision=1"
    p=payload([version("one","2024-01-01")])
    owner={"Authorization":"OWG-Session local"}
    with TestClient(app) as client:
        assert client.post("/timeline"+query,json=p).status_code==403
        assert client.post("/timeline"+query,json=p,headers={"Authorization":"Bearer api"}).status_code==403
        response=client.post("/timeline"+query,json=p,headers=owner)
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        body={**p,"expected_timeline_sha256":response.json()["timeline_sha256"],"as_of":"2024-01-02"}
        review=client.post("/date"+query,json=body,headers=owner)
        assert review.status_code==200,review.text
        assert review.headers["cache-control"]=="no-store"
        assert not review.json()["ledger_changed"]
        body["expected_timeline_sha256"]="b"*64
        assert client.post("/date"+query,json=body,headers=owner).status_code==409
