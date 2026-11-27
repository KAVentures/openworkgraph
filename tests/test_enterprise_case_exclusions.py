"""Prospective exclusions use the actual retained transaction and source identity."""
from datetime import timedelta
import json

import httpx
import pytest

from server import enterprise_case_exclusions as exclusions
from server import enterprise_rest_feed as rest
from server import enterprise_ingestion_coverage as coverage
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from test_enterprise_rest_feed import config,event,page,run,URL,ENV,TOKEN


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS",raising=False)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES",raising=False)


def profile():
    return rest.create(config(initial_since=rest._iso(rest._now()-timedelta(days=4))))


def current(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])


def request(p,*ids):
    return {"profile_id":p["profile_id"],"revision":p["revision"],
            "cases":[{"native_case_id":v} for v in ids]}


def approve(payload,plan):
    return {**payload,"plan_sha256":plan["plan_sha256"],"confirm_exact_exclusions":True,
            "confirm_prospective_only":True,"confirm_dispatch_reauthorization":True}


def set_exclusions(p,*ids):
    payload=request(p,*ids)
    plan=exclusions.preview(payload)
    assert plan["eligible"]
    result=exclusions.apply(approve(payload,plan))
    return current(p),result


def test_prospective_exclusion_retains_other_cases_and_checkpoint(monkeypatch):
    p=profile()
    before=p["revision"]
    with monkeypatch.context() as m:
        m.setattr(httpx.Client,"stream",lambda *a,**k:pytest.fail("provider read"))
        m.setattr(rest,"_check_binding",lambda *a:pytest.fail("credential read"))
        p,receipt=set_exclusions(p,"private-excluded-case")
    assert p["revision"]==before+1 and not p["enabled"]
    assert p["excluded_case_count"]==1 and p["checkpoint"] is None
    assert receipt["source_erasure_performed"] is False
    assert "private-excluded-case" not in json.dumps([receipt,p,exclusions.inventory()])
    assert rest.run_scheduled(p["profile_id"],before)[0].startswith("paused:")
    p=rest.change(p["profile_id"],p["revision"],True)
    at=rest._stamp(p["config"]["initial_since"])+timedelta(hours=23,minutes=30)
    excluded={**event(p,identity="private-event",changed=at),"case":"private-excluded-case"}
    kept={**event(p,identity="allowed-event",changed=at),"case":"allowed-case"}
    p=run(p,lambda _:page([excluded,kept]))
    assert p["events_added"]==1 and p["last_result"]["excluded_case_events_in_window"]==1
    assert ws.list_logs()["logs"][0]["events"]==1
    p=run(p,lambda _:page([excluded,kept]))
    assert p["events_added"]==0 and p["duplicates_skipped"]==1
    assert p["last_result"]["excluded_case_events_in_window"]==1
    with ws._LOCK:
        c=rest._connect()
        try:
            private=rest._get(c,p["profile_id"])
            excluded_key=rest._key(private,"case","private-excluded-case")
            assert private["excluded_case_keys"]==[excluded_key]
            assert excluded_key not in json.dumps(p)
            assert c.execute("SELECT count(*) FROM objects WHERE log_id=? AND object_id=?",
                [p["log_id"],"src:"+p["profile_id"]+":"+excluded_key]).fetchone()[0]==0
            assert coverage.load(c,p["profile_id"])["recorded_commits"]==2
        finally:
            c.close()


def test_excluding_all_rows_commits_empty_request_not_fabricated_log():
    p,_=set_exclusions(profile(),"raw-case-1")
    p=rest.change(p["profile_id"],p["revision"],True)
    p=run(p,lambda _:page([event(p)]))
    assert p["events_added"]==0 and p["log_id"] is None and p["checkpoint"]
    assert p["last_result"]["excluded_case_events_in_window"]==1
    assert ws.list_logs()["logs"]==[]


def test_existing_retained_cases_block_whole_review():
    p=profile()
    p=run(p,lambda _:page([event(p)]))
    payload=request(p,"raw-case-1","future-case")
    plan=exclusions.preview(payload)
    assert plan["retained_matches"]==1 and not plan["eligible"]
    with pytest.raises(rest.FeedError,match="ineligible"):
        exclusions.apply(approve(payload,plan))
    assert current(p)["excluded_case_count"]==0
    assert ws.list_logs()["logs"][0]["events"]==1


def test_additive_exclusion_after_retained_dataset_and_replay():
    p=profile()
    p=run(p,lambda _:page([event(p)]))
    checkpoint,log=p["checkpoint"],p["log_id"]
    p,_=set_exclusions(p,"future-case")
    assert p["checkpoint"]==checkpoint and p["log_id"]==log
    plan=exclusions.preview(request(p,"future-case"))
    assert not plan["eligible"] and plan["already_excluded_cases"]==1
    p,_=set_exclusions(p,"future-case","second-case")
    assert p["excluded_case_count"]==2
    assert ws.list_logs()["logs"][0]["events"]==1
    ws.delete_log(log)
    assert current(p)["excluded_case_count"]==0
    assert current(p)["status"]=="dataset_deleted"


def test_stale_review_when_ingestion_changes_state():
    p=profile()
    payload=request(p,"future-case")
    plan=exclusions.preview(payload)
    p=run(p,lambda _:page([]))
    with pytest.raises(rest.FeedError,match="review_stale"):
        exclusions.apply(approve(payload,plan))
    assert current(p)["excluded_case_count"]==0


def test_atomic_rollback_and_unreconciled_intent():
    p=profile()
    payload=request(p,"future-case")
    plan=exclusions.preview(payload)
    def fail(_):
        raise RuntimeError("before commit")
    with pytest.raises(RuntimeError,match="before commit"):
        exclusions.apply(approve(payload,plan),_transaction_hook=fail)
    assert current(p)["revision"]==p["revision"] and current(p)["excluded_case_count"]==0
    admitted,_,_=rest._begin(p["profile_id"],p["revision"],rest._now())
    with pytest.raises(rest.FeedError,match="run_unreconciled"):
        exclusions.preview(payload)
    rest._fail(admitted,rest.FeedError("fixture"))


@pytest.mark.parametrize("ids", [("a","a"),(" raw",),("",),("bad\nid",)])
def test_invalid_or_duplicate_ids(ids):
    p=profile()
    with pytest.raises(rest.FeedError):
        exclusions.preview(request(p,*ids))


def test_review_consent_and_exact_requested_keys():
    p=profile()
    payload=request(p,"a","b")
    plan=exclusions.preview(payload)
    assert plan["plan_sha256"]==exclusions.preview(request(p,"b","a"))["plan_sha256"]
    with pytest.raises(rest.FeedError,match="review_stale"):
        exclusions.apply(approve(request(p,"a","c"),plan))
    for flag in ("confirm_exact_exclusions","confirm_prospective_only","confirm_dispatch_reauthorization"):
        with pytest.raises(rest.FeedError,match="review_required"):
            exclusions.apply({**approve(payload,plan),flag:False})


def test_zendesk_native_case_resolution_is_exact():
    p={"provider":"zendesk","key":"a"*64,"config":{"ticket_ids":["12"]}}
    assert exclusions.resolve(p,exclusions.Case(native_case_id="12"))==rest._key(p,"zendesk_case","12")
    for row in ({"native_case_id":"13"},{"native_case_id":"12","native_type":"ticket"}):
        with pytest.raises(rest.FeedError,match="outside_reviewed_scope"):
            exclusions.resolve(p,exclusions.Case(**row))


def test_filter_rejects_malformed_projection_and_saved_state():
    p={"excluded_case_keys":["a"*64]}
    with pytest.raises(rest.FeedError,match="projection"):
        exclusions.filter_rows(p,[["e","raw-case","a","t","r"]])
    with pytest.raises(rest.FeedError,match="state"):
        exclusions.filter_rows({"excluded_case_keys":["bad"]},[])
