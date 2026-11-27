"""Pure deterministic cross-source review and row projection, shared by Gateway and offline verifier."""
from collections import Counter, defaultdict
from datetime import datetime
from shared.connector_event_snapshot import digest

FORMAT="openworkgraph.reviewed-connector-cases.v1"

def group_key(group):
    return sorted((m.job_id,m.case_id) for m in group.members)

def elapsed(first,last):
    return int((datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds())

def compute_review(spec,loaded):
    cases,manifests,providers,count,scope,expires=loaded
    assigned={};groups=[];basis_counts=Counter();linked_events=0
    for group in sorted(spec.groups,key=group_key):
        members=group_key(group)
        if any(k not in cases for k in members):
            raise ValueError("case_member_missing_from_exact_snapshot")
        identity=digest({"scope_sha256":scope,"members":members})
        for k in members:
            assigned[k]=identity
        basis_counts[group.basis]+=1
        by_source=defaultdict(list)
        events=[]
        for k in members:
            job,case=k
            for e in cases[k]:
                events.append((e["observed_at"],job,e["event_id"]))
                by_source[job].append(e["observed_at"])
        events.sort();linked_events+=len(events)
        sources=[{"job_id":job,"provider":providers[job],"event_count":len(ts),
                  "first_observed":min(ts),"last_observed":max(ts)}
                 for job,ts in sorted(by_source.items())]
        day_precision=any(providers[job]=="sap" for job in by_source)
        tied_cross_source=0;ordered_switches=0
        buckets=defaultdict(set)
        for ts,job,_ in events:
            buckets[ts].add(job)
        times=sorted(buckets)
        tied_cross_source=sum(len(buckets[t])>1 for t in times)
        if not day_precision:
            ordered_switches=sum(len(buckets[a])==len(buckets[b])==1 and buckets[a]!=buckets[b]
                                 for a,b in zip(times,times[1:]))
        groups.append({"linked_case_id":identity,"basis":group.basis,
                       "members":[{"job_id":j,"case_id":k,"events":len(cases[j,k])} for j,k in members],
                       "sources":sources,"events":len(events),"first_observed":times[0],"last_observed":times[-1],
                       "observed_span_seconds":None if day_precision else elapsed(times[0],times[-1]),
                       "cross_source_timestamp_ties":tied_cross_source,
                       "strict_adjacent_source_switches":None if day_precision else ordered_switches,
                       "day_precision_present":day_precision,"same_source_many_to_one":len(members)>len(by_source),
                       "business_identity_independently_verified":False,
                       **({"evidence_sha256":group.evidence_sha256} if group.evidence_sha256 else {})})
    unlinked_cases=len(cases)-len(assigned)
    unlinked_events=count-linked_events
    plan={"format":FORMAT,"scope_sha256":scope,"groups":groups,"include_unlinked":spec.include_unlinked,
          "confirm_many_to_one":spec.confirm_many_to_one}
    review=digest(plan)
    summary={"source_jobs":len(manifests),"input_cases":len(cases),"input_events":count,
             "linked_groups":len(groups),"linked_input_cases":len(assigned),"linked_events":linked_events,
             "unlinked_cases":unlinked_cases,"unlinked_events":unlinked_events,
             "export_cases":len(groups)+(unlinked_cases if spec.include_unlinked else 0),
             "export_events":linked_events+(unlinked_events if spec.include_unlinked else 0),
             "case_count_reduction":len(assigned)-len(groups),
             "day_precision_groups":sum(g["day_precision_present"] for g in groups),
             "groups_with_timestamp_ties":sum(bool(g["cross_source_timestamp_ties"]) for g in groups),
             "many_to_one_groups":sum(g["same_source_many_to_one"] for g in groups),
             "basis_counts":dict(sorted(basis_counts.items()))}
    return assigned,{"format":FORMAT,"scope_sha256":scope,"review_sha256":review,"expires_at":expires,
                     "manifests":manifests,"summary":summary,"groups":groups,
                     "include_unlinked":spec.include_unlinked,"business_identity_independently_verified":False,
                     "source_completeness_verified":False,"automatic_links_inferred":False,
                     "chronological_switches_are_not_verified_handoffs":True}


def project_rows(spec,loaded,assigned):
    cases,_,_,_,scope,_=loaded
    rows=[]
    for (job,case),events in sorted(cases.items()):
        linked=(job,case) in assigned
        if not linked and not spec.include_unlinked:
            continue
        target=assigned[(job,case)] if linked else digest({"scope_sha256":scope,"unlinked_member":[job,case]})
        for e in events:
            rows.append({"source_namespace":job,"event_id":e["event_id"],"case_id":target,
                         "activity":job+"."+e["activity"],"observed_at":e["observed_at"]})
    return sorted(rows,key=lambda r:(r["case_id"],r["observed_at"],r["source_namespace"],r["event_id"]))
