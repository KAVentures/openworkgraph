"""Local monitoring lifecycle: real retained workspace and admitted run journal."""
from datetime import timedelta
import json

import httpx
import pytest

from server import enterprise_ingestion_service_levels as sla
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


def profile():
    return rest.create(config(initial_since=rest._iso(rest._now()-timedelta(days=4))))


def policy(p, **overrides):
    return {"profile_id":p["profile_id"],"profile_revision":p["revision"],
            "expected_rule_revision":0,"enabled":True,"interval_minutes":15,
            "success_age_hours":1,"checkpoint_age_hours":None,"failure_streak":None,
            "watch_expired_intent":False,"watch_integrity_hold":False,
            "confirm_local_monitoring":True,**overrides}


def observation(signal):
    return next(x for x in sla.inventory()["profiles"][0]["rule"]["last_observations"]
                if x["signal"]==signal)


def mutate(p, **updates):
    with ws._LOCK:
        c=rest._connect()
        try:
            raw=rest._get(c,p["profile_id"])
            rest._put(c,{**raw,**updates})
        finally:
            c.close()


def tick(now, force=True):
    result=sla.evaluate_due(now=now,force=force)
    assert not result["errors"]
    return result["evaluated"]


def test_opt_in_schedule_acknowledgement_recovery_and_new_episode(monkeypatch):
    p=profile()
    born=rest._stamp(p["created_at"])
    assert tick(born)==[]
    rule=sla.save(policy(p),now=born)
    assert rule["revision"]==1
    assert tick(born,False)[0]["opened"]==0
    assert tick(born+timedelta(minutes=1),False)==[]
    assert sla.inventory()["episodes"]==[]
    with monkeypatch.context() as m:
        m.setattr(rest,"_check_binding",lambda *a:pytest.fail("unexpected credential check"))
        m.setattr(httpx.Client,"stream",lambda *a,**k:pytest.fail("unexpected provider read"))
        assert tick(born+timedelta(hours=1),False)[0]["opened"]==1
        e=sla.inventory()["episodes"][0]
        assert e["active"] and e["observation"]["reason"]=="since_first_configuration"
        assert sla.inventory()["unacknowledged_alerts"]==1
        result=sla.acknowledge({"episode_id":e["episode_id"],"version":e["version"],
                                "confirm_acknowledgement":True},now=born+timedelta(hours=1))
        assert result["active"] and result["acknowledged_at"]
        with pytest.raises(rest.FeedError,match="episode_changed"):
            sla.acknowledge({"episode_id":e["episode_id"],"version":e["version"],
                            "confirm_acknowledgement":True},now=born+timedelta(hours=1))
        assert tick(born+timedelta(hours=2))[0]["opened"]==0
        assert sla.inventory()["unacknowledged_alerts"]==0
    mutate(p,last_success_at=rest._iso(born+timedelta(hours=2)))
    assert tick(born+timedelta(hours=2,minutes=1))[0]["closed"]==1
    e2=sla.inventory()["episodes"][0]
    assert not e2["active"] and e2["close_reason"]=="observed_clear"
    assert tick(born+timedelta(hours=3))[0]["opened"]==1
    active=sla.inventory()["episodes"][0]
    assert active["episode_id"]!=e["episode_id"] and active["acknowledged_at"] is None


def test_disabled_and_future_timestamp_are_unknown_not_recovery():
    p=profile();born=rest._stamp(p["created_at"])
    sla.save(policy(p),now=born)
    tick(born+timedelta(hours=1))
    p=rest.change(p["profile_id"],p["revision"],False)
    tick(born+timedelta(hours=2))
    assert observation("success_age")["reason"]=="feed_disabled"
    assert sla.inventory()["active_alerts"]==1
    p=rest.change(p["profile_id"],p["revision"],True)
    mutate(p,last_success_at=rest._iso(born+timedelta(days=1)))
    tick(born+timedelta(hours=3))
    assert observation("success_age")["state"]=="unknown"
    assert sla.inventory()["active_alerts"]==1


def test_consecutive_failed_admitted_runs_and_success_break():
    p=profile();now=rest._now()
    sla.save(policy(p,success_age_hours=None,failure_streak=2),now=now)
    tick(now)
    assert observation("consecutive_failures")["state"]=="unknown"
    for i in range(2):
        admitted,_,_=rest._begin(p["profile_id"],p["revision"],now)
        rest._fail(admitted,rest.FeedError("fixture_failure",502))
        tick(now+timedelta(minutes=i+1))
    assert observation("consecutive_failures")["state"]=="breached"
    assert sla.inventory()["active_alerts"]==1
    p=run(p,lambda _:page([]))
    tick(now+timedelta(minutes=3))
    assert observation("consecutive_failures")["state"]=="clear"
    assert sla.inventory()["active_alerts"]==0


def test_expired_intent_and_integrity_hold_remain_distinct():
    p=profile();now=rest._now()
    sla.save(policy(p,success_age_hours=None,watch_expired_intent=True,watch_integrity_hold=True),now=now)
    admitted,_,_=rest._begin(p["profile_id"],p["revision"],now)
    expired=rest._stamp(rest._iso(now))+timedelta(hours=1)
    tick(expired)
    assert observation("expired_intent")["state"]=="breached"
    assert observation("integrity_hold")["state"]=="clear"
    mutate(p,integrity_hold={"at":rest._iso(now)},enabled=False,run_id=None,lease_until=0)
    tick(expired+timedelta(minutes=1))
    assert observation("expired_intent")["state"]=="clear"
    assert observation("integrity_hold")["state"]=="breached"
    assert sla.inventory()["active_alerts"]==1


def test_revision_fencing_and_replacement_not_false_recovery():
    p=profile();born=rest._stamp(p["created_at"])
    sla.save(policy(p),now=born)
    tick(born+timedelta(hours=1))
    with pytest.raises(rest.FeedError,match="rule_changed"):
        sla.save(policy(p),now=born+timedelta(hours=2))
    result=sla.save(policy(p,expected_rule_revision=1,enabled=False),now=born+timedelta(hours=2))
    assert result["revision"]==2
    assert tick(born+timedelta(hours=3))==[]
    e=sla.inventory()["episodes"][0]
    assert not e["active"] and e["close_reason"]=="policy_replaced"
    updated=rest.change(p["profile_id"],p["revision"],False)
    with pytest.raises(rest.FeedError,match="profile_changed"):
        sla.save(policy(p,expected_rule_revision=2),now=born+timedelta(hours=3))
    assert sla.inventory()["profiles"][0]["revision"]==updated["revision"]


def test_rollback_of_episode_and_observation(monkeypatch):
    p=profile();born=rest._stamp(p["created_at"])
    sla.save(policy(p),now=born)
    original=sla.put_rule
    def fail(c,rule):
        original(c,rule)
        raise RuntimeError("before commit")
    monkeypatch.setattr(sla,"put_rule",fail)
    result=sla.evaluate_due(now=born+timedelta(hours=1),force=True)
    assert result["evaluated"]==[] and len(result["errors"])==1
    assert sla.inventory()["episodes"]==[]
    assert sla.inventory()["profiles"][0]["rule"]["last_evaluated_at"] is None


def test_cleanup_on_profile_and_dataset_removal():
    p=profile()
    q=profile()
    q=run(q,lambda _:page([event(q)]))
    now=rest._now()
    for x in (p,q):
        sla.save(policy(x),now=now)
    tick(now+timedelta(hours=2))
    assert sla.inventory()["active_alerts"]==2
    rest.delete(p["profile_id"],p["revision"])
    ws.delete_log(q["log_id"])
    d=sla.inventory()
    assert d["episodes"]==[]
    assert len(d["profiles"])==1 and d["profiles"][0]["rule"] is None


@pytest.mark.parametrize("override",[
    {"confirm_local_monitoring":False},{"interval_minutes":4},{"failure_streak":21},
    {"success_age_hours":True},{"success_age_hours":None},
])
def test_invalid_or_unapproved_policy(override):
    with pytest.raises(rest.FeedError):
        sla.save(policy(profile(),**override))


def test_clock_regression_does_not_close_or_duplicate_alerts():
    p=profile();born=rest._stamp(p["created_at"])
    sla.save(policy(p),now=born)
    tick(born+timedelta(hours=2))
    result=sla.evaluate_due(now=born+timedelta(hours=1),force=True)
    assert len(result["errors"])==1 and result["evaluated"]==[]
    assert sla.inventory()["active_alerts"]==1


def historical_request(p,request_id):
    from server import enterprise_connector_history as history
    window={"revision":p["revision"],"since":p["config"]["initial_since"],"until":p["checkpoint"]}
    plan=history.preview(p["profile_id"],window)
    return {**window,"request_id":request_id,"expected_plan_sha256":plan["plan_sha256"],
            "confirm_historical_read":True,"confirm_retention":True,"confirm_workers_reconciled":True}


def test_historical_failure_streak_does_not_pollute_routine_monitoring():
    from server import enterprise_connector_history as history
    from test_enterprise_rest_feed import provider
    p=run(profile(),lambda _:page([]))
    now=rest._now()
    sla.save(policy(p,success_age_hours=None,failure_streak=1,historical_failure_streak=2),now=now)
    for i in range(2):
        spec=historical_request(p,str(i+1)*32)
        with pytest.raises(rest.FeedError):
            history.execute(p["profile_id"],spec,client_factory=provider(lambda _:httpx.Response(503)))
    tick(now+timedelta(minutes=1))
    assert observation("historical_failure_streak")["state"]=="breached"
    assert observation("consecutive_failures")["state"]=="clear"
    assert sla.inventory()["active_alerts"]==1
    spec=historical_request(p,"3"*32)
    history.execute(p["profile_id"],spec,client_factory=provider(lambda _:page([])))
    tick(now+timedelta(minutes=2))
    assert observation("historical_failure_streak")["state"]=="clear"
    assert sla.inventory()["active_alerts"]==0


def test_unknown_coverage_alert_recovers_after_committed_historical_request(monkeypatch):
    from server import enterprise_connector_history as history
    from server import enterprise_ingestion_coverage as coverage
    from test_enterprise_rest_feed import provider
    p=run(profile(),lambda _:page([]))
    with ws._LOCK:
        c=rest._connect()
        try:
            coverage.erase(c,p["profile_id"])
        finally:
            c.close()
    now=rest._now()
    sla.save(policy(p,success_age_hours=None,unknown_coverage_hours=2),now=now)
    with monkeypatch.context() as m:
        m.setattr(rest,"_check_binding",lambda *a:pytest.fail("unexpected credential check"))
        m.setattr(httpx.Client,"stream",lambda *a,**k:pytest.fail("unexpected source read"))
        tick(now)
        assert observation("unknown_prior_coverage")["state"]=="breached"
        assert observation("unknown_prior_coverage")["value"]==24*3600*1000000
    history.execute(p["profile_id"],historical_request(p,"a"*32),
                    client_factory=provider(lambda _:page([])))
    tick(now+timedelta(minutes=1))
    assert observation("unknown_prior_coverage")["state"]=="clear"
    assert observation("unknown_prior_coverage")["value"]==0
    assert sla.inventory()["active_alerts"]==0


def test_unknown_coverage_index_not_false_recovery():
    from server import enterprise_ingestion_coverage as coverage
    p=run(profile(),lambda _:page([]))
    now=rest._now()
    with ws._LOCK:
        c=rest._connect()
        try:
            coverage.erase(c,p["profile_id"])
        finally:
            c.close()
    sla.save(policy(p,success_age_hours=None,unknown_coverage_hours=1),now=now)
    tick(now)
    assert sla.inventory()["active_alerts"]==1
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("INSERT INTO enterprise_ingestion_coverage VALUES (?,?)",
                      [p["profile_id"],'{"intervals":"invalid"}'])
        finally:
            c.close()
    tick(now+timedelta(minutes=1))
    assert observation("unknown_prior_coverage")["state"]=="unknown"
    assert sla.inventory()["active_alerts"]==1


def test_historical_uncertainty_and_revision_changes_are_not_success():
    p=profile()
    raw={"revision":p["revision"],"historical_receipts":[
        {"profile_revision":p["revision"],"outcome":"failed"},
        {"profile_revision":p["revision"],"outcome":"uncertain"}]}
    assert sla.historical_failure_observation(raw,2)["state"]=="unknown"
    raw["historical_receipts"]=[{"profile_revision":p["revision"],"outcome":"failed"}]*2
    assert sla.historical_failure_observation(raw,2)["state"]=="breached"
    raw["revision"]+=1
    assert sla.historical_failure_observation(raw,2)["state"]=="unknown"
    raw["historical_active"]={"request_id":"a"*32}
    assert sla.historical_failure_observation(raw,2)["reason"]=="historical_intent_unreconciled"


def test_coverage_threshold_bound_and_deleted_profile_refusal():
    p=profile()
    p=run(p,lambda _:page([event(p)]))
    with pytest.raises(rest.FeedError):
        sla.save(policy(p,success_age_hours=None,unknown_coverage_hours=25,coverage_lookback_days=1))
    ws.delete_log(p["log_id"])
    deleted=next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])
    with pytest.raises(rest.FeedError,match="dataset_deleted"):
        sla.save(policy(deleted))
