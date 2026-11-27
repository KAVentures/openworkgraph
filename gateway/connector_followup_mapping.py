"""Read-only mapping workbench for explicitly reviewed later-sample comparisons."""
from collections import Counter
from pydantic import Field,StrictBool,StrictInt
from shared.connector_event_snapshot import Strict,DIGEST,digest
from .connector_case_review import Plan
from .connector_linked_conformance import Review,_review
from .connector_followup_reassessment import Reassessments,stamp,require

FORMAT="openworkgraph.followup-mapping-workbench.v1"
MAX_ACTIVITIES=200
MAX_CANDIDATES=500


class Later(Plan):
    expected_review_sha256:str=Field(pattern=DIGEST)




class WorkbenchRequest(Strict):
    expected_queue_revision:StrictInt=Field(ge=1)
    expected_packet_revision:StrictInt=Field(ge=1)
    baseline:Review
    subsequent:Later
    confirm_mapping_inspection:StrictBool


def public_source(x):
    m=x["manifest"]
    return {"job_id":x["job_id"],"provider":m["provider"],
            "activity_mode":m["activity_mode"],"since":m["since"],"until":m["until"],
            "event_count":m["event_count"],"case_count":m["case_count"]}


def group_shape(g):
    return dict(sorted(Counter(m["job_id"] for m in g["members"]).items()))


def compatible_sources(a,b,completed,now):
    return (a["job_id"]!=b["job_id"] and a["provider"]==b["provider"]
            and a["activity_mode"]==b["activity_mode"]
            and stamp(b["since"])>=max(stamp(a["until"]),completed)
            and stamp(b["until"])<=now)


def group_summary(g):
    return {"linked_case_id":g["linked_case_id"],"basis":g["basis"],
            "source_multiplicities":group_shape(g),"events":g["events"],
            "first_observed":g["first_observed"],"last_observed":g["last_observed"]}


class MappingWorkbench(Reassessments):
    def guidance(self,org,queue,packet,spec,principal):
        require(spec.confirm_mapping_inspection,"explicit_mapping_inspection_required",403)
        with self.jobs._tx(org) as c:
            d=self._load(c,org,queue,spec.expected_queue_revision)
            p=self._find(d,packet,spec.expected_packet_revision)
            require(p["state"]=="completed_unverified","followup_not_completed")
            require(self._current(c,org,d,p),"followup_item_pins_changed")
            require(bool(p["history"]) and p["history"][0]["state"]=="completed_unverified",
                    "followup_completion_history_missing")
            completed=p["history"][0]["at"]
            baseline=spec.baseline
            require(baseline.offset==0 and baseline.filter_status=="all",
                    "complete_unfiltered_baseline_required",422)
            require(baseline.expected_conformance_sha256 in (None,d["conformance_sha256"]),
                    "reassessment_baseline_conformance_changed")
            require(baseline.expected_review_sha256==d["review_sha256"],
                    "reassessment_baseline_review_changed")
            require(sorted((x.model_dump() for x in baseline.snapshots),key=lambda x:x["job_id"])==d["pins"],
                    "reassessment_baseline_pins_changed")
            baseline=baseline.model_copy(update={"expected_conformance_sha256":d["conformance_sha256"]})
            reviewed=self.queues.reviewed
            old_loaded=reviewed._read(c,org,baseline)
            new_loaded=reviewed._read(c,org,spec.subsequent)
            _,old_report=reviewed._compute(org,baseline,old_loaded)
            _,new_report=reviewed._compute(org,spec.subsequent,new_loaded)
            require(new_report["review_sha256"]==spec.subsequent.expected_review_sha256,
                    "cross_source_review_changed")
            old=[public_source(x) for x in old_loaded[1]]
            new=[public_source(x) for x in new_loaded[1]]
            require(len(old)==len(new) and not {x["job_id"] for x in old}&{x["job_id"] for x in new},
                    "reassessment_exact_distinct_source_mapping_required",422)
            result=_review(reviewed,org,baseline,principal,worklist=False,_conn=c,_all_groups=True)
            checks={(g["linked_case_id"],r["rule_id"]):r for g in result["groups"] for r in g["checks"]}
            for item in p["items"]:
                check=checks.get((item["linked_case_id"],item["rule_id"]))
                require(check is not None and check["status"]==item["status"] and check["reason"]==item["reason"],
                        "reassessment_baseline_exception_changed")
            required={i["linked_case_id"] for i in p["items"]}
            needed={i["rule_id"] for i in p["items"]}
            old_groups=[group_summary(g) for g in old_report["groups"] if g["linked_case_id"] in required]
            require({g["linked_case_id"] for g in old_groups}==required,
                    "reassessment_baseline_exception_changed")
            new_groups=[group_summary(g) for g in new_report["groups"]]
            require(len(new_groups)<=MAX_CANDIDATES,"mapping_candidate_budget_exceeded")
            rules=[r.model_dump() for r in baseline.rules if r.rule_id in needed]
            require({r["rule_id"] for r in rules}==needed,"reassessment_rule_population_changed")
            activity_counts={x["job_id"]:Counter() for x in new}
            for group in spec.subsequent.groups:
                for m in group.members:
                    activity_counts[m.job_id].update(e["activity"] for e in new_loaded[0][m.job_id,m.case_id])
            catalogs=[]
            for job,counter in sorted(activity_counts.items()):
                labels=sorted(counter)
                catalogs.append({"job_id":job,"total_distinct_activities":len(labels),
                    "truncated":len(labels)>MAX_ACTIVITIES,
                    "activities":[{"activity":a,"events":counter[a]} for a in labels[:MAX_ACTIVITIES]]})
            expires=min(old_loaded[-1],new_loaded[-1],d["expires_at"])
            out={"format":FORMAT,"queue_revision":d["revision"],"packet_revision":p["revision"],
                 "packet_id":p["packet_id"],"baseline_conformance_sha256":d["conformance_sha256"],
                 "subsequent_review_sha256":new_report["review_sha256"],"expires_at":expires,
                 "completed_at":completed,"baseline_sources":old,"subsequent_sources":new,
                 "source_candidates":[{"before":a["job_id"],"candidates":[b["job_id"] for b in new
                    if compatible_sources(a,b,completed,self.jobs.clock())]} for a in old],
                 "baseline_cases":old_groups,"subsequent_cases":new_groups,"rules":rules,
                 "activity_catalogs":catalogs,"identity_inferred":False,
                 "execution_authorized":False,"source_remediation_verified":False}
            out["guidance_sha256"]=digest(out)
            reviewed._audit(c,org,spec.subsequent,principal,"followup_mapping_inspected",
                {"guidance_sha256":out["guidance_sha256"],"candidate_groups":len(new_groups)})
            reviewed._fresh(expires)
            return out
