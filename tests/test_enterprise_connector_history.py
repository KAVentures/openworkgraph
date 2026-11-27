"""Historical reconciliation uses the real retained event-log transaction."""
from datetime import timedelta
import json
import time

import httpx
import pytest

from server import enterprise_connector_history as history
from server import enterprise_rest_feed as rest
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


def completed(empty=False):
    p=rest.create(config())
    retained=run(p,lambda _:page([] if empty else [event(p)]))
    start=retained["config"]["initial_since"]
    return retained,{"revision":retained["revision"],"since":start,"until":retained["checkpoint"]}


def approve(p,window,request="a"*32,**changes):
    plan=history.preview(p["profile_id"],window)
    return {**window,"request_id":request,"expected_plan_sha256":plan["plan_sha256"],
            "confirm_historical_read":True,"confirm_retention":True,
            "confirm_workers_reconciled":True,**changes}


def execute(p,spec,handler):
    return history.execute(p["profile_id"],spec,client_factory=provider(handler))


def public(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])


def test_late_arrival_retained_with_no_live_checkpoint_or_success_rewrite():
    p,window=completed()
    spec=approve(p,window)
    late=event(p,"late-source-event")
    calls=[]
    def fetch(req):
        calls.append(req)
        assert req.url.params["updated_since"]==rest._iso(rest._stamp(window["since"]))
        assert req.url.params["updated_before"]==window["until"]
        # The prior row is a duplicate; the late-arriving row is new.
        return page([event(p),late])
    result=execute(p,spec,fetch)
    assert result["receipt"]["outcome"]=="succeeded"
    assert result["receipt"]["events_added"]==1 and result["receipt"]["duplicates_skipped"]==1
    assert result["receipt"]["checkpoint_before"]==result["receipt"]["checkpoint_after"]==p["checkpoint"]
    assert result["receipt"]["routine_checkpoint_advanced"] is False
    current=public(p)
    for k in ("checkpoint","last_success_at","last_attempt_at","last_result","status","revision","log_id"):
        assert current[k]==p[k]
    assert ws.list_logs()["logs"][0]["events"]==2
    repeated=execute(p,spec,lambda _:pytest.fail("idempotent receipt must not read provider"))
    assert repeated["replayed_receipt"] and not repeated["provider_read_performed"]
    assert repeated["receipt"]==result["receipt"] and len(calls)==1
    assert TOKEN not in json.dumps(current) and "late-source-event" not in json.dumps(current)


def test_historical_read_can_create_first_log_after_empty_routine_window():
    p,window=completed(empty=True)
    assert p["log_id"] is None
    r=execute(p,approve(p,window),lambda _:page([event(p)]))
    assert r["receipt"]["events_added"]==1
    updated=public(p)
    assert updated["log_id"] is not None
    assert updated["checkpoint"]==p["checkpoint"]
    assert updated["last_result"]==p["last_result"]
    assert ws.list_logs()["logs"][0]["events"]==1


def test_new_review_of_same_window_deduplicates_existing_rows():
    p,window=completed()
    first=execute(p,approve(p,window),lambda _:page([event(p)]))
    second=execute(p,approve(p,window,request="b"*32),lambda _:page([event(p)]))
    for result in (first,second):
        assert result["receipt"]["events_added"]==0 and result["receipt"]["duplicates_skipped"]==1
    assert len(public(p)["historical_receipts"])==2


def test_stale_plan_after_another_historical_receipt():
    p,window=completed()
    first=approve(p,window)
    stale=approve(p,window,request="b"*32)
    execute(p,first,lambda _:page([event(p)]))
    with pytest.raises(rest.FeedError,match="historical_plan_changed"):
        execute(p,stale,lambda _:pytest.fail("no source read"))


def test_stale_plan_after_routine_advance():
    p,window=completed()
    spec=approve(p,window)
    advanced=run(p,lambda _:page([]))
    assert advanced["checkpoint"]!=p["checkpoint"]
    with pytest.raises(rest.FeedError,match="historical_plan_changed"):
        execute(p,spec,lambda _:pytest.fail("no source read"))


@pytest.mark.parametrize("consent",["confirm_historical_read","confirm_retention","confirm_workers_reconciled"])
def test_separate_consent_required(consent):
    p,window=completed()
    with pytest.raises(rest.FeedError,match="explicit_historical"):
        execute(p,approve(p,window,**{consent:False}),lambda _:pytest.fail("no source read"))


def test_untraversed_or_preapproval_bounds_rejected():
    p,window=completed()
    start=rest._stamp(window["since"]);end=rest._stamp(window["until"])
    for changed in ({"since":rest._iso(start-timedelta(seconds=1))},
                    {"until":rest._iso(end+timedelta(seconds=1))},
                    {"since":window["until"]}):
        with pytest.raises(rest.FeedError,match="inside_traversed_scope"):
            history.preview(p["profile_id"],{**window,**changed})


def test_failure_rolls_back_all_pages_and_preserves_routine_receipt():
    p,window=completed()
    spec=approve(p,window)
    def fetch(req):
        if req.url.params.get("cursor"):
            return httpx.Response(503,text="private source error")
        return page([event(p,"new-but-uncommitted")],"more")
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        execute(p,spec,fetch)
    current=public(p)
    assert current["checkpoint"]==p["checkpoint"] and current["last_result"]==p["last_result"]
    assert current["historical_receipts"][0]["outcome"]=="failed"
    assert current["historical_receipts"][0]["events_added"] is None
    assert current["historical_active"] is None
    assert ws.list_logs()["logs"][0]["events"]==1
    replay=execute(p,spec,lambda _:pytest.fail("completed failed receipt is not a new attempt"))
    assert replay["replayed_receipt"] and replay["receipt"]["outcome"]=="failed"


def test_changed_event_payload_rolls_back_without_overwriting_old_event():
    p,window=completed()
    with pytest.raises(rest.FeedError):
        execute(p,approve(p,window),lambda _:page([event(p,step="different payload")]))
    assert ws.list_logs()["logs"][0]["events"]==1
    assert public(p)["historical_receipts"][0]["outcome"]=="failed"


def test_lease_fences_normal_ingestion_and_expired_reconciliation():
    p,window=completed()
    spec=approve(p,window)
    active,_=history._start(p["profile_id"],history.Execute.model_validate(spec),rest._now())
    with pytest.raises(rest.FeedError,match="historical_run_requires"):
        run(p,lambda _:pytest.fail("active historical lease must block routine ingestion"))
    reconcile={"revision":p["revision"],"request_id":spec["request_id"],
        "confirm_workers_stopped":True,"confirm_uncertain_outcome":True}
    with pytest.raises(rest.FeedError,match="lease_still_active"):
        history.reconcile(p["profile_id"],reconcile)
    with ws._LOCK:
        c=rest._connect()
        try:
            active["lease_until"]=time.time()-1
            rest._put(c,active)
        finally:
            c.close()
    assert public(p)["historical_active"]["lease_expired"]
    with pytest.raises(rest.FeedError,match="historical_run_requires"):
        run(p,lambda _:pytest.fail("expired historical intent must still fence routine ingestion"))
    result=history.reconcile(p["profile_id"],reconcile)
    assert result["receipt"]["outcome"]=="uncertain"
    assert result["receipt"]["checkpoint_after"]==p["checkpoint"]
    assert public(p)["historical_active"] is None
    assert run(p,lambda _:page([]))["checkpoint"]!=p["checkpoint"]


def test_profile_revision_change_invalidates_historical_commit():
    p,window=completed()
    spec=approve(p,window)
    def fetch(_):
        rest.change(p["profile_id"],p["revision"],False)
        return page([event(p,"must-not-commit")])
    with pytest.raises(rest.FeedError):
        execute(p,spec,fetch)
    current=public(p)
    assert not current["enabled"] and current["revision"]==p["revision"]+1
    assert current["historical_active"] is None
    assert current["historical_receipts"][0]["outcome"]=="uncertain"
    assert ws.list_logs()["logs"][0]["events"]==1


def test_request_id_cannot_be_reused_for_another_window():
    p,window=completed()
    spec=approve(p,window)
    execute(p,spec,lambda _:page([event(p)]))
    changed={**spec,"since":rest._iso(rest._stamp(window["since"])+timedelta(minutes=1))}
    with pytest.raises(rest.FeedError,match="historical_request_identity_conflict"):
        execute(p,changed,lambda _:pytest.fail("no provider read"))


def test_preview_is_passive_and_requires_completed_checkpoint():
    p=rest.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    with pytest.raises(rest.FeedError,match="completed_checkpoint"):
        history.preview(p["profile_id"],{"revision":p["revision"],"since":rest._iso(start),
                                        "until":rest._iso(start+timedelta(hours=1))})
    assert not public(p)["historical_receipts"]
