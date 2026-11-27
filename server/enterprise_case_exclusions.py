"""Reviewed prospective case exclusions, not erasure of retained evidence.

Raw identifiers are transient local inputs. Store only the exact existing
profile-scoped case pseudonym. Never infer a provider deletion from absence.
"""
import hmac
import re
from pydantic import Field, StrictInt, StrictBool, ValidationError, model_validator

from . import enterprise_rest_feed as rest
from . import enterprise_connector_crosswalk as crosswalk
from . import enterprise_retained_integrity as integrity
from . import event_log_workspace as ws

FORMAT = "owg_enterprise_case_exclusion_v1"
MAX_INPUT = 100
MAX_KEYS = 5000
KEY = re.compile(r"[a-f0-9]{64}\Z")
SUPPORTED = crosswalk.SUPPORTED | {"zendesk"}


class Case(rest.Strict):
    native_case_id: str = Field(min_length=1,max_length=200)
    native_type: str | None = Field(default=None,min_length=1,max_length=80)

    @model_validator(mode="after")
    def valid(self):
        for v in (self.native_case_id,self.native_type):
            if v is not None and (v!=v.strip() or any(ord(c)<32 or 127<=ord(c)<=159
                    or 0xD800<=ord(c)<=0xDFFF or ord(c) in (0xFFFE,0xFFFF) for c in v)):
                raise ValueError("invalid native identifier")
        return self


class Review(rest.Strict):
    profile_id: str = Field(pattern=r"^rest_[a-f0-9]{24}$")
    revision: StrictInt = Field(ge=1)
    cases: list[Case] = Field(min_length=1,max_length=MAX_INPUT)


class Apply(Review):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_exact_exclusions: StrictBool
    confirm_prospective_only: StrictBool
    confirm_dispatch_reauthorization: StrictBool


def require(ok, code, status=409):
    if not ok:
        raise rest.FeedError(code,status)


def parse(model,value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_case_exclusion_request",422) from None


def keys(p):
    values=p.get("excluded_case_keys",[])
    require(isinstance(values,list) and len(values)<=MAX_KEYS
            and all(isinstance(v,str) and KEY.fullmatch(v) for v in values)
            and len(set(values))==len(values),"invalid_case_exclusion_state")
    return set(values)


def resolve(p,row):
    provider=p.get("provider","rest")
    require(provider in SUPPORTED,"case_exclusion_provider_not_supported",422)
    try:
        if provider=="zendesk":
            from .zendesk_ticket_audit_feed import ID
            if row.native_type is not None or not ID.fullmatch(row.native_case_id) or row.native_case_id not in p["config"]["ticket_ids"]:
                raise ValueError()
            value=rest._key(p,"zendesk_case",row.native_case_id)
        else:
            value=crosswalk.native_case(p,row)
        if not KEY.fullmatch(value):
            raise ValueError()
        return value
    except Exception:
        raise rest.FeedError("native_case_outside_reviewed_scope",422) from None


def _plan(c,spec):
    p=rest._get(c,spec.profile_id)
    require(p["revision"]==spec.revision,"profile_changed")
    require(p["status"]!="dataset_deleted","retained_dataset_deleted_create_new_profile")
    require(not p.get("integrity_hold"),"retained_integrity_hold_requires_reviewed_release")
    require(not p.get("run_id"),"case_exclusion_run_unreconciled")
    existing=keys(p)
    requested=[resolve(p,row) for row in spec.cases]
    require(len(set(requested))==len(requested),"duplicate_canonical_case_exclusion",422)
    added=set(requested)-existing
    require(len(existing|added)<=MAX_KEYS,"case_exclusion_capacity_exceeded")
    # Prove absence in the actual retained case index before allowing a
    # prospective exclusion. No selective surgery on derived evidence.
    retained_count=0
    audit_state="no_retained_dataset"
    if p["log_id"]:
        report=integrity.audit(c,p)
        audit_state=report["status"]
        if audit_state=="structurally_consistent":
            ids=["src:"+p["profile_id"]+":"+v for v in sorted(added)]
            if ids:
                retained_count=c.execute("SELECT count(*) FROM objects WHERE log_id=? AND object_id IN ("+
                    ",".join("?" for _ in ids)+")",[p["log_id"],*ids]).fetchone()[0]
        else:
            retained_count=None
    eligible=bool(added) and retained_count==0
    result={"format":FORMAT,"profile_id":p["profile_id"],"provider":p.get("provider","rest"),
            "revision":p["revision"],"next_revision":p["revision"]+1 if eligible else None,
            "requested_cases":len(requested),"already_excluded_cases":len(set(requested)&existing),
            "new_exclusions":len(added),"retained_matches":retained_count,
            "total_exclusions_after_apply":len(existing|added),"retained_integrity_state":audit_state,
            "eligible":eligible,"will_disable_feed":True,"retained_dataset_modified":False,
            "provider_reads_performed":False,"source_erasure_performed":False,
            "remote_publications_withdrawn":False,"new_raw_source_identifiers_persisted":False}
    digest=rest._digest({"review":result,"private_profile_sha256":rest._digest(p),
                         "requested_keys":sorted(requested)})
    return p,existing|added,{**result,"plan_sha256":digest}


def preview(value):
    spec=parse(Review,value)
    with ws._LOCK:
        c=rest._connect()
        try:
            return _plan(c,spec)[2]
        finally:
            c.close()


def apply(value,*,_transaction_hook=None):
    spec=parse(Apply,value)
    require(spec.confirm_exact_exclusions and spec.confirm_prospective_only and
            spec.confirm_dispatch_reauthorization,"explicit_case_exclusion_review_required",403)
    with ws._LOCK:
        c=rest._connect()
        try:
            c.execute("BEGIN")
            try:
                p,new,plan=_plan(c,spec)
                require(hmac.compare_digest(spec.plan_sha256,plan["plan_sha256"]),"case_exclusion_review_stale")
                require(plan["eligible"],"case_exclusion_ineligible_existing_evidence_or_no_change")
                p.update(excluded_case_keys=sorted(new),revision=p["revision"]+1,
                         enabled=False,status="disabled")
                rest._put(c,p)
                if _transaction_hook is not None:
                    _transaction_hook(c)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return {**plan,"applied":True,"enabled":False,"revision":p["revision"],
                    "separate_enable_and_reauthorization_required":True}
        finally:
            c.close()


def filter_rows(p,rows):
    """Apply at canonical retained boundary, with immutable admitted pins."""
    excluded=keys(p)
    if not excluded:
        return rows,0
    require(all(isinstance(row,(list,tuple)) and len(row)==5 and isinstance(row[1],str)
                and KEY.fullmatch(row[1]) for row in rows),"invalid_case_exclusion_projection")
    kept=[row for row in rows if row[1] not in excluded]
    return kept,len(rows)-len(kept)


def inventory():
    with ws._LOCK:
        c=rest._connect()
        try:
            rows=[]
            for (pid,) in c.execute("SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall():
                p=rest._get(c,pid)
                rows.append({"profile_id":pid,"provider":p.get("provider","rest"),"revision":p["revision"],
                             "enabled":p["enabled"],"status":p["status"],"supported":p.get("provider","rest") in SUPPORTED,
                             "excluded_cases":len(keys(p)),"active_or_unreconciled_run":bool(p.get("run_id")),
                             "integrity_hold":bool(p.get("integrity_hold"))})
            return {"format":FORMAT,"profiles":rows,"max_input":MAX_INPUT,"max_keys":MAX_KEYS,
                    "provider_reads_performed":False,"raw_identifiers_returned":False}
        finally:
            c.close()
