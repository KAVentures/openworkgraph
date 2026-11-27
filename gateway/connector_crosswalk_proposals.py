"""Exact crosswalk proposal generation from current published snapshots.

A keyed equality is a candidate based on operator input, not verified
business identity. Ambiguous same-source groups are withheld by default.
Match tags and key checks are never returned or persisted.
"""
from collections import defaultdict, Counter
from itertools import combinations
from pydantic import Field,StrictBool,model_validator
from shared.connector_crosswalk import Crosswalk,artifact_digest
from shared.connector_event_snapshot import digest
from .connector_case_review import Scope,Plan,MAX_GROUPS,MAX_MEMBERS
from .connector_jobs import JobError

MAX_REQUEST=3*1024*1024
MAX_INPUT_ROWS=10000


class Propose(Scope):
    crosswalks:list[Crosswalk]=Field(min_length=2,max_length=10)
    confirm_tag_processing:StrictBool
    include_ambiguous:StrictBool=False
    include_unlinked:StrictBool=True

    @model_validator(mode="after")
    def validate_artifacts(self):
        if len({a.job_id for a in self.crosswalks})!=len(self.crosswalks):
            raise ValueError("one crosswalk per selected job")
        if {a.job_id for a in self.crosswalks}!={p.job_id for p in self.snapshots}:
            raise ValueError("crosswalks must match exact selected jobs")
        if len({(a.batch_id,a.domain,a.key_check) for a in self.crosswalks})!=1:
            raise ValueError("crosswalk batch, domain or key mismatch")
        if sum(len(a.rows) for a in self.crosswalks)>MAX_INPUT_ROWS:
            raise ValueError("crosswalk row budget exceeded")
        return self


def propose(reviewed,org,spec,principal):
    if not spec.confirm_tag_processing:
        raise JobError("explicit_crosswalk_tag_processing_required",403)
    with reviewed.jobs._tx(org) as c:
        loaded=reviewed._read(c,org,spec)
        cases,manifests,providers,count,scope,expires=loaded
        pins={p.job_id:p for p in spec.snapshots}
        groups=defaultdict(list);artifacts=[]
        for a in sorted(spec.crosswalks,key=lambda x:x.job_id):
            p=pins[a.job_id]
            if (a.grant_id,a.publication_revision,a.manifest_sha256)!=(p.grant_id,p.publication_revision,p.manifest_sha256):
                raise JobError("crosswalk_publication_pin_mismatch")
            sha=artifact_digest(a)
            for row in a.rows:
                if (a.job_id,row.case_id) not in cases:
                    raise JobError("crosswalk_case_missing_from_exact_snapshot")
                groups[row.match_tag].append((a.job_id,row.case_id,a.basis,sha))
            artifacts.append({"job_id":a.job_id,"artifact_sha256":sha,"row_count":len(a.rows),"basis":a.basis})
        eligible=[];single=ambiguous=ambiguous_members=0
        # Exact source-local coverage, not a business-identity confidence score.
        totals=Counter(job for job,_ in cases)
        mapped=Counter()
        for a in spec.crosswalks:
            mapped[a.job_id]=len(a.rows)
        source_stats={job:{"job_id":job,"source_cases":total,"mapped_cases":mapped[job],
                          "unmapped_cases":total-mapped[job],"single_source_cases":0,
                          "ambiguous_cases":0,"candidate_cases":0}
                      for job,total in sorted(totals.items())}
        pairs=Counter()
        pair_members=defaultdict(set)
        # The weakest input assertion governs the candidate group's basis.
        rank={"operator_assertion":0,"reviewed_business_record":1,"reviewed_native_reference":2}
        for tag,group in groups.items():
            n=len({m[0] for m in group})
            if n<2:
                single+=len(group)
                for job,_,_,_ in group:source_stats[job]["single_source_cases"]+=1
                continue
            if len(group)>n:
                ambiguous+=1;ambiguous_members+=len(group)
                for job,_,_,_ in group:source_stats[job]["ambiguous_cases"]+=1
                if not spec.include_ambiguous:
                    continue
            if len(group)>20:
                raise JobError("crosswalk_candidate_group_too_large")
            for job,_,_,_ in group:source_stats[job]["candidate_cases"]+=1
            by_source=defaultdict(list)
            for job,case,_,_ in group:by_source[job].append(case)
            for a,b in combinations(sorted(by_source),2):
                pairs[a,b]+=1
                pair_members[a,b].update((a,case) for case in by_source[a])
                pair_members[a,b].update((b,case) for case in by_source[b])
            members=[{"job_id":job,"case_id":case} for job,case,_,_ in sorted(group)]
            evidence=digest({"scope_sha256":scope,"members":members,
                             "artifacts":sorted({m[3] for m in group})})
            eligible.append({"members":members,"basis":min((m[2] for m in group),key=rank.__getitem__),
                             "evidence_sha256":evidence})
        eligible.sort(key=lambda g:[(m["job_id"],m["case_id"]) for m in g["members"]])
        if len(eligible)>MAX_GROUPS or sum(len(g["members"]) for g in eligible)>MAX_MEMBERS:
            raise JobError("crosswalk_candidate_budget_exceeded")
        summary={"input_rows":sum(len(a.rows) for a in spec.crosswalks),"input_cases":len(cases),
                 "unmapped_source_cases":len(cases)-sum(len(a.rows) for a in spec.crosswalks),
                 "single_source_members":single,"ambiguous_groups":ambiguous,"ambiguous_members":ambiguous_members,
                 "candidate_groups":len(eligible),"candidate_members":sum(len(g["members"]) for g in eligible),
                 "ambiguous_candidates_included":spec.include_ambiguous}
        coverage={"sources":list(source_stats.values()),
                  "source_pairs":[{"left_job_id":a,"right_job_id":b,"candidate_groups":pairs[a,b],
                                   "left_candidate_cases":sum(j==a for j,_ in pair_members[a,b]),
                                   "right_candidate_cases":sum(j==b for j,_ in pair_members[a,b])}
                                  for a,b in combinations(sorted(totals),2)],
                  "ambiguous_counts_overlap_candidates":spec.include_ambiguous,
                  "case_counts_not_event_counts":True}
        plan=None;review=None
        if eligible:
            plan=Plan.model_validate({"snapshots":[p.model_dump() for p in spec.snapshots],
                  "confirm_inspection":True,"groups":eligible,"confirm_many_to_one":spec.include_ambiguous,
                  "include_unlinked":spec.include_unlinked})
            _,review=reviewed._compute(org,plan,loaded)
        reviewed._fresh(expires)
        reviewed._audit(c,org,spec,principal,"crosswalk_proposed",{"scope_sha256":scope,
                 "candidate_groups":len(eligible),"input_rows":summary["input_rows"],"ambiguous_groups":ambiguous})
        return {"format":"openworkgraph.crosswalk-proposals.v1","scope_sha256":scope,"expires_at":expires,
                "artifacts":artifacts,"summary":summary,"coverage":coverage,"plan":plan.model_dump() if plan else None,
                "review":review,"operator_review_required":True,"business_identity_independently_verified":False,
                "raw_native_identifiers_received":False,"match_tags_returned":False,"crosswalk_artifacts_persisted":False}
