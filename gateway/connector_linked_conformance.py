"""Exact snapshot-pinned, owner-reviewed linked case conformance diagnostics."""
from collections import Counter, defaultdict
from contextlib import nullcontext
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,DIGEST,digest
from shared.connector_linked_conformance import index_events,evaluate,counts,group_status
from .connector_case_review import Plan,group_key
from .connector_jobs import JobError,JOB

FORMAT="openworkgraph.linked-case-conformance.v1"
MAX_RULES=40
MAX_CATALOG_PAGE=100


class Selector(Strict):
    job_id:str=Field(pattern=JOB)
    activity:str=Field(min_length=1,max_length=160)


class Rule(Strict):
    rule_id:str=Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
    kind:Literal["required","forbidden","count_range","cooccurs","precedes","response"]
    left:Selector
    right:Selector|None=None
    minimum:StrictInt|None=Field(default=None,ge=0,le=20000)
    maximum:StrictInt|None=Field(default=None,ge=0,le=20000)

    @model_validator(mode="after")
    def shape(self):
        pair=self.kind in {"cooccurs","precedes","response"}
        if pair!=(self.right is not None):
            raise ValueError("rule selector count mismatch")
        if pair and self.left==self.right:
            raise ValueError("pair requires distinct selectors")
        if self.kind=="count_range":
            if self.minimum is None or self.maximum is None or self.minimum>self.maximum:
                raise ValueError("valid inclusive count range required")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("count limits only valid for count range")
        return self


class Catalog(Plan):
    expected_review_sha256:str=Field(pattern=DIGEST)
    confirm_rule_catalog:StrictBool
    offset:StrictInt=Field(ge=0,le=20000,default=0)
    limit:StrictInt=Field(ge=1,le=MAX_CATALOG_PAGE,default=50)
    expected_catalog_sha256:str|None=Field(default=None,pattern=DIGEST)

    @model_validator(mode="after")
    def pinned_page(self):
        if self.offset and self.expected_catalog_sha256 is None:
            raise ValueError("catalog pin required for later page")
        return self


class Review(Plan):
    expected_review_sha256:str=Field(pattern=DIGEST)
    rules:list[Rule]=Field(min_length=1,max_length=MAX_RULES)
    confirm_conformance_review:StrictBool
    filter_status:Literal["all","satisfied","not_satisfied","indeterminate","not_applicable"]="all"
    offset:StrictInt=Field(ge=0,le=500,default=0)
    limit:StrictInt=Field(ge=1,le=50,default=20)
    expected_conformance_sha256:str|None=Field(default=None,pattern=DIGEST)

    @model_validator(mode="after")
    def reviewed_rules(self):
        jobs={p.job_id for p in self.snapshots}
        if len({r.rule_id for r in self.rules})!=len(self.rules):
            raise ValueError("duplicate rule identity")
        if any(s.job_id not in jobs for r in self.rules for s in (r.left,r.right) if s):
            raise ValueError("rule selector outside selected publications")
        if self.offset and self.expected_conformance_sha256 is None:
            raise ValueError("conformance pin required for later page")
        return self


def _load(reviewed,c,org,spec):
    loaded=reviewed._read(c,org,spec)
    _,base=reviewed._compute(org,spec,loaded)
    if base["review_sha256"]!=spec.expected_review_sha256:
        raise JobError("cross_source_review_changed")
    cases,_,providers,_,scope,expires=loaded
    groups=[]
    for group in sorted(spec.groups,key=group_key):
        members=group_key(group)
        rows=[(job,e) for job,case in members for e in cases[job,case]]
        identity=digest({"scope_sha256":scope,"members":members})
        groups.append((identity,rows))
    return groups,providers,scope,expires,base


def _catalog(groups,providers):
    totals=Counter();coverage=Counter()
    for _,rows in groups:
        keys=[(job,e["activity"]) for job,e in rows]
        totals.update(keys);coverage.update(set(keys))
    return [{"job_id":job,"provider":providers[job],"activity":activity,
             "event_count":totals[job,activity],"linked_groups":coverage[job,activity]}
            for job,activity in sorted(totals)]


def catalog(reviewed,org,spec,principal):
    if not spec.confirm_rule_catalog:
        raise JobError("explicit_conformance_catalog_review_required",403)
    with reviewed.jobs._tx(org) as c:
        groups,providers,scope,expires,base=_load(reviewed,c,org,spec)
        entries=_catalog(groups,providers)
        pin=digest({"format":FORMAT,"review_sha256":base["review_sha256"],"entries":entries})
        if spec.expected_catalog_sha256 and spec.expected_catalog_sha256!=pin:
            raise JobError("conformance_catalog_changed")
        if spec.offset>=len(entries) and spec.offset:
            raise JobError("conformance_catalog_page_outside_scope",422)
        page=entries[spec.offset:spec.offset+spec.limit]
        reviewed._fresh(expires)
        reviewed._audit(c,org,spec,principal,"conformance_catalog",
                        {"catalog_sha256":pin,"entries_returned":len(page)})
        reviewed._fresh(expires)
        return {"format":FORMAT,"scope_sha256":scope,"review_sha256":base["review_sha256"],
                "catalog_sha256":pin,"expires_at":expires,"entries":page,
                "total_entries":len(entries),"offset":spec.offset,
                "next_offset":spec.offset+len(page) if spec.offset+len(page)<len(entries) else None,
                "source_completeness_verified":False}


class Worklist(Review):
    expected_conformance_sha256:str=Field(pattern=DIGEST)
    confirm_worklist_export:StrictBool

    @model_validator(mode="after")
    def whole_scope(self):
        if self.offset!=0 or self.filter_status!="all":
            raise ValueError("worklist export requires complete unfiltered review")
        return self


def review(reviewed,org,spec,principal):
    return _review(reviewed,org,spec,principal,worklist=False)


def worklist(reviewed,org,spec,principal):
    if not spec.confirm_worklist_export:
        raise JobError("explicit_conformance_worklist_export_required",403)
    return _review(reviewed,org,spec,principal,worklist=True)


def _review(reviewed,org,spec,principal,*,worklist,retain=None,_conn=None,_all_groups=False,_approved_selectors=()):
    if not spec.confirm_conformance_review:
        raise JobError("explicit_conformance_rule_review_required",403)
    with (reviewed.jobs._tx(org) if _conn is None else nullcontext(_conn)) as c:
        groups,providers,scope,expires,base=_load(reviewed,c,org,spec)
        # Exact selectors must be observed in at least one linked group.
        # A misspelled activity cannot silently pass a forbidden rule.
        known={(e["job_id"],e["activity"]) for e in _catalog(groups,providers)} | set(_approved_selectors)
        ordered=sorted((r.model_dump() for r in spec.rules),key=lambda r:r["rule_id"])
        for rule in ordered:
            for sel in (rule["left"],rule["right"]):
                if sel and (sel["job_id"],sel["activity"]) not in known:
                    raise JobError("conformance_selector_not_observed_in_linked_scope",422)
        # A bounded 500 x 40 result set, independent of event pair counts.
        rows=[];by_rule=defaultdict(list)
        for identity,events in groups:
            index=index_events(events)
            checks=[]
            for rule in ordered:
                result={"rule_id":rule["rule_id"],**evaluate(index,rule,providers)}
                checks.append(result);by_rule[rule["rule_id"]].append(result)
            rows.append({"linked_case_id":identity,"event_count":len(events),
                         "status":group_status(checks),"checks":checks})
        rows.sort(key=lambda r:r["linked_case_id"])
        summaries=[{"rule_id":r["rule_id"],**counts(by_rule[r["rule_id"]])} for r in ordered]
        canonical={"format":FORMAT,"review_sha256":base["review_sha256"],"rules":ordered,
                   "groups":rows,"rule_summary":summaries}
        pin=digest(canonical)
        if spec.expected_conformance_sha256 and spec.expected_conformance_sha256!=pin:
            raise JobError("conformance_review_changed")
        filtered=[r for r in rows if spec.filter_status=="all" or r["status"]==spec.filter_status]
        if spec.offset>=len(filtered) and spec.offset:
            raise JobError("conformance_page_outside_scope",422)
        page=rows if _all_groups else filtered[spec.offset:spec.offset+spec.limit]
        summary={"linked_groups":len(rows),"rules":len(ordered),
                 "evaluations":len(rows)*len(ordered),"group_outcomes":counts(rows),
                 "rule_outcomes":counts([check for r in rows for check in r["checks"]])}
        reviewed._fresh(expires)
        reviewed._audit(c,org,spec,principal,"conformance_triage_review" if retain else "conformance_worklist_export" if worklist else "conformance_review",
                        {"conformance_sha256":pin,"groups":len(rows),"rules":len(ordered),
                         "groups_returned":len(page)})
        reviewed._fresh(expires)
        if worklist:
            # One row per non-satisfied or indeterminate rule. No source
            # labels, raw records or pseudonymous source-case membership.
            exceptions=[{"linked_case_id":r["linked_case_id"],**check}
                        for r in rows for check in r["checks"]
                        if check["status"] in ("not_satisfied","indeterminate")]
            report={"format":"openworkgraph.linked-conformance-worklist.v1",
                    "review_sha256":base["review_sha256"],"conformance_sha256":pin,
                    "scope_sha256":scope,"expires_at":expires,"summary":summary,
                    "exceptions":exceptions,"exception_count":len(exceptions),
                    "actual_process_compliance_verified":False}
            if retain is not None:
                return retain(c,report)
            return report
        return {"format":FORMAT,"scope_sha256":scope,"review_sha256":base["review_sha256"],
                "conformance_sha256":pin,"expires_at":expires,"rules":ordered,
                "summary":summary,"rule_summary":summaries,"groups":page,
                "filter_status":spec.filter_status,"filtered_groups":len(filtered),
                "offset":spec.offset,"next_offset":spec.offset+len(page) if not _all_groups and spec.offset+len(page)<len(filtered) else None,
                "source_completeness_verified":False,"business_identity_verified":False,
                "actual_process_compliance_verified":False,"unlinked_cases_included":False,
                "interpretation":"These are rule outcomes for selected reported evidence only. Missing observations do not prove missing actions. Date-only evidence and timestamp ties cannot establish strict occurrence order. No actual compliance, misconduct, causal relationship or business identity is verified."}


def worklist_csv(report):
    """Spreadsheet-safe counts-only worklist: identifiers and fixed codes only."""
    from io import StringIO
    import csv
    out=StringIO(newline="")
    writer=csv.DictWriter(out,fieldnames=["linked_case_id","rule_id","status","reason",
        "left_count","right_count","conformance_sha256"])
    writer.writeheader()
    for row in report["exceptions"]:
        writer.writerow({**row,"conformance_sha256":report["conformance_sha256"]})
    return out.getvalue()
