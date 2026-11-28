"""Whole-window effective policy review: independent day accounting and real source tests."""
from collections import Counter
from copy import deepcopy
from datetime import date,timedelta
from random import Random
import json

import pytest

from shared import process_effective_policy as ep
from shared import process_policy_window as window
from test_process_effective_policy import RECORD, PIN, family, version, payload


def run(p):
    t=ep.timeline(RECORD,p)
    return window.review(RECORD,{**p,"expected_timeline_sha256":t["timeline_sha256"]})


def test_repeated_bundle_gap_and_changed_evidence(monkeypatch):
    p=payload([version("a","2024-01-01","2024-01-10",priority=1),
               version("override","2024-01-04","2024-01-06",priority=9,
                       text="A refund amount above 1000 EUR must get manager approval."),
               version("later","2024-01-12",priority=1,
                       text="A refund amount above 2000 EUR must get manager approval.")],
              [family(resolution="explicit_priority")])
    actual=ep._clarify
    calls=[]
    def count(record,docs):
        calls.append(ep.clarification.documents_sha256(docs))
        return actual(record,docs)
    monkeypatch.setattr(ep,"_clarify",count)
    r=run(p)
    assert r["window_days"]==31 and r["resolved_days"]==29 and r["unresolved_days"]==2
    assert r["distinct_bundle_count"]==3 and len(set(calls))==len(calls)==3
    assert [s["days"] for s in r["intervals"]]==[3,2,4,2,20]
    assert r["intervals"][3]["question_count"] is None
    assert [x["status"] for x in r["transitions"]]==["compared","compared","not_compared_unresolved_boundary","not_compared_unresolved_boundary"]
    assert r["transitions"][0]["changed_evidence"]
    assert any(q["generated_on_every_resolved_day"] for q in r["questions"])
    assert not r["prior_decisions_imported"] and not r["execution_authorized"]
    assert "must get manager approval" not in json.dumps(r)
    assert "instruction_like_statements" not in json.dumps(r)
    assert sum(q["generated_days"] for q in r["questions"])==sum(s["days"]*(s["question_count"] or 0) for s in r["intervals"])


def test_all_unresolved_and_stale_timeline():
    p=payload([version("one","2024-01-01")],[family(),family("missing")])
    t=ep.timeline(RECORD,p)
    r=run(p)
    assert r["distinct_bundle_count"]==r["resolved_days"]==0
    assert r["unresolved_days"]==31 and r["questions"]==[]
    edited=deepcopy(p);edited["versions"][0]["text"]+=" "
    with pytest.raises(ValueError,match="timeline changed"):
        window.review(RECORD,{**edited,"expected_timeline_sha256":t["timeline_sha256"]})


def test_budget_refuses_instead_of_sampling(monkeypatch):
    p=payload([version("a","2024-01-01","2024-01-15"),version("b","2024-01-15")])
    for key in ("MAX_BUNDLES","MAX_SCAN_CHARS","MAX_MEMBERSHIPS"):
        with monkeypatch.context() as m:
            m.setattr(window,key,0)
            with pytest.raises(ValueError,match="shorten"):
                run(p)


def test_random_independent_daily_oracle():
    rng=Random(918)
    for _ in range(300):
        bundles={}
        for i in range(4):
            q={str(k):{"kind":"rule","target_id":str(k),"coverage_blocked":bool(rng.randrange(2)),
                "evidence_sha256":str(rng.randrange(3))} for k in range(6) if rng.randrange(2)}
            bundles[str(i)]={"questions":q,"coverage_complete":bool(rng.randrange(2))}
        segments=[];day=0;daily=[]
        for i in range(rng.randrange(1,15)):
            pin=rng.choice([None,*bundles]);n=rng.randrange(1,6)
            a=date(2024,1,1)+timedelta(days=day);b=a+timedelta(days=n)
            segments.append({"start_date":a.isoformat(),"end_date_exclusive":b.isoformat(),"days":n,
                "status":window.RESOLVED if pin is not None else "ambiguous",
                "selected_version_ids":[] if pin is None else [pin],"documents_sha256":pin})
            day+=n;daily.extend([pin]*n)
        r=window.summarize(segments,bundles)
        assert r["resolved_days"]==sum(p is not None for p in daily)
        assert r["unresolved_days"]==daily.count(None)
        assert r["resolved_days_with_incomplete_scan"]==sum(p is not None and not bundles[p]["coverage_complete"] for p in daily)
        expected=Counter(q for p in daily if p is not None for q in bundles[p]["questions"])
        assert {q["question_id"]:q["generated_days"] for q in r["questions"]}==expected
        for q in r["questions"]:
            k=q["question_id"]
            assert q["coverage_incomplete_days"]==sum(p is not None and k in bundles[p]["questions"] and (
                not bundles[p]["coverage_complete"] or bundles[p]["questions"][k]["coverage_blocked"]) for p in daily)
            assert q["not_generated_in_resolved_days"]==r["resolved_days"]-expected[k]
            assert q["distinct_evidence_count"]==len({bundles[p]["questions"][k]["evidence_sha256"] for p in daily
                if p is not None and k in bundles[p]["questions"]})
        for i,t in enumerate(r["transitions"]):
            before,after=(segments[j]["documents_sha256"] for j in (i,i+1))
            if before is None or after is None:
                assert t["status"]=="not_compared_unresolved_boundary" and t["new"] is None
            else:
                x,y=bundles[before]["questions"],bundles[after]["questions"]
                assert t["status"]=="compared"
                assert set(t["new"])==y.keys()-x.keys()
                assert set(t["no_longer_generated"])==x.keys()-y.keys()
                assert set(t["changed_evidence"])=={k for k in x.keys()&y.keys() if x[k]["evidence_sha256"]!=y[k]["evidence_sha256"]}


def test_http_dashboard_only_and_exact_saved_revision(monkeypatch):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from server import workflow_evidence_routes as routes,local_source_routes
    from server import process_policy_window_routes as dedicated
    monkeypatch.setattr(routes,"_catalog_record",lambda *_:RECORD)
    def auth(request):
        if request.headers.get("authorization")!="OWG-Session local":
            raise HTTPException(403,"session required")
    monkeypatch.setattr(local_source_routes,"_require_dashboard",auth)
    app=FastAPI();app.include_router(dedicated.router)
    p=payload([version("one","2024-01-01")])
    body={**p,"expected_timeline_sha256":ep.timeline(RECORD,p)["timeline_sha256"]}
    url="/v1/process-catalog/clarify/effective-window-review?workflow_id=refund_process&revision=1"
    with TestClient(app) as c:
        assert c.get("/policy-window-review.js").status_code==200
        assert c.post(url,json=body).status_code==403
        assert c.post(url,json=body,headers={"Authorization":"Bearer api"}).status_code==403
        result=c.post(url,json=body,headers={"Authorization":"OWG-Session local"})
        assert result.status_code==200,result.text
        assert result.headers["cache-control"]=="no-store"
        assert result.json()==run(p)
        assert c.post(url,json={**body,"expected_timeline_sha256":"b"*64},headers={"Authorization":"OWG-Session local"}).status_code==409
