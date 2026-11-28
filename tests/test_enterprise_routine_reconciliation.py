"""Reviewed routine intent recovery through the real workspace."""
from datetime import timedelta
import json
import time
import pytest

from server import enterprise_rest_feed as rest
from server import enterprise_routine_reconciliation as recovery
from server import enterprise_ingestion_run_history as history
from server import enterprise_reviewed_catchup as catchup
from server import enterprise_ingestion_coverage as coverage
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


def profile():
    return rest.create(config(initial_since=rest._iso(rest._now()-timedelta(days=4))))


def expire(p,*,legacy=False,mismatch=False):
    admitted,_,until=rest._begin(p["profile_id"],p["revision"],rest._now())
    with ws._LOCK:
        c=rest._connect()
        try:
            admitted["lease_until"]=time.time()-1
            rest._put(c,admitted)
            if legacy:
                c.execute("DELETE FROM enterprise_ingestion_runs WHERE run_id=?",[admitted["run_id"]])
            elif not mismatch:
                c.execute("UPDATE enterprise_ingestion_runs SET lease_until=? WHERE run_id=?",
                          [admitted["lease_until"],admitted["run_id"]])
        finally:
            c.close()
    return admitted,until


def target(p):
    return {k:p[k] for k in ("profile_id","revision","run_id")}


def approve(p,plan=None,**extra):
    plan=plan or recovery.preview(target(p))
    return {**target(p),"reviewed_at":plan["reviewed_at"],"plan_sha256":plan["plan_sha256"],
            "confirm_exact_intent":True,"confirm_workers_reconciled":True,
            "confirm_uncertain_remote_outcome":True,"confirm_disable_and_reauthorize":True,**extra}


def public(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"]==p["profile_id"])


def test_expired_review_preserves_committed_checkpoint_and_fences_old_worker(monkeypatch):
    p=profile()
    p=run(p,lambda _:page([event(p)]))
    old,until=expire(p)
    before=history.history(profile_id=p["profile_id"])
    with monkeypatch.context() as m:
        m.setattr(rest,"_check_binding",lambda *a:pytest.fail("unexpected credential check"))
        m.setattr(rest,"_extract_window",lambda *a:pytest.fail("unexpected provider read"))
        plan=recovery.preview(target(old))
        assert plan["intent"]["eligible"]
        assert plan["checkpoint"]==p["checkpoint"]
        assert not plan["provider_contacted"]
        receipt=recovery.apply(approve(old,plan))
    assert receipt["outcome"]=="uncertain_remote_outcome"
    assert receipt["revision"]==p["revision"]+1
    assert receipt["checkpoint"]==p["checkpoint"]
    assert not receipt["remote_cancellation_verified"]
    live=public(p)
    assert live["checkpoint"]==p["checkpoint"] and live["log_id"]==p["log_id"]
    assert live["last_result"]==p["last_result"] and not live["enabled"]
    assert recovery.receipts(p["profile_id"])["receipts"]==[
        {k:v for k,v in receipt.items() if k!="separate_enable_and_reauthorization_required"}]
    rest._fail(old,rest.FeedError("late_worker_failure"))
    rows=history.history(profile_id=p["profile_id"])["runs"]
    assert len(rows)==len(before["runs"])==2
    reconciled=next(x for x in rows if x["run_id"]==old["run_id"])
    assert reconciled["stored_state"]=="interrupted"
    assert reconciled["error_code"]=="expired_intent_reviewed_reconciliation"
    assert not reconciled["local_intent_unreconciled"]
    assert ws.list_logs()["logs"][0]["events"]==1
    with ws._LOCK:
        c=rest._connect()
        try:
            assert coverage.load(c,p["profile_id"])["recorded_commits"]==1
        finally:
            c.close()
    enabled=rest.change(p["profile_id"],receipt["revision"],True)
    with pytest.raises(rest.FeedError):
        rest._check(old)
    assert run(enabled,lambda _:page([]))["checkpoint"]!=p["checkpoint"]


def test_passive_inventory_and_all_normal_dispatch_paths_blocked():
    p=profile()
    old,_=expire(p)
    rows=recovery.inventory()["intents"]
    assert len(rows)==1 and rows[0]["eligible"]
    assert public(p)["routine_intent_requires_reconciliation"]
    with pytest.raises(rest.FeedError,match="expired_routine_intent"):
        run(p,lambda _:pytest.fail("expired dispatch"))
    for enabled in (True,False):
        with pytest.raises(rest.FeedError,match="expired_routine_intent"):
            rest.change(p["profile_id"],p["revision"],enabled)
    result=catchup.preview({"targets":[{"profile_id":p["profile_id"],"revision":p["revision"]}]})
    assert result["targets"][0]["reason"]=="expired_intent_requires_review"
    assert rest.run_scheduled(p["profile_id"],p["revision"])[0]=="paused:rest_profile_changed"
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==1


def test_legacy_missing_admission_no_fabricated_journal():
    p=profile()
    old,_=expire(p,legacy=True)
    plan=recovery.preview(target(old))
    assert plan["intent"]["legacy_missing_admission"] and plan["intent"]["eligible"]
    assert plan["intent"]["admitted_since"] is None
    result=recovery.apply(approve(old,plan))
    assert result["legacy_missing_admission"]
    assert history.history(profile_id=p["profile_id"])["returned_runs"]==0


def test_mismatched_journal_refuses_review():
    old,_=expire(profile(),mismatch=True)
    plan=recovery.preview(target(old))
    assert plan["intent"]["reason"]=="journal_profile_inconsistent"
    with pytest.raises(rest.FeedError,match="not_eligible"):
        recovery.apply(approve(old,plan))
    assert public(old)["routine_intent_requires_reconciliation"]


def test_active_lease_cannot_be_reconciled():
    p=profile()
    admitted,_,_=rest._begin(p["profile_id"],p["revision"],rest._now())
    plan=recovery.preview(target(admitted))
    assert plan["intent"]["reason"]=="lease_still_active"
    with pytest.raises(rest.FeedError,match="not_eligible"):
        recovery.apply(approve(admitted,plan))


@pytest.mark.parametrize("flag",["confirm_exact_intent","confirm_workers_reconciled",
    "confirm_uncertain_remote_outcome","confirm_disable_and_reauthorize"])
def test_explicit_approval_flags(flag):
    old,_=expire(profile())
    with pytest.raises(rest.FeedError,match="explicit_routine"):
        recovery.apply(approve(old,**{flag:False}))


def test_expiry_tamper_and_rollback():
    old,_=expire(profile())
    now=rest._now()
    plan=recovery.preview(target(old),now=now)
    with pytest.raises(rest.FeedError,match="review_expired"):
        recovery.apply(approve(old,plan),now=now+timedelta(minutes=10))
    with pytest.raises(rest.FeedError,match="review_stale"):
        recovery.apply(approve(old,plan,plan_sha256="a"*64),now=now)
    def fail(_):
        raise RuntimeError("before commit")
    with pytest.raises(RuntimeError,match="before commit"):
        recovery.apply(approve(old,plan),now=now,_transaction_hook=fail)
    assert recovery.receipts(old["profile_id"])["receipts"]==[]
    assert history.history(profile_id=old["profile_id"])["runs"][0]["stored_state"]=="running"
    receipt=recovery.apply(approve(old,plan),now=now)
    assert not receipt["enabled"]
    with pytest.raises(rest.FeedError):
        recovery.apply(approve(old,plan),now=now)


def test_dataset_deletion_erases_review_receipts():
    p=profile()
    p=run(p,lambda _:page([event(p)]))
    old,_=expire(p)
    recovery.apply(approve(old))
    ws.delete_log(p["log_id"])
    assert recovery.receipts(p["profile_id"])["receipts"]==[]
