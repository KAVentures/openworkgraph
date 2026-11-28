"""Reviewed reconciliation of expired routine ingestion intents.

No provider access, no speculative checkpoint repair and no remote cancellation.
A stale worker must be fenced by the profile revision and exact run identity.
"""
from datetime import timedelta
import hmac
import time
from pydantic import Field, StrictInt, StrictBool, ValidationError

from . import enterprise_rest_feed as rest
from . import enterprise_ingestion_run_history as history
from . import event_log_workspace as ws

FORMAT="owg_enterprise_routine_intent_reconciliation_v1"
MAX_RECEIPTS=50
TTL_SECONDS=600
DIGEST=r"^[a-f0-9]{64}$"
RUN=r"^[a-f0-9]{32}$"
PROFILE=r"^rest_[a-f0-9]{24}$"


class Preview(rest.Strict):
    profile_id:str=Field(pattern=PROFILE)
    revision:StrictInt=Field(ge=1)
    run_id:str=Field(pattern=RUN)


class Apply(Preview):
    reviewed_at:str=Field(min_length=20,max_length=64)
    plan_sha256:str=Field(pattern=DIGEST)
    confirm_exact_intent:StrictBool
    confirm_workers_reconciled:StrictBool
    confirm_uncertain_remote_outcome:StrictBool
    confirm_disable_and_reauthorize:StrictBool


def require(ok,code,status=409):
    if not ok:
        raise rest.FeedError(code,status)


def parse(model,value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_routine_intent_review",422) from None


def journal(c,p):
    return c.execute("""SELECT revision, state, since_at, until_at, checkpoint_before,
        lease_until FROM enterprise_ingestion_runs WHERE profile_id=? AND run_id=?""",
        [p["profile_id"],p["run_id"]]).fetchone()


def assess(c,p,epoch):
    if not p.get("run_id"):
        return None
    if p.get("historical_active"):
        return {"kind":"historical","eligible":False,"reason":"use_historical_reconciliation"}
    row=journal(c,p)
    item={"kind":"routine","run_id":p["run_id"],"lease_expires_at":history.iso(p["lease_until"]),
          "expired":p["lease_until"]<=epoch,"journal_state":row[1] if row else None,
          "admitted_since":row[2] if row else None,"admitted_until":row[3] if row else None,
          "checkpoint_before":p["checkpoint"],"legacy_missing_admission":row is None,
          "eligible":False,"reason":None}
    if not item["expired"]:
        item["reason"]="lease_still_active"
    elif row is not None and (row[0]!=p["revision"] or row[1]!="running"
                              or row[4]!=p["checkpoint"] or row[5]!=p["lease_until"]):
        item["reason"]="journal_profile_inconsistent"
    elif p["status"]=="dataset_deleted":
        item["reason"]="dataset_deleted"
    else:
        item["eligible"]=True
    return item


def _plan(c,p,spec,reviewed,epoch):
    require(p["revision"]==spec.revision,"profile_changed")
    require(p.get("run_id")==spec.run_id,"routine_intent_changed")
    observed=assess(c,p,epoch)
    require(observed and observed["kind"]=="routine","not_a_routine_intent")
    result={"format":FORMAT,"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
            "revision":p["revision"],"next_revision":p["revision"]+1,
            "reviewed_at":rest._iso(reviewed),
            "expires_at":rest._iso(reviewed+timedelta(seconds=TTL_SECONDS)),
            "intent":observed,"retained_log_id":p["log_id"],"checkpoint":p["checkpoint"],
            "will_disable_feed":True,"provider_contacted":False,
            "retained_data_modified":False,"remote_cancellation_verified":False}
    digest=rest._key(p,"routine_intent_review_v1",rest._digest({
        "plan":result,"private_profile_sha256":rest._digest(p)}))
    return {**result,"plan_sha256":digest}


def inventory(*,epoch=None):
    epoch=time.time() if epoch is None else epoch
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p=rest._get(c,pid)
                observed=assess(c,p,epoch)
                if observed and observed["kind"]=="routine":
                    rows.append({"profile_id":pid,"provider":p.get("provider","rest"),
                        "revision":p["revision"],**observed})
            return {"format":FORMAT,"intents":rows,"review_ttl_seconds":TTL_SECONDS,
                    "provider_contacted":False,"remote_cancellation_verified":False}
        finally:
            c.close()


def preview(value,*,now=None,epoch=None):
    spec=parse(Preview,value)
    now=now or rest._now()
    with ws._LOCK:
        c=rest._connect()
        try:
            return _plan(c,rest._get(c,spec.profile_id),spec,now,time.time() if epoch is None else epoch)
        finally:
            c.close()


def apply(value,*,now=None,epoch=None,_transaction_hook=None):
    spec=parse(Apply,value)
    require(spec.confirm_exact_intent and spec.confirm_workers_reconciled and
            spec.confirm_uncertain_remote_outcome and spec.confirm_disable_and_reauthorize,
            "explicit_routine_reconciliation_approval_required",403)
    reviewed=rest._stamp(spec.reviewed_at)
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("BEGIN")
            try:
                now=now or rest._now()
                epoch=time.time() if epoch is None else epoch
                require(reviewed<=now<reviewed+timedelta(seconds=TTL_SECONDS),
                        "routine_reconciliation_review_expired")
                p=rest._get(c,spec.profile_id)
                plan=_plan(c,p,spec,reviewed,epoch)
                require(hmac.compare_digest(spec.plan_sha256,plan["plan_sha256"]),
                        "routine_reconciliation_review_stale")
                require(plan["intent"]["eligible"],"routine_intent_not_eligible")
                receipt={"format":FORMAT,"run_id":p["run_id"],"profile_id":p["profile_id"],
                         "provider":p.get("provider","rest"),"previous_revision":p["revision"],
                         "revision":p["revision"]+1,"plan_sha256":spec.plan_sha256,
                         "reconciled_at":rest._iso(now),"checkpoint":p["checkpoint"],
                         "retained_log_id":p["log_id"],"outcome":"uncertain_remote_outcome",
                         "legacy_missing_admission":plan["intent"]["legacy_missing_admission"],
                         "provider_contacted":False,"remote_cancellation_verified":False,
                         "retained_data_modified":False,"enabled":False}
                history.finish(c,p["profile_id"],p["run_id"],"interrupted",
                               error="expired_intent_reviewed_reconciliation",now=epoch)
                p.update(revision=p["revision"]+1,enabled=False,status="disabled",run_id=None,lease_until=0)
                p["routine_reconciliation_receipts"]=[receipt,*p.get("routine_reconciliation_receipts",[])][:MAX_RECEIPTS]
                rest._put(c,p)
                if _transaction_hook is not None:
                    _transaction_hook(c)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return {**receipt,"separate_enable_and_reauthorization_required":True}
        finally:
            c.close()


def receipts(profile_id):
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            return {"format":FORMAT,"profile_id":profile_id,"revision":p["revision"],
                    "receipts":p.get("routine_reconciliation_receipts",[]),
                    "max_receipts":MAX_RECEIPTS,"provider_contacted":False}
        finally:
            c.close()
