"""Exact reviewed one-window catch-up worklist over the retained ingestion engine.

A review is passive, short-lived, and bound to the exact private profile
state and admitted interval. Execution is one operator-approved read per
request; no hidden batch dispatch, schedule mutation, or cursor rewind.
"""
from datetime import timedelta
import hmac
import re
import time

import httpx
from pydantic import Field, StrictInt, StrictBool, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import enterprise_ingestion_coverage as coverage
from . import event_log_workspace as ws

FORMAT="owg_enterprise_reviewed_catchup_v1"
MAX_TARGETS=20
REVIEW_TTL_SECONDS=600
PROFILE=r"^rest_[a-f0-9]{24}$"
DIGEST=r"^[a-f0-9]{64}$"


class Target(rest.Strict):
    profile_id:str=Field(pattern=PROFILE)
    revision:StrictInt=Field(ge=1)


class Selection(rest.Strict):
    targets:list[Target]=Field(min_length=1,max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def unique(self):
        if len({x.profile_id for x in self.targets})!=len(self.targets):
            raise ValueError("duplicate target")
        return self


class Execute(Target):
    reviewed_at:str=Field(min_length=20,max_length=64)
    since:str=Field(min_length=20,max_length=64)
    until:str=Field(min_length=20,max_length=64)
    plan_sha256:str=Field(pattern=DIGEST)
    confirm_exact_window:StrictBool
    confirm_provider_read:StrictBool
    confirm_retention:StrictBool
    confirm_competing_workers_reconciled:StrictBool


def require(ok,code,status=409):
    if not ok:
        raise rest.FeedError(code,status)


def parse(model,value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_catchup_request",422) from None


def readiness(c,p,epoch):
    if p.get("integrity_hold"):
        return "integrity_hold"
    if not p["enabled"] or p["status"]=="dataset_deleted":
        return "feed_disabled"
    if p.get("historical_active"):
        return "historical_intent_unreconciled"
    if p["run_id"]:
        return "active_intent" if p["lease_until"]>epoch else "expired_intent_requires_review"
    if p["retry_not_before"]>epoch:
        return "retry_cooldown"
    try:
        if p["log_id"]:
            ws._check_log(c,p["log_id"])
    except ws.EventLogWorkspaceError:
        return "retained_dataset_unavailable"
    # This is deliberately separate from live provider health. The existing
    # binding check only inspects local deployment metadata/credential state.
    try:
        rest._check_binding(p)
    except rest.FeedError:
        return "local_binding_unavailable"
    return None


def signed_plan(p,reviewed_at,since,until):
    definition={"format":FORMAT,"profile_id":p["profile_id"],"revision":p["revision"],
                "reviewed_at":rest._iso(reviewed_at),"since":rest._iso(since),
                "until":rest._iso(until),"private_state_sha256":rest._digest(p)}
    return rest._key(p,"reviewed_catchup_plan_v1",rest._digest(definition))


def row(c,p,now,epoch):
    item={"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
          "revision":p["revision"],"checkpoint":p["checkpoint"],
          "enabled":p["enabled"],"eligible":False,"reason":None,
          "reviewed_at":rest._iso(now),"expires_at":rest._iso(now+timedelta(seconds=REVIEW_TTL_SECONDS)),
          "since":None,"until":None,"plan_sha256":None,"advancing_hours":None,
          "overlap_minutes":p["config"]["overlap_minutes"],
          "excluded_case_count":len(p.get("excluded_case_keys",[])),
          "backlog":coverage.backlog(p,now)}
    reason=readiness(c,p,epoch)
    if reason:
        item["reason"]=reason
        return item
    try:
        since,until=rest._next_window(p,now)
    except rest.FeedError as error:
        item["reason"]="no_advancing_window" if error.code in ("no_new_checkpoint_window","clock_before_checkpoint") else "provider_cutoff_unavailable"
        return item
    anchor=rest._stamp(p["checkpoint"] or p["config"]["initial_since"])
    item.update(eligible=True,since=rest._iso(since),until=rest._iso(until),
                advancing_hours=coverage.microseconds(anchor,until)/3600000000,
                plan_sha256=signed_plan(p,now,since,until))
    return item


def inventory():
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p=rest._get(c,pid)
                rows.append({"profile_id":pid,"provider":p.get("provider","rest"),
                             "revision":p["revision"],"enabled":p["enabled"],
                             "checkpoint":p["checkpoint"],"integrity_hold":bool(p.get("integrity_hold"))})
            return {"format":FORMAT,"profiles":rows,"max_targets":MAX_TARGETS,
                    "review_ttl_seconds":REVIEW_TTL_SECONDS,"provider_reads_performed":False}
        finally:
            c.close()


def preview(value,*,now=None):
    spec=parse(Selection,value)
    now=now or rest._now()
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for t in sorted(spec.targets,key=lambda t:t.profile_id):
                p=rest._get(c,t.profile_id)
                require(p["revision"]==t.revision,"profile_changed")
                rows.append(row(c,p,now,time.time()))
        finally:
            c.close()
    # Longest estimated backlog first; stable profile ID tie break. This
    # ordering is a planning hint, not a provider throughput forecast.
    rows.sort(key=lambda r:(not r["eligible"],-r["backlog"]["uncheckpointed_microseconds"],r["profile_id"]))
    return {"format":FORMAT,"reviewed_at":rest._iso(now),"targets":rows,
            "eligible_count":sum(x["eligible"] for x in rows),
            "blocked_count":sum(not x["eligible"] for x in rows),
            "provider_reads_performed":False,"credential_refresh_performed":False,
            "execution_authorized":False,"batch_dispatch_supported":False,
            "review_ttl_seconds":REVIEW_TTL_SECONDS}


def execute(value,*,client_factory=httpx.Client,now=None):
    spec=parse(Execute,value)
    require(spec.confirm_exact_window and spec.confirm_provider_read and spec.confirm_retention
            and spec.confirm_competing_workers_reconciled,"explicit_catchup_window_approval_required",403)
    reviewed=rest._stamp(spec.reviewed_at)
    expected_since,expected_until=rest._stamp(spec.since),rest._stamp(spec.until)
    require(expected_since<expected_until,"invalid_catchup_window",422)

    def verify(c,p,since,until):
        # Runs while the canonical workspace lock is held, immediately
        # before lease admission, preventing a preview/admission race.
        actual_now=now or rest._now()
        require(reviewed<=actual_now<reviewed+timedelta(seconds=REVIEW_TTL_SECONDS),
                "catchup_review_expired")
        require(readiness(c,p,time.time()) is None,"catchup_target_no_longer_ready")
        require((since,until)==(expected_since,expected_until),"catchup_review_window_changed")
        require(hmac.compare_digest(spec.plan_sha256,signed_plan(p,reviewed,since,until)),
                "catchup_review_stale")
        # A fresh cutoff may permit a wider interval; never take it.
        # If it no longer permits the reviewed end, fail before admission.
        try:
            current_since,current_until=rest._next_window(p,actual_now)
        except rest.FeedError:
            raise rest.FeedError("catchup_current_cutoff_unavailable") from None
        require(current_since==since and until<=current_until,"catchup_current_cutoff_changed")

    result=rest.run(spec.profile_id,spec.revision,confirm_reads=True,confirm_retention=True,
                    client_factory=client_factory,now=reviewed,_review_guard=verify)
    return {"format":FORMAT,"profile_id":result["profile_id"],"revision":result["revision"],
            "since":rest._iso(expected_since),"until":rest._iso(expected_until),
            "checkpoint":result["checkpoint"],"events_added":result["events_added"],
            "duplicates_skipped":result["duplicates_skipped"],"last_result":result["last_result"],
            "provider_read_performed":True,"schedule_modified":False,
            "requires_new_review_for_next_window":True,"source_completeness_verified":False}
