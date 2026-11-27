"""Guided, ephemeral reassessment mapping catalog and canonical request builder.

Neither a suggested source nor a matching group shape establishes business
identity. Operators must select every correspondence and separately approve
the exact recomputed comparison. No mapping is retained by discovery.
"""
from collections import Counter
import re
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_case_review import Plan,group_key
from .connector_followup_reassessment import Reassessments,Preview,JobMap,CaseMap,stamp
from .connector_linked_conformance import Review,_review
from .connector_exception_followups import require
from .connector_jobs import JOB

FORMAT="openworkgraph.guided-reassessment.v1"
MAX_CANDIDATES=500
MAX_ACTIVITIES=20000


class Discover(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    expected_packet_revision:StrictInt=Field(ge=1)
    baseline:Review
    subsequent:Plan
    expected_subsequent_review_sha256:str=Field(pattern=DIGEST)
    confirm_catalog_inspection:StrictBool

    @model_validator(mode="after")
    def full(self):
        if self.baseline.offset or self.baseline.filter_status!="all":
            raise ValueError("complete baseline required")
        return self


class SelectorMap(Strict):
    before_job:str=Field(pattern=JOB)
    before_activity:str=Field(min_length=1,max_length=160)
    after_job:str=Field(pattern=JOB)
    after_activity:str=Field(min_length=1,max_length=160)


class Build(Discover):
    expected_catalog_sha256:str=Field(pattern=DIGEST)
    jobs:list[JobMap]=Field(min_length=2,max_length=10)
    cases:list[CaseMap]=Field(min_length=1,max_length=50)
    selectors:list[SelectorMap]=Field(min_length=1,max_length=80)
    confirm_case_and_selector_correspondence:StrictBool

    @model_validator(mode="after")
    def exact(self):
        for mapping in (self.jobs,self.cases):
            if len({x.before for x in mapping})!=len(mapping) or len({x.after for x in mapping})!=len(mapping):
                raise ValueError("one-to-one mapping required")
        if len({(x.before_job,x.before_activity) for x in self.selectors})!=len(self.selectors):
            raise ValueError("duplicate selector mapping")
        if len({(x.after_job,x.after_activity) for x in self.selectors})!=len(self.selectors):
            raise ValueError("duplicate target selector")
        return self


def shapes(plan,loaded):
    out=[]
    for group in plan.groups:
        members=group_key(group)
        out.append({"linked_case_id":digest({"scope_sha256":loaded[4],"members":members}),
                    "basis":group.basis,"source_counts":dict(sorted(Counter(j for j,_ in members).items())),
                    "event_count":sum(len(loaded[0][j,c]) for j,c in members)})
    return sorted(out,key=lambda r:r["linked_case_id"])


def sources(loaded):
    return {p["job_id"]:{"job_id":p["job_id"],"provider":p["manifest"]["provider"],
            "activity_mode":p["manifest"]["activity_mode"],"since":p["manifest"]["since"],
            "until":p["manifest"]["until"],"event_count":p["manifest"]["event_count"]}
            for p in loaded[1]}


class Guided(Reassessments):
    def _catalog(self,c,org,d,p,spec,principal):
        require(spec.confirm_catalog_inspection,"explicit_reassessment_catalog_inspection_required",403)
        require(p["state"]=="completed_unverified","followup_not_completed")
        require(self._current(c,org,d,p),"followup_item_pins_changed")
        require(bool(p["history"]) and p["history"][0]["state"]=="completed_unverified",
                "followup_completion_history_missing")
        completed=p["history"][0]["at"]
        baseline=spec.baseline
        require(baseline.expected_conformance_sha256 in (None,d["conformance_sha256"]),
                "reassessment_baseline_conformance_changed")
        require(baseline.expected_review_sha256==d["review_sha256"],"reassessment_baseline_review_changed")
        require(sorted((x.model_dump() for x in baseline.snapshots),key=lambda x:x["job_id"])==d["pins"],
                "reassessment_baseline_pins_changed")
        baseline=baseline.model_copy(update={"expected_conformance_sha256":d["conformance_sha256"]})
        reviewed=self.queues.reviewed
        old_loaded=reviewed._read(c,org,baseline)
        new_loaded=reviewed._read(c,org,spec.subsequent)
        _,later=reviewed._compute(org,spec.subsequent,new_loaded)
        require(later["review_sha256"]==spec.expected_subsequent_review_sha256,
                "reassessment_subsequent_review_changed")
        before=_review(reviewed,org,baseline,principal,worklist=False,_conn=c,_all_groups=True)
        old,new=sources(old_loaded),sources(new_loaded)
        require(len(old)==len(new) and not set(old)&set(new),
                "reassessment_exact_distinct_source_mapping_required",422)
        for src in new.values():
            require(stamp(src["since"])>=completed and stamp(src["until"])<=self.jobs.clock(),
                    "reassessment_requires_completed_post_followup_window",422)
        candidates={}
        for job,a in old.items():
            candidates[job]=[b["job_id"] for b in new.values()
                if a["provider"]==b["provider"] and a["activity_mode"]==b["activity_mode"]
                and stamp(b["since"])>=stamp(a["until"])]
        required={x["rule_id"] for x in p["items"]}
        rules=sorted((r.model_dump() for r in baseline.rules if r.rule_id in required),key=lambda r:r["rule_id"])
        require({r["rule_id"] for r in rules}==required,"reassessment_rule_population_changed")
        needed={(s["job_id"],s["activity"]) for r in rules for s in (r["left"],r["right"]) if s}
        chosen={x["linked_case_id"] for x in p["items"]}
        original=[x for x in shapes(baseline,old_loaded) if x["linked_case_id"] in chosen]
        require({x["linked_case_id"] for x in original}==chosen,"reassessment_baseline_exception_changed")
        later_shapes=shapes(spec.subsequent,new_loaded)
        require(len(later_shapes)<=MAX_CANDIDATES,"reassessment_candidate_budget_exceeded")
        activities={job:Counter() for job in new}
        for group in spec.subsequent.groups:
            for job,case in group_key(group):
                activities[job].update(e["activity"] for e in new_loaded[0][job,case])
        require(sum(len(x) for x in activities.values())<=MAX_ACTIVITIES,"reassessment_activity_budget_exceeded")
        expires=min(d["expires_at"],old_loaded[-1],new_loaded[-1])
        result={"format":FORMAT,"queue_id":d["queue_id"],"queue_revision":d["revision"],
            "packet_id":p["packet_id"],"packet_revision":p["revision"],
            "baseline_conformance_sha256":before["conformance_sha256"],
            "subsequent_review_sha256":later["review_sha256"],"expires_at":expires,
            "original_sources":list(old.values()),"later_sources":list(new.values()),
            "source_candidates":candidates,"original_groups":original,"later_groups":later_shapes,
            "required_rules":rules,"required_selectors":[{"job_id":j,"activity":a} for j,a in sorted(needed)],
            "later_activities":{j:[{"activity":a,"count":n} for a,n in sorted(v.items())]
                                for j,v in sorted(activities.items())},
            "identity_independently_verified":False,"automatic_correspondence":False,
            "source_remediation_verified":False}
        reviewed._fresh(expires)
        return {**result,"catalog_sha256":digest(result)}

    def discover(self,org,queue,packet,spec,principal):
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet,spec.expected_packet_revision)
            return self._catalog(c,org,d,p,spec,principal)

    def build(self,org,queue,packet,spec,principal):
        require(spec.confirm_case_and_selector_correspondence,
                "explicit_reassessment_correspondence_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet,spec.expected_packet_revision)
            catalog=self._catalog(c,org,d,p,spec,principal)
            require(catalog["catalog_sha256"]==spec.expected_catalog_sha256,"reassessment_catalog_changed")
            mapping={x.before:x.after for x in spec.jobs}
            require(set(mapping)==set(catalog["source_candidates"]) and all(
                after in catalog["source_candidates"][before] for before,after in mapping.items()),
                "reassessment_source_mapping_outside_catalog",422)
            selectors={(x.before_job,x.before_activity):(x.after_job,x.after_activity) for x in spec.selectors}
            needed={(x["job_id"],x["activity"]) for x in catalog["required_selectors"]}
            require(set(selectors)==needed,"reassessment_selector_population_changed",422)
            rules=[]
            for rule in catalog["required_rules"]:
                rule=dict(rule)
                for side in ("left","right"):
                    if rule[side] is None:continue
                    original=rule[side]
                    job,activity=selectors[original["job_id"],original["activity"]]
                    require(job==mapping[original["job_id"]],"reassessment_selector_source_changed",422)
                    rule[side]={"job_id":job,"activity":activity}
                rules.append(rule)
            subsequent=Review.model_validate({**spec.subsequent.model_dump(),
                "expected_review_sha256":spec.expected_subsequent_review_sha256,
                "rules":rules,"confirm_conformance_review":True})
            value={"expected_queue_revision":d["revision"],"expected_packet_revision":p["revision"],
                "baseline":spec.baseline.model_dump(),"subsequent":subsequent.model_dump(),
                "jobs":[x.model_dump() for x in spec.jobs],"cases":[x.model_dump() for x in spec.cases],
                "confirm_case_and_selector_correspondence":True}
            request=Preview.model_validate(value)
            comparison=self._compare(c,org,d,p,request,principal)
            return {"request":request.model_dump(),"report":comparison,
                    "catalog_sha256":catalog["catalog_sha256"],"source_remediation_verified":False}
