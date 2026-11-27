"""Recalculate the complete v1 quality report against verified original rows.

The report's declared identity, revision and expiry are not authenticated.
Only its internal consistency, sample metrics and declared gate outcomes
are established. Do not use the report as a signature or compliance proof.
"""
from collections import defaultdict
from math import isfinite
from typing import Literal
from pydantic import Field,StrictInt,StrictFloat,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest,MAX_EVENTS
from shared.connector_publication_quality import summarize,evaluate

FORMAT="openworkgraph.connector-publication-quality.v1"
INTERPRETATION="Thresholds apply to the selected disclosed sample only. Singleton cases and repeated labels may be legitimate. Reported timestamp ties are not verified simultaneous actions. SAP business dates do not establish occurrence order. Source-local cases are not distinct enterprise business cases."


class Policy(Strict):
    min_events:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)
    min_cases:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)
    max_singleton_case_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_single_activity_case_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_largest_activity_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_timestamp_tie_event_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    min_cases_eligible_for_reported_order:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)

    @model_validator(mode="after")
    def configured(self):
        if not any(v is not None for v in self.model_dump().values()):
            raise ValueError("at least one explicit evidence gate required")
        return self


class SourceDeclaration(Strict):
    job_id:str=Field(pattern=r"^cj_[a-f0-9]{32}$")
    grant_id:str=Field(pattern=r"^cep_[a-f0-9]{32}$")
    publication_revision:StrictInt=Field(ge=1)
    manifest_sha256:str=Field(pattern=DIGEST)
    expires_at:StrictInt|StrictFloat=Field(gt=0)
    policy_origin:Literal["source_override","default"]
    policy:Policy

    @model_validator(mode="after")
    def finite(self):
        if not isfinite(self.expires_at):
            raise ValueError("invalid expiry")
        return self


def calculate(organization_id,default_policy,declared,bundle):
    """Build the same report contract as #1602 from typed source declarations."""
    from shared.connector_bundle_import import Bundle
    if not isinstance(bundle,Bundle):
        raise ValueError("quality requires complete original source rows")
    by_job=defaultdict(list)
    for e in bundle.events:
        by_job[e.source_namespace].append(e.model_dump(exclude={"source_namespace"}))
    originals={s.job_id:s for s in bundle.manifests}
    if len({s.job_id for s in declared})!=len(declared) or set(originals)!={s.job_id for s in declared}:
        raise ValueError("quality publication scope mismatch")
    sources=[]
    for src in sorted(declared,key=lambda x:x.job_id):
        original=originals[src.job_id]
        if src.grant_id!=original.grant_id or src.manifest_sha256!=original.manifest_sha256:
            raise ValueError("quality source manifest mismatch")
        if src.policy_origin=="default" and src.policy!=default_policy:
            raise ValueError("quality default policy mismatch")
        m=original.manifest
        policy=src.policy.model_dump()
        metrics=summarize(by_job[src.job_id],day_precision=m.provider=="sap")
        checks=evaluate(metrics,policy)
        status="fail" if any(g["status"]=="fail" for g in checks) else (
            "indeterminate" if any(g["status"]=="indeterminate" for g in checks) else "pass")
        sources.append({**src.model_dump(),"provider":m.provider,
                        "window":{"since":m.since,"until":m.until,"change_checkpoint":m.checkpoint},
                        "activity_mode":m.activity_mode,"policy":policy,"metrics":metrics,"gates":checks,"status":status})
    summary={"sources":len(sources),"events":sum(x["metrics"]["events"] for x in sources),
             "source_local_cases":sum(x["metrics"]["cases"] for x in sources),
             "passing_sources":sum(x["status"]=="pass" for x in sources),
             "failing_sources":sum(x["status"]=="fail" for x in sources),
             "indeterminate_sources":sum(x["status"]=="indeterminate" for x in sources)}
    report={"format":FORMAT,"organization_id":organization_id,"summary":summary,"sources":sources,
            "default_policy":default_policy.model_dump(),
            "expires_at":min(x["expires_at"] for x in sources),
            "all_selected_sources_pass":all(x["status"]=="pass" for x in sources),
            "provider_completeness_verified":False,"business_identity_verified":False,
            "physical_occurrence_times_verified":False,"cross_source_identity_inferred":False,
            "interpretation":INTERPRETATION}
    return {**report,"quality_sha256":digest(report)}


def verify_quality(value,bundle,*,expected_org=None,expected_pins=None):
    """Check all submitted fields, not just the supplied digest or pass flag."""
    try:
        if not isinstance(value,dict) or value.get("format")!=FORMAT:
            raise ValueError("unsupported quality report")
        from re import fullmatch
        org=value["organization_id"]
        if not isinstance(org,str) or not fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}",org):
            raise ValueError("invalid organization")
        if expected_org is not None and org!=expected_org:
            raise ValueError("quality organization mismatch")
        sources=value["sources"]
        if not isinstance(sources,list) or not 1<=len(sources)<=10:
            raise ValueError("quality source budget")
        keys=SourceDeclaration.model_fields
        declarations=[SourceDeclaration.model_validate({k:s[k] for k in keys}) for s in sources]
        if expected_pins is not None:
            actual=[s.model_dump(exclude={"policy_origin","policy"}) for s in declarations]
            if digest(sorted(actual,key=lambda p:p["job_id"]))!=digest(sorted(expected_pins,key=lambda p:p["job_id"])):
                raise ValueError("quality and linkage publication pins differ")
        default=Policy.model_validate(value["default_policy"])
        report=calculate(org,default,declarations,bundle)
        # Compare canonical hashes to distinguish JSON booleans from integers,
        # reject unknown fields, and bind the complete supplied report.
        if digest(value)!=digest(report):
            raise ValueError("quality report differs from recalculated metrics")
        if not report["all_selected_sources_pass"]:
            raise ValueError("quality_gates_not_satisfied")
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError) as exc:
        if str(exc)=="quality_gates_not_satisfied":
            raise ValueError("quality_gates_not_satisfied") from None
        raise ValueError("quality_report_integrity_failure") from None
    policy_pin=digest({"organization_id":org,"default_policy":default.model_dump(),
        "source_policies":[{"job_id":s.job_id,"origin":s.policy_origin,"policy":s.policy.model_dump()}
                           for s in sorted(declarations,key=lambda x:x.job_id)]})
    return report,policy_pin
