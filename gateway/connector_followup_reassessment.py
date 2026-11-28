"""Reassess completed follow-ups against independently selected later evidence.

Exact reports are recomputed while both sets of publication grants remain
live. Case and coded-selector correspondence is explicitly human asserted.
A successful sample comparison is never verified corrective action.
"""
from datetime import datetime
from collections import Counter
import re
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_jobs import JOB,JobError
from .connector_exception_followups import Followups,require
from .connector_linked_conformance import Review,_review
from .connector_case_review import group_key

FORMAT="openworkgraph.followup-reassessment.v1"
MAX_RECEIPTS=10


class JobMap(Strict):
    before:str=Field(pattern=JOB)
    after:str=Field(pattern=JOB)


class CaseMap(Strict):
    before:str=Field(pattern=DIGEST)
    after:str=Field(pattern=DIGEST)


class Preview(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    expected_packet_revision:StrictInt=Field(ge=1)
    baseline:Review
    subsequent:Review
    jobs:list[JobMap]=Field(min_length=2,max_length=10)
    cases:list[CaseMap]=Field(min_length=1,max_length=50)
    confirm_case_and_selector_correspondence:StrictBool

    @model_validator(mode="after")
    def exact(self):
        for mapping in (self.jobs,self.cases):
            if len({x.before for x in mapping})!=len(mapping) or len({x.after for x in mapping})!=len(mapping):
                raise ValueError("one-to-one mapping required")
        for review in (self.baseline,self.subsequent):
            if review.offset or review.filter_status!="all":
                raise ValueError("complete unfiltered comparison required")
        return self


class Apply(Preview):
    expected_reassessment_sha256:str=Field(pattern=DIGEST)
    confirm_record_reassessment:StrictBool
    confirm_sample_only:StrictBool


def stamp(value):
    return datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()


def validate_rules(before,after,mapping,old_manifests,new_manifests,required):
    old={r.rule_id:r.model_dump() for r in before.rules}
    new={r.rule_id:r.model_dump() for r in after.rules}
    require(set(new)==required and required<=set(old),"reassessment_rule_population_changed",422)
    approvals=set()
    forward={};reverse={}
    for rid in sorted(required):
        a,b=old[rid],new[rid]
        require(all(a[k]==b[k] for k in ("kind","minimum","maximum")),
                "reassessment_rule_semantics_changed",422)
        for side in ("left","right"):
            x,y=a[side],b[side]
            require((x is None)==(y is None),"reassessment_rule_semantics_changed",422)
            if x is None:
                continue
            require(mapping[x["job_id"]]==y["job_id"],"reassessment_selector_source_changed",422)
            mode=old_manifests[x["job_id"]]["activity_mode"]
            require(mode==new_manifests[y["job_id"]]["activity_mode"],"reassessment_activity_mode_changed",422)
            if mode=="labels":
                require(x["activity"]==y["activity"],"reassessment_activity_label_changed",422)
            else:
                require(re.fullmatch(r"A_[a-f0-9]{64}",y["activity"]) is not None,
                        "reassessment_invalid_activity_code",422)
            left=(x["job_id"],x["activity"]);right=(y["job_id"],y["activity"])
            require(forward.get(left,right)==right and reverse.get(right,left)==left,
                    "reassessment_inconsistent_selector_mapping",422)
            forward[left]=right;reverse[right]=left
            # Codes are scoped pseudonyms and are not assumed comparable.
            # The operator explicitly asserts semantic correspondence.
            approvals.add((y["job_id"],y["activity"]))
    return approvals


def classify(before,after):
    if after=="satisfied":
        return "satisfied_in_later_sample"
    if after=="not_satisfied":
        return "not_satisfied_in_later_sample"
    if after=="indeterminate":
        return "indeterminate_in_later_sample"
    return "not_applicable_in_later_sample"


class Reassessments(Followups):
    def _compare(self,c,org,d,p,spec,principal):
        require(spec.confirm_case_and_selector_correspondence,"explicit_reassessment_correspondence_required",403)
        require(p["state"]=="completed_unverified","followup_not_completed")
        require(self._current(c,org,d,p),"followup_item_pins_changed")
        require(bool(p["history"]) and p["history"][0]["state"]=="completed_unverified",
                "followup_completion_history_missing")
        completed=p["history"][0]["at"]
        require(spec.baseline.expected_conformance_sha256 in (None,d["conformance_sha256"]),
                "reassessment_baseline_conformance_changed")
        baseline=spec.baseline.model_copy(update={"expected_conformance_sha256":d["conformance_sha256"]})
        require(baseline.expected_review_sha256==d["review_sha256"],"reassessment_baseline_review_changed")
        require(sorted((x.model_dump() for x in baseline.snapshots),key=lambda x:x["job_id"])==d["pins"],
                "reassessment_baseline_pins_changed")
        reviewed=self.queues.reviewed
        old_loaded=reviewed._read(c,org,baseline)
        new_loaded=reviewed._read(c,org,spec.subsequent)
        old={x["job_id"]:x["manifest"] for x in old_loaded[1]}
        new={x["job_id"]:x["manifest"] for x in new_loaded[1]}
        mapping={x.before:x.after for x in spec.jobs}
        require(set(mapping)==set(old) and set(mapping.values())==set(new) and not set(old)&set(new),
                "reassessment_exact_distinct_source_mapping_required",422)
        for a,b in mapping.items():
            require(old[a]["provider"]==new[b]["provider"],"reassessment_provider_changed",422)
            require(old[a]["activity_mode"]==new[b]["activity_mode"],"reassessment_activity_mode_changed",422)
            require(stamp(new[b]["since"])>=max(stamp(old[a]["until"]),completed)
                    and stamp(new[b]["until"])<=self.jobs.clock(),
                    "reassessment_requires_completed_post_followup_window",422)
        required={x["rule_id"] for x in p["items"]}
        approved=validate_rules(baseline,spec.subsequent,mapping,old,new,required)
        cases={x.before:x.after for x in spec.cases}
        require(set(cases)=={x["linked_case_id"] for x in p["items"]},
                "reassessment_case_population_changed",422)
        # Equal reviewed group shape does not establish identity, but prevents
        # comparing an exception in a multi-source case with a partial case.
        def shapes(spec,loaded):
            return {digest({"scope_sha256":loaded[4],"members":group_key(g)}):
                    (Counter(job for job,_ in group_key(g)),g.basis) for g in spec.groups}
        original_shapes=shapes(baseline,old_loaded)
        later_shapes=shapes(spec.subsequent,new_loaded)
        for a,b in cases.items():
            require(a in original_shapes and b in later_shapes,"reassessment_later_case_or_rule_missing",422)
            before_shape,basis=original_shapes[a]
            after_shape,after_basis=later_shapes[b]
            require(Counter({mapping[k]:v for k,v in before_shape.items()})==after_shape and basis==after_basis,
                    "reassessment_case_source_population_changed",422)
        before=_review(reviewed,org,baseline,principal,worklist=False,_conn=c,_all_groups=True)
        after=_review(reviewed,org,spec.subsequent,principal,worklist=False,_conn=c,_all_groups=True,
                      _approved_selectors=approved)
        bchecks={(g["linked_case_id"],r["rule_id"]):r for g in before["groups"] for r in g["checks"]}
        achecks={(g["linked_case_id"],r["rule_id"]):r for g in after["groups"] for r in g["checks"]}
        rows=[]
        for item in p["items"]:
            key=(item["linked_case_id"],item["rule_id"])
            b=bchecks.get(key)
            a=achecks.get((cases[key[0]],key[1]))
            require(b is not None and b["status"]==item["status"] and b["reason"]==item["reason"],
                    "reassessment_baseline_exception_changed")
            require(a is not None,"reassessment_later_case_or_rule_missing",422)
            rows.append({"item_id":item["item_id"],"rule_id":key[1],"baseline_linked_case_id":key[0],
                         "subsequent_linked_case_id":cases[key[0]],"baseline_status":b["status"],
                         "subsequent_status":a["status"],"subsequent_reason":a["reason"],
                         "comparison":classify(b["status"],a["status"])})
        rows.sort(key=lambda x:x["item_id"])
        summary={k:sum(r["comparison"]==k for r in rows) for k in (
            "satisfied_in_later_sample","not_satisfied_in_later_sample",
            "indeterminate_in_later_sample","not_applicable_in_later_sample")}
        expires=min(d["expires_at"],old_loaded[-1],new_loaded[-1])
        reviewed._fresh(expires)
        result={"format":FORMAT,"queue_id":d["queue_id"],"queue_revision":d["revision"],
                "packet_id":p["packet_id"],"packet_revision":p["revision"],
                "completion_reported_at":completed,"baseline_conformance_sha256":before["conformance_sha256"],
                "subsequent_conformance_sha256":after["conformance_sha256"],
                "subsequent_review_sha256":after["review_sha256"],"expires_at":expires,
                "comparison_assumptions_sha256":digest({"jobs":sorted(mapping.items()),"cases":sorted(cases.items()),
                    "rules":sorted((r.model_dump() for r in spec.subsequent.rules),key=lambda x:x["rule_id"])}),
                "rows":rows,"summary":summary,"case_correspondence_independently_verified":False,
                "selector_correspondence_independently_verified":False,"source_remediation_verified":False,
                "source_completeness_verified":False,"causal_effect_verified":False,
                "execution_authorized":False,
                "interpretation":"Later selected evidence only; a satisfied observation does not establish correction or causation."}
        result["comparison_content_sha256"]=digest({k:result[k] for k in ("packet_id",
            "baseline_conformance_sha256","subsequent_conformance_sha256",
            "comparison_assumptions_sha256","rows")})
        return {**result,"reassessment_sha256":digest(result)}

    def compare(self,org,queue,packet,spec,principal):
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet,spec.expected_packet_revision)
            return self._compare(c,org,d,p,spec,principal)

    def record(self,org,queue,packet,spec,principal):
        require(spec.confirm_record_reassessment and spec.confirm_sample_only,
                "explicit_reassessment_record_approval_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet,spec.expected_packet_revision)
            result=self._compare(c,org,d,p,spec,principal)
            require(result["reassessment_sha256"]==spec.expected_reassessment_sha256,
                    "reassessment_review_changed")
            receipts=p.get("reassessment_receipts",[])
            require(len(receipts)<MAX_RECEIPTS,"reassessment_receipt_limit")
            require(not any(x.get("comparison_content_sha256")==result["comparison_content_sha256"] for x in receipts),
                    "reassessment_already_recorded")
            # Only a digest and time survive in the old queue. No new-source
            # outcomes, pins, labels or case IDs are persisted here.
            receipt={"reassessment_sha256":result["reassessment_sha256"],
                     "comparison_content_sha256":result["comparison_content_sha256"],
                     "recorded_at":self.jobs.clock(),"source_remediation_verified":False}
            p["reassessment_receipts"]=[receipt,*receipts]
            p["revision"]+=1
            self._store(c,org,d,principal,"followup_reassessment_recorded",receipt)
            self.queues.reviewed._fresh(result["expires_at"])
            return {"queue":self.queues._public(c,org,d),"packet":self._public(c,org,d,p),
                    "receipt":receipt,"report":result}
