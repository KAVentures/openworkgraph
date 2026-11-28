"""R10 rule-level retained history: exact independent oracle and real workspace."""
from datetime import datetime, timedelta
from itertools import groupby
from random import Random
from statistics import median
import json

import pytest

from shared import signal_rule_history as core
from server import event_log_signal_episode_review as episodes
from server import event_log_signal_rule_history as history
from server import event_log_workspace as workspace
from server import event_log_signals as signals
from test_event_log_signal_timeline import _log, _add, isolate

BASE = datetime(2026,1,1)


def t(us):
    return BASE + timedelta(microseconds=us)


def raw(events, key, start_id=0):
    return [(key[0],"same log name",key[1],json.dumps({"name":"same rule name"}),t(at),
             f"sevt_{i+start_id:024x}","triggered" if kind=="T" else "cleared")
            for i,(at,kind) in enumerate(events)]


def oracle(events,end,deadline,recurrence):
    # Inspect each trigger's immediate successor group, and each clearance's
    # immediate predecessor. A timestamp tie never establishes chronology.
    groups=[(at,[kind for _,kind in g]) for at,g in groupby(events,key=lambda x:x[0])]
    counts=dict.fromkeys(core.COUNTS,0);durations=[];gaps=[]
    for i,(at,kinds) in enumerate(groups):
        counts["retained_triggers"]+=kinds.count("T");counts["retained_clearances"]+=kinds.count("C")
        if len(kinds)>1:
            counts["ambiguous_triggers"]+=kinds.count("T");counts["ambiguous_clearances"]+=kinds.count("C");continue
        if kinds==["T"]:
            if i+1==len(groups):
                counts["unresolved_window_past_deadline" if end-at>deadline else "unresolved_window_not_past_deadline"]+=1
            elif groups[i+1][1]==["C"]:
                elapsed=groups[i+1][0]-at;durations.append(elapsed)
                counts["cleared_within_deadline" if elapsed<=deadline else "cleared_after_deadline"]+=1
            else:
                counts["interrupted_triggers"]+=1
            if i>=2 and groups[i-1][1]==["C"] and groups[i-2][1]==["T"]:
                gap=at-groups[i-1][0];gaps.append(gap);counts["observed_retrigger_pairs"]+=1
                counts["retrigger_within_horizon"]+=int(gap<=recurrence)
        elif not i or groups[i-1][1]!=["T"]:
            counts["leading_clearances"]+=1
    return counts,durations,gaps


def test_exact_thresholds_and_ties():
    events=[(0,"C"),(1,"T"),(1000001,"C"),(2000001,"T"),(3000002,"C"),
            (4000002,"T"),(5000002,"T"),(5000002,"C"),(6000002,"T")]
    r=core.summarize(episodes.reconstruct(raw(events,("L","R"))),t(7000002),1,1)[0]
    assert r["cleared_within_deadline"]==1 and r["cleared_after_deadline"]==1
    assert r["retrigger_within_horizon"]==2 and r["interrupted_triggers"]==1
    assert r["ambiguous_triggers"]==1 and r["ambiguous_clearances"]==1
    assert r["unresolved_window_not_past_deadline"]==1
    assert r["clearance_elapsed"]["median_us"]==1000000.5


def test_random_exact_oracle():
    rng=Random(723)
    for _ in range(600):
        deadline=rng.randint(1,8);horizon=rng.randint(1,8)
        rules=[];expected={};end=150000000
        for idx,key in enumerate([("L1","R"),("L2","R"),("L1","R2")]):
            at=0;events=[]
            for i in range(rng.randint(1,30)):
                at+=rng.randint(0,4)*1000000+rng.choice([0,0,1]);events.append((at,rng.choice("TC")))
            rules.extend(raw(events,key,idx*100));expected[key]=oracle(events,end,deadline*1000000,horizon*1000000)
        rules.sort(key=lambda r:(r[0],r[2],r[4],r[5]))
        reconstructed=episodes.reconstruct(rules)
        rng.shuffle(reconstructed)
        result=core.summarize(reconstructed,t(end),deadline,horizon)
        assert len(result)==3
        for row in result:
            c,d,g=expected[(row["log_id"],row["rule_id"])]
            assert {k:row[k] for k in core.COUNTS}==c
            for vals,stats in [(d,row["clearance_elapsed"]),(g,row["adjacent_retrigger_gap"])]:
                assert stats==dict(count=len(vals),minimum_us=min(vals) if vals else None,
                    median_us=median(vals) if vals else None,maximum_us=max(vals) if vals else None)
        assert core.totals(result)=={k:sum(v[0][k] for v in expected.values()) for k in core.COUNTS}
        assert core.ordered(result,"volume")==sorted(result,key=lambda r:(-r["retained_triggers"],r["log_id"],r["rule_id"]))


def _seed():
    a,b=_log(),_log()
    ar,br=_add(a,"Same rule name"),_add(b,"Same rule name")
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            events=[(a,ar,i,h,k) for i,(h,k) in enumerate([(0,"triggered"),(1,"cleared"),(2,"triggered"),(5,"cleared"),(6,"triggered")])]
            events.extend((b,br,100+i,h,k) for i,(h,k) in enumerate([(0,"cleared"),(1,"triggered"),(1,"cleared")]))
            conn.executemany("INSERT INTO signal_transitions VALUES (?,?,?,?,?,?)",
                [[log,rule,f"sevt_{i:024x}",BASE+timedelta(hours=h),kind,"a"*64] for log,rule,i,h,kind in events])
        finally:
            conn.close()
    return a,ar,b,br


ARGS={"start_date":"2026-01-01","end_date":"2026-01-02","deadline_seconds":3600,"recurrence_seconds":3600}


def test_real_population_paging_scope_and_stale_pin():
    a,ar,b,br=_seed()
    first=history.review(**ARGS,limit=1)
    assert first["all_rule_count"]==2 and first["next_offset"]==1
    assert first["all_totals"]["retained_triggers"]==4
    assert first["all_totals"]["retained_clearances"]==4
    assert first["rows"][0]["log_id"]==a
    assert first["rows"][0]["cleared_within_deadline"]==1
    assert first["rows"][0]["cleared_after_deadline"]==1
    assert first["rows"][0]["retrigger_within_horizon"]==2
    assert first["rows"][0]["unresolved_window_past_deadline"]==1
    next_page=history.review(**ARGS,limit=1,offset=1,expected_review_sha256=first["review_sha256"])
    assert next_page["rows"][0]["log_id"]==b and next_page["next_offset"] is None
    scoped=history.review(**ARGS,log_id=b)
    assert scoped["matching_rule_count"]==1 and scoped["matching_totals"]["ambiguous_triggers"]==1
    assert scoped["all_totals"]==first["all_totals"]
    source=episodes.review(start_date=ARGS["start_date"],end_date=ARGS["end_date"],log_id=a,rule_id=ar,
                           expected_review_sha256=first["source_episode_review_sha256"])
    assert source["page"]["matching_rows"]==3
    assert not first["actions_authorized"] and not first["evaluations_performed"]
    assert "state_fingerprint" not in json.dumps(first)
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        history.review(**{**ARGS,"deadline_seconds":7200},expected_review_sha256=first["review_sha256"])
    signals.delete_rule(a,ar)
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        history.review(**ARGS,expected_review_sha256=first["review_sha256"])


@pytest.mark.parametrize("changes",[
    {"deadline_seconds":True},{"deadline_seconds":0},{"recurrence_seconds":31622401},
    {"sort":"unknown"},{"log_id":"bad path"},{"offset":1},{"limit":101},
    {"expected_review_sha256":"bad"},{"end_date":"9999-12-31"},{"start_date":"2027-01-01"},
])
def test_bad_requests(changes):
    with pytest.raises(workspace.EventLogWorkspaceError):
        history.review(**{**ARGS,**changes})


def test_budget_empty_and_dashboard_auth(monkeypatch):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from server import event_log_routes as routes
    empty=history.review(**ARGS)
    assert empty["all_rule_count"]==0 and empty["rows"]==[]
    _seed()
    monkeypatch.setattr(episodes,"MAX_TRANSITIONS",0)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):
        history.review(**ARGS)
    monkeypatch.setattr(episodes,"MAX_TRANSITIONS",100000)
    def auth(request):
        if request.headers.get("authorization")!="OWG-Session test":
            raise HTTPException(403,"session required")
    monkeypatch.setattr(routes,"_require_dashboard",auth)
    app=FastAPI();app.include_router(routes.router)
    with TestClient(app) as c:
        endpoint="/v1/event-logs/signal-rule-history"
        assert c.get("/signal-rule-history.js").status_code==200
        assert c.get(endpoint,params=ARGS).status_code==403
        assert c.get(endpoint,params=ARGS,headers={"Authorization":"Bearer api"}).status_code==403
        response=c.get(endpoint,params=ARGS,headers={"Authorization":"OWG-Session test"})
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        assert response.json()["all_rule_count"]==2
