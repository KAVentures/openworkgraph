"""Committed request-window coverage: real retained transactions and exact intervals."""
from datetime import timedelta
import json
import random

import httpx
import pytest

from server import enterprise_ingestion_coverage as coverage
from server import enterprise_ingestion_run_history as history
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from test_enterprise_rest_feed import config, event, page, run, URL, ENV, TOKEN


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS",raising=False)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES",raising=False)


def profile(**kw):
    return rest.create(config(initial_since=rest._iso(rest._now()-timedelta(days=4)),**kw))


def request(profiles,start,end):
    return {"targets":[{"profile_id":p["profile_id"],"revision":p["revision"]} for p in profiles],
            "since":rest._iso(start),"until":rest._iso(end)}


def stored(p):
    with ws._LOCK:
        c=rest._connect()
        try:
            return coverage.load(c,p["profile_id"])
        finally:
            c.close()


def test_committed_overlap_union_and_empty_windows_survive_history_pruning(monkeypatch):
    p=profile()
    start=rest._stamp(p["config"]["initial_since"])
    monkeypatch.setattr(history,"MAX_PER_PROFILE",1)
    first=event(p,changed=start+timedelta(hours=23,minutes=30))
    p=run(p,lambda _:page([first]))
    p=run(p,lambda _:page([first]))
    p=run(p,lambda _:page([]))
    assert stored(p)["recorded_commits"]==3
    assert stored(p)["intervals"]==[[rest._iso(start),rest._iso(start+timedelta(days=3))]]
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==1
    result=coverage.review(request([p],start-timedelta(hours=6),start+timedelta(hours=78)))
    row=result["profiles"][0]
    assert [x["kind"] for x in row["segments"]]==["before_configured_anchor","committed_request_window","not_yet_checkpointed"]
    assert row["microseconds_by_kind"]["committed_request_window"]==72*3600*1000000
    assert result["summary"]["committed_request_window_fraction"]==72/84
    assert not result["provider_completeness_verified"]
    assert ws.list_logs()["logs"][0]["events"]==1


def test_pre_upgrade_checkpoint_is_unknown_not_inferred():
    p=profile()
    start=rest._stamp(p["config"]["initial_since"])
    # Simulate the legacy runtime with a checkpoint but no coverage ledger.
    p=run(p,lambda _:page([]))
    with ws._LOCK:
        c=rest._connect()
        try:
            coverage.erase(c,p["profile_id"])
        finally:
            c.close()
    prior=coverage.review(request([p],start,start+timedelta(days=2)))["profiles"][0]
    assert prior["segments"][0]["kind"]=="unknown_prior_coverage"
    p=run(p,lambda _:page([]))
    after=coverage.review(request([p],start,start+timedelta(days=2)))["profiles"][0]
    assert after["segments"][0]["kind"]=="unknown_prior_coverage"
    assert after["segments"][0]["until"]==rest._iso(start+timedelta(hours=23))
    assert after["segments"][1]["kind"]=="committed_request_window"
    assert stored(p)["recorded_commits"]==1


def test_failed_and_rolled_back_retention_never_claim_coverage(monkeypatch):
    p=profile()
    with pytest.raises(rest.FeedError):
        run(p,lambda _:httpx.Response(502,text="private provider payload"))
    assert stored(p)==coverage.empty()
    original=history.finish
    def fail(c,pid,rid,state,**kwargs):
        original(c,pid,rid,state,**kwargs)
        if state=="succeeded":
            raise RuntimeError("injected post-coverage precommit failure")
    monkeypatch.setattr(history,"finish",fail)
    with pytest.raises(rest.FeedError):
        run(p,lambda _:page([event(p)]))
    assert stored(p)==coverage.empty()
    assert ws.list_logs()["logs"]==[]
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_legacy_inflight_without_admission_row_creates_no_coverage():
    p=profile()
    admitted,_,until=rest._begin(p["profile_id"],p["revision"],rest._now())
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("DELETE FROM enterprise_ingestion_runs")
        finally:
            c.close()
    rest._retain(admitted,[],{"pages":1},until)
    assert stored(p)==coverage.empty()
    assert rest.inventory()["profiles"][0]["checkpoint"]


def test_review_passive_multi_profile_exact_denominator_and_no_payload(monkeypatch):
    p,q=profile(name="first"),profile(name="second")
    p=run(p,lambda _:page([event(p)]))
    start=rest._stamp(p["config"]["initial_since"])
    end=start+timedelta(days=2)
    monkeypatch.setattr(rest,"_check_binding",lambda *a:pytest.fail("unexpected credential read"))
    monkeypatch.setattr(httpx.Client,"stream",lambda *a,**k:pytest.fail("unexpected provider read"))
    result=coverage.review(request([p,q],start,end))
    assert result["summary"]["included_profiles"]==2
    assert result["summary"]["profile_window_microseconds"]==96*3600*1000000
    assert sum(result["summary"]["microseconds_by_kind"].values())==96*3600*1000000
    assert result["summary"]["committed_request_window_fraction"]==0.25
    assert result["execution_authorized"] is False
    assert all(x not in json.dumps(result) for x in (TOKEN,URL,"raw-event","raw-case","raw-user","never-retain-me"))


def test_deletion_and_inconsistent_index():
    p=run(profile(),lambda _:page([]))
    q=profile()
    q=run(q,lambda _:page([event(q)]))
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("UPDATE enterprise_ingestion_coverage SET definition=? WHERE profile_id=?",
                      ['{"intervals":[["bad","bad"]]}',p["profile_id"]])
        finally:
            c.close()
    start=rest._stamp(p["config"]["initial_since"])
    result=coverage.review(request([p,q],start,start+timedelta(days=2)))
    assert result["summary"]["excluded_inconsistent_profiles"]==1
    assert next(r for r in result["profiles"] if r["profile_id"]==p["profile_id"])["segments"]==[]
    rest.delete(p["profile_id"],p["revision"])
    ws.delete_log(q["log_id"])
    assert stored(p)==coverage.empty() and stored(q)==coverage.empty()


def test_exact_interval_partition_randomized_against_discrete_oracle():
    rng=random.Random(2307)
    origin=rest._now()-timedelta(days=1)
    for _ in range(300):
        raw=[]
        for _ in range(rng.randrange(12)):
            a=rng.randrange(40);b=rng.randrange(a+1,41)
            raw.append((origin+timedelta(microseconds=a),origin+timedelta(microseconds=b)))
        intervals=coverage.union(raw)
        initial=origin+timedelta(microseconds=5)
        checkpoint=origin+timedelta(microseconds=30)
        segments=coverage.partition(intervals,origin,origin+timedelta(microseconds=40),initial,checkpoint)
        assert sum(x["microseconds"] for x in segments)==40
        for i in range(40):
            t=origin+timedelta(microseconds=i)
            expected=("committed_request_window" if any(a<=t<b for a,b in raw)
                      else "before_configured_anchor" if t<initial
                      else "unknown_prior_coverage" if t<checkpoint
                      else "not_yet_checkpointed")
            actual=next(x for x in segments if rest._stamp(x["since"])<=t<rest._stamp(x["until"]))
            assert actual["kind"]==expected


def test_bad_ranges_and_stale_profile():
    p=profile()
    start=rest._stamp(p["config"]["initial_since"])
    with pytest.raises(rest.FeedError,match="range"):
        coverage.review(request([p],start,start))
    with pytest.raises(rest.FeedError,match="range"):
        coverage.review(request([p],start-timedelta(days=400),start))
    with pytest.raises(rest.FeedError,match="invalid_coverage_review"):
        coverage.review(request([p,p],start,start+timedelta(days=1)))
    rest.change(p["profile_id"],p["revision"],False)
    with pytest.raises(rest.FeedError,match="profile_changed"):
        coverage.review(request([p],start,start+timedelta(days=1)))
