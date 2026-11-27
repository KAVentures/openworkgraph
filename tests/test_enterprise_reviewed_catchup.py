"""Reviewed catch-up queue exercises the real retained ingestion boundary."""
from datetime import timedelta
import json
import time

import httpx
import pytest

from server import enterprise_reviewed_catchup as catchup
from server import enterprise_rest_feed as rest
from server import enterprise_ingestion_coverage as coverage
from server import enterprise_ingestion_run_history as history
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from test_enterprise_rest_feed import config,event,page,provider,run,URL,ENV,TOKEN


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS",raising=False)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES",raising=False)


def profile(days=4,**changes):
    return rest.create(config(initial_since=rest._iso(rest._now()-timedelta(days=days)),**changes))


def preview(*profiles,now=None):
    return catchup.preview({"targets":[{"profile_id":p["profile_id"],"revision":p["revision"]} for p in profiles]},now=now)


def approve(row,**changes):
    return {**{k:row[k] for k in ("profile_id","revision","reviewed_at","since","until","plan_sha256")},
            "confirm_exact_window":True,"confirm_provider_read":True,"confirm_retention":True,
            "confirm_competing_workers_reconciled":True,**changes}


def current(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])


def mutate(p,**changes):
    with ws._LOCK:
        c=rest._connect()
        try:
            live=rest._get(c,p["profile_id"])
            rest._put(c,{**live,**changes})
        finally:
            c.close()


def test_passive_multi_profile_queue_and_independent_approval(monkeypatch):
    p,q,r=profile(5),profile(3),profile(2)
    r=rest.change(r["profile_id"],r["revision"],False)
    with monkeypatch.context() as m:
        m.setattr(httpx.Client,"stream",lambda *a,**k:pytest.fail("unexpected provider read"))
        m.setattr(rest,"_extract_window",lambda *a:pytest.fail("unexpected provider extraction"))
        plan=preview(q,r,p)
    assert plan["eligible_count"]==2 and plan["blocked_count"]==1
    assert [x["profile_id"] for x in plan["targets"]]==[p["profile_id"],q["profile_id"],r["profile_id"]]
    assert plan["targets"][-1]["reason"]=="feed_disabled"
    assert not plan["provider_reads_performed"] and not plan["execution_authorized"]
    result=catchup.execute(approve(plan["targets"][0]),client_factory=provider(lambda _:page([])))
    assert result["checkpoint"]==plan["targets"][0]["until"]
    assert current(q)["checkpoint"] is None
    assert current(r)["checkpoint"] is None
    assert not result["schedule_modified"]
    assert TOKEN not in json.dumps(plan) and URL not in json.dumps(plan)


def test_frozen_exact_window_even_if_current_clock_allows_more():
    p=profile(days=0.25)
    reviewed=rest._now()
    row=preview(p,now=reviewed)["targets"][0]
    assert row["eligible"] and row["until"]==rest._iso(reviewed)
    seen=[]
    def fetch(req):
        seen.append((req.url.params["updated_since"],req.url.params["updated_before"]))
        return page([event(p)])
    result=catchup.execute(approve(row),client_factory=provider(fetch),
                           now=reviewed+timedelta(minutes=3))
    assert seen==[(row["since"],row["until"])]
    assert result["events_added"]==1 and result["checkpoint"]==row["until"]
    with ws._LOCK:
        c=rest._connect()
        try:
            assert coverage.load(c,p["profile_id"])["intervals"]==[[row["since"],row["until"]]]
        finally:
            c.close()
    with pytest.raises(rest.FeedError):
        catchup.execute(approve(row),client_factory=provider(lambda _:pytest.fail("stale review read")),
                        now=reviewed+timedelta(minutes=4))


def test_review_expiry_and_clock_regression_refuse_before_admission():
    p=profile()
    reviewed=rest._now()
    row=preview(p,now=reviewed)["targets"][0]
    for now in (reviewed-timedelta(seconds=1),reviewed+timedelta(seconds=600)):
        with pytest.raises(rest.FeedError,match="catchup_review_expired"):
            catchup.execute(approve(row),now=now,client_factory=provider(lambda _:pytest.fail("expired review read")))
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==0


def test_tampered_plan_or_window_refuses_without_read():
    p=profile()
    row=preview(p)["targets"][0]
    for patch in ({"plan_sha256":"a"*64},{"until":rest._iso(rest._stamp(row["until"])-timedelta(seconds=1))}):
        with pytest.raises(rest.FeedError,match="catchup_review"):
            catchup.execute(approve(row,**patch),client_factory=provider(lambda _:pytest.fail("tampered review read")))
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==0


@pytest.mark.parametrize("flag",["confirm_exact_window","confirm_provider_read","confirm_retention","confirm_competing_workers_reconciled"])
def test_separate_approval_required(flag):
    p=profile()
    row=preview(p)["targets"][0]
    with pytest.raises(rest.FeedError,match="explicit_catchup_window_approval_required"):
        catchup.execute(approve(row,**{flag:False}),client_factory=provider(lambda _:pytest.fail("unapproved read")))


def test_competing_dispatch_and_revision_change_fence_review():
    p=profile()
    row=preview(p)["targets"][0]
    run(p,lambda _:page([]))
    with pytest.raises(rest.FeedError):
        catchup.execute(approve(row),client_factory=provider(lambda _:pytest.fail("stale read")))
    live=current(p)
    new=preview(live)["targets"][0]
    rest.change(live["profile_id"],live["revision"],False)
    with pytest.raises(rest.FeedError):
        catchup.execute(approve(new),client_factory=provider(lambda _:pytest.fail("disabled read")))
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==1


def test_active_expired_and_historical_intents_are_not_dispatched():
    p=profile()
    raw,_,_=rest._begin(p["profile_id"],p["revision"],rest._now())
    assert preview(p)["targets"][0]["reason"]=="active_intent"
    mutate(p,lease_until=time.time()-1)
    assert preview(p)["targets"][0]["reason"]=="expired_intent_requires_review"
    mutate(p,historical_active={"request_id":"a"*32})
    assert preview(p)["targets"][0]["reason"]=="historical_intent_unreconciled"


def test_recovery_hold_cooldown_and_binding_failure_are_blocked(monkeypatch):
    p=profile()
    mutate(p,integrity_hold={"at":rest._iso(rest._now())})
    assert preview(p)["targets"][0]["reason"]=="integrity_hold"
    mutate(p,integrity_hold=None,retry_not_before=time.time()+3600)
    assert preview(p)["targets"][0]["reason"]=="retry_cooldown"
    mutate(p,retry_not_before=0)
    monkeypatch.delenv(ENV)
    assert preview(p)["targets"][0]["reason"]=="local_binding_unavailable"


def test_provider_cutoff_changes_refuse_without_admission(monkeypatch):
    p=profile()
    original=rest._next_window
    reviewed=rest._now()
    row=preview(p,now=reviewed)["targets"][0]
    def changed(p,now):
        since,until=original(p,now)
        return (since,until-timedelta(seconds=1)) if now>reviewed else (since,until)
    monkeypatch.setattr(rest,"_next_window",changed)
    with pytest.raises(rest.FeedError,match="catchup_current_cutoff_changed"):
        catchup.execute(approve(row),now=reviewed+timedelta(seconds=1),
                        client_factory=provider(lambda _:pytest.fail("cutoff changed")))
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==0


def test_failed_read_consumes_review_without_advancing_checkpoint():
    p=profile()
    row=preview(p)["targets"][0]
    with pytest.raises(rest.FeedError):
        catchup.execute(approve(row),client_factory=provider(lambda _:httpx.Response(503)))
    assert current(p)["checkpoint"] is None
    assert history.history(profile_id=p["profile_id"])["runs"][0]["state"]=="failed"
    with pytest.raises(rest.FeedError):
        catchup.execute(approve(row),client_factory=provider(lambda _:pytest.fail("consumed review read")))


def test_duplicate_selection_and_stale_revision_refused():
    p=profile()
    with pytest.raises(rest.FeedError,match="invalid_catchup_request"):
        preview(p,p)
    rest.change(p["profile_id"],p["revision"],False)
    with pytest.raises(rest.FeedError,match="profile_changed"):
        preview(p)
