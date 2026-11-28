from collections import Counter
from random import Random

import pytest

from shared.run_concurrency import summarize
from server import automation_monitoring as m
from server import automation_run_concurrency as concurrency
from test_automation_run_evidence import store,run,report,imported

HASH="a"*64


def test_random():
    rng=Random(471)
    for _ in range(600):
        w=rng.randint(1,35); cap=rng.randint(1,8)
        intervals=[]
        for i in range(rng.randint(0,22)):
            a=rng.randrange(w); b=rng.randint(a+1,w); intervals.append((str(i),a,b))
        r=summarize(intervals,w,cap)
        loads=[sum(a<=t<b for _,a,b in intervals) for t in range(w)]
        counts=Counter(loads); s=r["summary"]
        assert r["histogram"]==[{"concurrent_runs":k,"duration_us":v} for k,v in sorted(counts.items())]
        assert s["peak_concurrent_runs"]==max(loads)
        assert s["occupied_us"]==sum(x>0 for x in loads)
        assert s["above_capacity_us"]==sum(x>cap for x in loads)
        assert s["at_capacity_us"]==sum(x==cap for x in loads)
        assert s["excess_run_us"]==sum(max(0,x-cap) for x in loads)
        assert s["reported_run_elapsed_sum_us"]==sum(loads)
        peers={k:sum(k!=j and max(a,c)<min(b,d) for j,c,d in intervals) for k,a,b in intervals}
        assert {x["run_id"]:x["overlapping_run_count"] for x in r["runs"]}==peers
        assert s["overlapping_run_pairs"]==sum(peers.values())//2
        assert s["runs_with_overlap"]==sum(v>0 for v in peers.values())
        assert [x["concurrent_runs"] for x in r["segments"] for _ in range(x["start_us"],x["end_us"])]==loads


def test_scale():
    r=summarize([(str(i),0,100) for i in range(10000)],100,5000)
    assert r["summary"]["overlapping_run_pairs"]==49995000
    assert r["summary"]["excess_run_us"]==500000
    assert r["summary"]["peak_concurrent_runs"]==10000


@pytest.mark.parametrize("bad",[[("a",1,1)],[("a",-1,2)],[("a",0,11)],[("a",0,1),("a",1,2)]])
def test_invalid(bad):
    with pytest.raises(ValueError):summarize(bad,10,1)


def interval(rid,start,end,status="failed"):
    return {**run(rid,status,[]),"started_at":start,"finished_at":end}


def settings(**kw):
    return {"start_date":"2026-01-02","end_date":"2026-01-03","reported_spec_sha256":HASH,"capacity":1,**kw}


def test_storage_clipping_exclusions_and_paging():
    d=imported([
        interval("carry-in","2026-01-01T23:59:58Z","2026-01-02T00:00:02Z"),
        interval("nested","2026-01-02T00:00:01Z","2026-01-02T00:00:03Z"),
        interval("touch","2026-01-02T00:00:03Z","2026-01-02T00:00:04Z"),
        interval("carry-out","2026-01-02T23:59:59Z","2026-01-03T00:00:02Z"),
        interval("boundary","2026-01-01T23:59:59Z","2026-01-02T00:00:00Z"),
        interval("zero","2026-01-02T00:00:04Z","2026-01-02T00:00:04Z"),
        interval("open","2026-01-01T00:00:00Z",None,"running"),
        interval("outside","2026-01-03T00:00:00Z","2026-01-03T00:00:03Z")
    ],HASH)
    first=concurrency.review(d,settings(view="runs",limit=1))
    s=first["summary"]
    assert s["selected_records"]==7 and s["measured_intervals"]==4 and s["excluded_records"]==3
    assert s["missing_terminal_interval"]==1 and s["no_positive_intersection"]==2
    assert s["carry_in_intervals"]==s["carry_out_intervals"]==1
    assert s["peak_concurrent_runs"]==2 and s["above_capacity_us"]==1000000
    assert s["occupied_us"]==5000000 and s["reported_run_elapsed_sum_us"]=="6000000"
    assert s["overlapping_run_pairs"]==1 and s["excess_run_us"]=="1000000"
    assert not first["step_evidence_pinned"] and not first["live_concurrency_verified"]
    assert not first["execution_authorized"]
    pin=first["review_sha256"]; rows=first["rows"]; offset=first["next_offset"]
    while offset is not None:
        p=concurrency.review(d,settings(view="runs",limit=1,offset=offset,expected_review_sha256=pin))
        rows+=p["rows"];offset=p["next_offset"]
    assert {x["run_id"]:x["overlapping_run_count"] for x in rows}=={"carry-in":1,"nested":1,"touch":0,"carry-out":0}
    excluded=concurrency.review(d,settings(view="excluded"))
    assert {x["run_id"] for x in excluded["rows"]}=={"boundary","zero","open"}
    with pytest.raises(m.MonitoringError,match="changed"):
        concurrency.review(d,settings(view="runs",capacity=2,expected_review_sha256=pin))
    m.import_report(report([interval("new","2026-01-02T00:00:01Z","2026-01-02T00:00:03Z")],HASH),d)
    with pytest.raises(m.MonitoringError,match="changed"):
        concurrency.review(d,settings(view="runs",expected_review_sha256=pin))


@pytest.mark.parametrize("changes",[{"offset":1},{"capacity":True},{"capacity":0},{"limit":101},
    {"reported_spec_sha256":"bad"},{"end_date":"9999-12-31"},{"start_date":"2026-02-30"},{"view":"unknown"},{"extra":True}])
def test_strict_settings(changes):
    d=imported([run("a")],HASH)
    with pytest.raises(m.MonitoringError):concurrency.review(d,settings(**changes))


def test_empty_version_and_budget(monkeypatch):
    d=imported([run("open","running",[])],HASH)
    assert concurrency.review(d,settings(reported_spec_sha256=None))["summary"]["selected_records"]==0
    monkeypatch.setattr(concurrency,"MAX_RUNS",0)
    with pytest.raises(m.MonitoringError,match="budget"):concurrency.review(d,settings())


def test_dashboard_only_http(monkeypatch):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from server import automation_monitoring_routes as routes
    d=imported([interval("a","2026-01-02T00:00:00Z","2026-01-02T00:00:02Z")],HASH)
    monkeypatch.setattr(routes,"dashboard_session_valid",lambda token:token=="test")
    app=FastAPI();app.include_router(routes.router)
    with TestClient(app) as c:
        url="/v1/automation-monitoring/deployments/"+d+"/run-concurrency"
        assert c.get("/automation-run-concurrency.js").status_code==200
        assert c.post(url,json=settings()).status_code==403
        assert c.post(url,json=settings(),headers={"Authorization":"Bearer test"}).status_code==403
        result=c.post(url,json=settings(),headers={"Authorization":"OWG-Session test"})
        assert result.status_code==200,result.text
        assert result.headers["cache-control"]=="no-store"
        assert result.json()["summary"]["measured_intervals"]==1
