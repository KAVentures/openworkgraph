"""Explicit historical re-read of a previously traversed change window.

The existing provider adapter, scope, pseudonym key, row budgets and
transactional source-key retention are reused. The live cursor is never
rewound. Historical receipts are separate from routine success/failure.
"""
from datetime import timedelta
import secrets
import time
import re

import httpx
from pydantic import Field,StrictBool,StrictInt,ValidationError
from . import enterprise_rest_feed as rest
from . import event_log_workspace as ws
from shared.enterprise_connector_contract import PROVIDERS

FORMAT="openworkgraph.connector-historical-reconciliation.v1"
MAX_RECEIPTS=30
SUPPORTED=PROVIDERS
REQUEST=r"^[a-f0-9]{32}$"
DIGEST=r"^[a-f0-9]{64}$"


class Preview(rest.Strict):
    revision:StrictInt=Field(ge=1)
    since:str=Field(min_length=20,max_length=64)
    until:str=Field(min_length=20,max_length=64)


class Execute(Preview):
    request_id:str=Field(pattern=REQUEST)
    expected_plan_sha256:str=Field(pattern=DIGEST)
    confirm_historical_read:StrictBool
    confirm_retention:StrictBool
    confirm_workers_reconciled:StrictBool


class Reconcile(rest.Strict):
    revision:StrictInt=Field(ge=1)
    request_id:str=Field(pattern=REQUEST)
    confirm_workers_stopped:StrictBool
    confirm_uncertain_outcome:StrictBool


def model(cls,value):
    try:
        return cls.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_historical_reconciliation_request",422) from None


def _window(p,spec,now):
    if p.get("provider","rest") not in SUPPORTED:
        raise rest.FeedError("historical_adapter_not_reviewed",422)
    if p["revision"]!=spec.revision:
        raise rest.FeedError("profile_changed")
    if p.get("integrity_hold"):
        raise rest.FeedError("retained_integrity_hold_requires_reviewed_release")
    if not p["enabled"] or not p["checkpoint"]:
        raise rest.FeedError("enabled_profile_with_completed_checkpoint_required")
    if p["run_id"] or p.get("historical_active"):
        raise rest.FeedError("finish_or_reconcile_active_connector_run")
    if p["retry_not_before"]>time.time():
        raise rest.FeedError("provider_retry_cooldown")
    rest._check_binding(p)
    since,until=rest._stamp(spec.since),rest._stamp(spec.until)
    original,checkpoint=rest._stamp(p["config"]["initial_since"]),rest._stamp(p["checkpoint"])
    if not original<=since<until<=checkpoint:
        raise rest.FeedError("historical_window_must_be_inside_traversed_scope",422)
    if until-since>timedelta(hours=p["config"]["window_hours"]):
        raise rest.FeedError("historical_window_exceeds_configured_window",422)
    if since<now-timedelta(days=365):
        raise rest.FeedError("historical_window_exceeds_local_age_bound",422)
    provider=p.get("provider","rest")
    adapter=rest._adapter(provider)
    # Native providers must not silently round reviewed bounds.
    whole_seconds=provider in {"salesforce","servicenow"} or (
        provider=="slack" and p["config"]["mode"]=="enterprise_audit")
    if whole_seconds and (since.microsecond or until.microsecond):
        raise rest.FeedError("historical_window_requires_whole_seconds",422)
    if provider=="sap" and any(v.hour or v.minute or v.second or v.microsecond for v in (since,until)):
        raise rest.FeedError("historical_window_requires_whole_business_dates",422)
    if provider=="google":
        # The provider's routine cutoff accounts for overlap. Historical
        # bounds are exact and use no automatic overlap.
        historical={**p,"checkpoint":None}
        cutoff=adapter.review_cutoff(historical,now,since)
    elif adapter is not None and provider!="sql":
        cutoff=adapter.cutoff(p,now,since)
    else:
        cutoff=now
    if until>cutoff:
        raise rest.FeedError("historical_window_exceeds_current_provider_cutoff",422)
    return since,until


def _plan(p,spec,now):
    since,until=_window(p,spec,now)
    prior=p.get("historical_receipts",[])
    definition={"format":FORMAT,"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
                "profile_revision":p["revision"],"configuration_sha256":rest._digest(p["config"]),
                "checkpoint":p["checkpoint"],"retained_log_id":p["log_id"],
                "since":rest._iso(since),"until":rest._iso(until),
                "previous_receipt_id":prior[0]["request_id"] if prior else None,
                "profile_state_sha256":rest._digest(p),
                "excluded_case_count":len(p.get("excluded_case_keys",[]))}
    return {**definition,"plan_sha256":rest._digest(definition),
            "provider_reads_performed":False,"retention_authorized":False,
            "source_completeness_verified":False,
            "max_rows":rest.MAX_ROWS,"max_requests":rest.MAX_PAGES,
            "no_checkpoint_rewind":True,"no_automatic_overlap":True}


def preview(profile_id,value,*,now=None):
    spec=model(Preview,value)
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            if p["log_id"]:
                ws._check_log(c,p["log_id"])
            return _plan(p,spec,now or rest._now())
        finally:
            c.close()


def _receipt(p,request_id):
    return next((r for r in p.get("historical_receipts",[]) if r["request_id"]==request_id),None)


def _start(profile_id,spec,now):
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            previous=_receipt(p,spec.request_id)
            if previous:
                if p["revision"]!=spec.revision or p.get("integrity_hold"):
                    raise rest.FeedError("historical_receipt_revision_changed")
                if (previous["plan_sha256"]!=spec.expected_plan_sha256 or previous["profile_revision"]!=spec.revision
                    or previous["since"]!=rest._iso(rest._stamp(spec.since)) or previous["until"]!=rest._iso(rest._stamp(spec.until))):
                    raise rest.FeedError("historical_request_identity_conflict")
                return None,previous
            plan=_plan(p,spec,now)
            if plan["plan_sha256"]!=spec.expected_plan_sha256:
                raise rest.FeedError("historical_plan_changed_repreview_required")
            if p["log_id"]:
                ws._check_log(c,p["log_id"])
            p.update(run_id=secrets.token_hex(16),lease_until=time.time()+rest.LEASE_SECONDS,
                     historical_active={"request_id":spec.request_id,"plan_sha256":plan["plan_sha256"],
                         "profile_revision":p["revision"],"since":plan["since"],"until":plan["until"],
                         "checkpoint_before":p["checkpoint"],"started_at":rest._iso(now),
                         "prior_status":p["status"]},status="running")
            c.execute("BEGIN")
            try:
                rest._put(c,p)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return p,None
        finally:
            c.close()


def finalize(p,*,outcome,added=None,duplicates=None,observed=None,excluded=None,code=None):
    """Pure state transition, invoked inside the retained-log transaction."""
    active=p.pop("historical_active")
    receipt={k:active[k] for k in ("request_id","plan_sha256","profile_revision","since","until",
                                   "checkpoint_before","started_at")}
    receipt.update(outcome=outcome,finished_at=rest._iso(rest._now()),checkpoint_after=p["checkpoint"],
                   events_added=added,duplicates_skipped=duplicates,distinct_events_observed=observed,
                   excluded_case_events=excluded,
                   error=code,source_completeness_verified=False,
                   routine_checkpoint_advanced=False)
    p["historical_receipts"]=[receipt,*p.get("historical_receipts",[])][:MAX_RECEIPTS]
    p.update(status=active["prior_status"],run_id=None,lease_until=0)
    return receipt


def execute(profile_id,value,*,client_factory=httpx.Client,now=None):
    spec=model(Execute,value)
    if not (spec.confirm_historical_read and spec.confirm_retention and spec.confirm_workers_reconciled):
        raise rest.FeedError("explicit_historical_read_retention_and_worker_review_required",403)
    p,previous=_start(profile_id,spec,now or rest._now())
    if previous:
        return {"receipt":previous,"replayed_receipt":True,"provider_read_performed":False}
    try:
        since,until=rest._stamp(p["historical_active"]["since"]),rest._stamp(p["historical_active"]["until"])
        rows,counts=rest._extract_window(p,since,until,client_factory)
        result=rest._retain(p,rows,counts,until)
        return {"receipt":result["historical_receipts"][0],"replayed_receipt":False,
                "provider_read_performed":True}
    except rest.FeedError as error:
        rest._fail(p,error)
        raise
    except Exception:
        error=rest.FeedError("historical_retention_or_source_failed_inspect_receipt")
        rest._fail(p,error)
        raise error from None


def reconcile(profile_id,value):
    spec=model(Reconcile,value)
    if not spec.confirm_workers_stopped or not spec.confirm_uncertain_outcome:
        raise rest.FeedError("explicit_uncertain_historical_run_reconciliation_required",403)
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            active=p.get("historical_active")
            if p["revision"]!=spec.revision or not active or active["request_id"]!=spec.request_id:
                raise rest.FeedError("historical_run_changed")
            if p["lease_until"]>time.time():
                raise rest.FeedError("historical_lease_still_active")
            c.execute("BEGIN")
            try:
                receipt=finalize(p,outcome="uncertain",code="expired_historical_intent_reconciled")
                rest._put(c,p)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return {"receipt":receipt,"provider_reads_performed":False}
        finally:
            c.close()


def public_status(p,now):
    active=p.get("historical_active")
    return {"historical_reconciliation_supported":p.get("provider","rest") in SUPPORTED,
            "historical_active":None if not active else {**{k:active[k] for k in
                ("request_id","profile_revision","since","until","started_at")},
                "lease_expired":p["lease_until"]<=now},
            "historical_receipts":p.get("historical_receipts",[])}
