"""Effective-dated, explicitly declared policy source coverage and design review.

Dates, families and priorities are supplied by the human reviewer. No source
authority, approval, signatures, retention, or execution are inferred.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import process_clarification as clarification
from . import process_source_revision as source_revision

FORMAT = "openworkgraph.effective-policy-timeline.v1"
MAX_VERSIONS = 20
MAX_FAMILIES = 5
MAX_DAYS = 366 * 5
MAX_TEXT_CHARS = 300_000
KEY = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")]
DATE = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Family(Strict):
    family_id: KEY
    label: str = Field(min_length=1, max_length=100)
    resolution: Literal["unique_active", "explicit_priority"] = "unique_active"


class Version(Strict):
    version_id: KEY
    family_id: KEY
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=clarification.MAX_DOCUMENT_CHARS)
    effective_from: DATE
    effective_until: DATE | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


class Timeline(Strict):
    format: Literal["openworkgraph.effective-policy-timeline.v1"] = FORMAT
    expected_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start_date: DATE
    end_date_exclusive: DATE
    families: list[Family] = Field(min_length=1, max_length=MAX_FAMILIES)
    versions: list[Version] = Field(min_length=1, max_length=MAX_VERSIONS)


class Review(Timeline):
    expected_timeline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    as_of: DATE
    compare_to: DATE | None = None
    question_offset: int = Field(default=0, ge=0, le=10000)
    question_limit: int = Field(default=25, ge=1, le=50)
    expected_review_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _day(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("use valid Gregorian calendar dates") from None


def _text(value):
    if not value.strip() or not value.isprintable():
        raise ValueError("family labels and document titles must be printable and nonempty")


def _validate(record, spec):
    if record["content_sha256"] != spec.expected_content_sha256:
        raise ValueError("saved process digest changed; reopen the selected revision")
    start, end = _day(spec.start_date), _day(spec.end_date_exclusive)
    if not 1 <= (end-start).days <= MAX_DAYS:
        raise ValueError("review a positive date window of at most 1830 days")
    families = {f.family_id:f for f in spec.families}
    if len(families) != len(spec.families):
        raise ValueError("policy family identifiers must be unique")
    for f in spec.families:
        _text(f.label)
    if len({v.version_id for v in spec.versions}) != len(spec.versions):
        raise ValueError("policy version identifiers must be unique across the whole timeline")
    if sum(len(v.text) for v in spec.versions) > MAX_TEXT_CHARS:
        raise ValueError("combined source text exceeds 300000 characters")
    dates, metadata = {}, []
    for v in spec.versions:
        if v.family_id not in families:
            raise ValueError("policy version refers to an undeclared family")
        _text(v.title)
        if not v.text.strip():
            raise ValueError("policy text must be nonempty")
        # Reject unpaired Unicode surrogate codepoints in both metadata and text.
        try:
            v.text.encode("utf-8")
            v.title.encode("utf-8")
        except UnicodeError:
            raise ValueError("policy source must be valid Unicode text") from None
        a, b = _day(v.effective_from), _day(v.effective_until) if v.effective_until else None
        if b is not None and b <= a:
            raise ValueError("each effective interval must have positive duration")
        policy = families[v.family_id].resolution
        if (policy == "explicit_priority") != (v.priority is not None):
            raise ValueError("explicit priority families require a priority on every version; unique-active families forbid it")
        dates[v.version_id] = (a,b)
        doc = clarification.Document(title=v.title,text=v.text)
        metadata.append({"version_id":v.version_id,"family_id":v.family_id,"title":v.title,
                         "effective_from":v.effective_from,"effective_until":v.effective_until,
                         "priority":v.priority,"document_sha256":clarification.documents_sha256([doc]),
                         "characters":len(v.text)})
    # A family with no supplied versions is an intentional gap, not omitted.
    # Preserve declared family order because the ordinary clarification bundle
    # is ordered. Version array order is irrelevant: identities are explicit.
    basis={"format":FORMAT,"workflow_id":record["workflow_id"],"revision":record["revision"],
           "content_sha256":record["content_sha256"],"start_date":spec.start_date,
           "end_date_exclusive":spec.end_date_exclusive,
           "families":[f.model_dump() for f in spec.families],
           "versions":sorted(metadata,key=lambda v:v["version_id"])}
    return start,end,families,dates,basis


def _resolve(families, versions, dates, point):
    rows=[]
    for f in families.values():
        active=sorted((v for v in versions if v.family_id==f.family_id and
                       dates[v.version_id][0]<=point and
                       (dates[v.version_id][1] is None or point<dates[v.version_id][1])),
                      key=lambda v:v.version_id)
        picked=None
        if not active:
            status="gap"
        elif len(active)==1:
            status="unique_active"
            picked=active[0]
        elif f.resolution=="unique_active":
            status="ambiguous_overlap"
        else:
            top=max(v.priority for v in active)
            winners=[v for v in active if v.priority==top]
            if len(winners)==1:
                status="overlap_resolved_by_declared_priority"
                picked=winners[0]
            else:
                status="ambiguous_priority_tie"
        rows.append({"family_id":f.family_id,"status":status,
                     "active_version_ids":[v.version_id for v in active],
                     "selected_version_id":picked.version_id if picked else None,
                     "shadowed_version_ids":[v.version_id for v in active if picked and v.version_id!=picked.version_id]})
    return rows


def _state(rows):
    if any(r["status"] in {"ambiguous_overlap","ambiguous_priority_tie"} for r in rows):
        return "ambiguous"
    if any(r["status"]=="gap" for r in rows):
        return "incomplete"
    return "resolved_under_declared_assumptions"


def _selection(rows):
    return [r["selected_version_id"] for r in rows if r["selected_version_id"] is not None]


def _documents(spec, ids):
    by_id={v.version_id:v for v in spec.versions}
    return [clarification.Document(title=by_id[k].title,text=by_id[k].text) for k in ids]


def timeline(record,spec):
    if not isinstance(spec,Timeline):
        spec=Timeline.model_validate(spec)
    start,end,families,dates,basis=_validate(record,spec)
    points={start,end}
    for a,b in dates.values():
        if start<a<end:points.add(a)
        if b is not None and start<b<end:points.add(b)
    points=sorted(points)
    segments=[]
    family_days={k:Counter() for k in families}
    aggregate=Counter()
    for a,b in zip(points,points[1:]):
        rows=_resolve(families,spec.versions,dates,a)
        status=_state(rows)
        days=(b-a).days
        aggregate[status]+=days
        for row in rows:
            family_days[row["family_id"]][row["status"]]+=days
        selected=_selection(rows)
        bundle_pin=clarification.documents_sha256(_documents(spec,selected)) if status=="resolved_under_declared_assumptions" else None
        # Coalesce only equal complete diagnostics, not just equal winners:
        # otherwise an overlap beginning in the middle would disappear.
        if segments and segments[-1]["families"]==rows:
            segments[-1]["end_date_exclusive"]=b.isoformat()
            segments[-1]["days"]+=days
        else:
            segments.append({"start_date":a.isoformat(),"end_date_exclusive":b.isoformat(),
                             "days":days,"status":status,"families":rows,
                             "selected_version_ids":selected,"documents_sha256":bundle_pin})
    transitions=[]
    for before,after in zip(segments,segments[1:]):
        old={r["family_id"]:r for r in before["families"]}
        changed=[]
        for row in after["families"]:
            prev=old[row["family_id"]]
            if row!=prev:
                changed.append({"family_id":row["family_id"],"before_status":prev["status"],
                                "after_status":row["status"],"before_selected":prev["selected_version_id"],
                                "after_selected":row["selected_version_id"],
                                "entered_active":sorted(set(row["active_version_ids"])-set(prev["active_version_ids"])),
                                "left_active":sorted(set(prev["active_version_ids"])-set(row["active_version_ids"]))})
        transitions.append({"date":after["start_date"],"before_status":before["status"],
                            "after_status":after["status"],"changed_families":changed,
                            "selected_bundle_changed":before["selected_version_ids"]!=after["selected_version_ids"]})
    used={v for s in segments for r in s["families"] for v in r["active_version_ids"]}
    selected={v for s in segments if s["status"]=="resolved_under_declared_assumptions" for v in s["selected_version_ids"]}
    return {**basis,"timeline_sha256":_digest(basis),"window_days":(end-start).days,
            "coverage_days":{k:aggregate[k] for k in ("resolved_under_declared_assumptions","incomplete","ambiguous")},
            "family_coverage":[{"family_id":k,"coverage_days":{status:family_days[k][status] for status in
                ("gap","unique_active","ambiguous_overlap","ambiguous_priority_tie","overlap_resolved_by_declared_priority")}}
                for k in families],
            "segments":segments,"transitions":transitions,
            "versions_never_active_in_window":sorted(v.version_id for v in spec.versions if v.version_id not in used),
            "versions_never_selected_in_complete_bundle":sorted(v.version_id for v in spec.versions if v.version_id not in selected),
            "all_families_required":True,"end_dates_exclusive":True,
            "source_authority_verified":False,"policy_consistency_verified":False,
            "ledger_changed":False,"model_changed":False,"execution_authorized":False,
            "interpretation":"Effective dates, required families and priority ordering are reviewer declarations. "
                             "An overlap resolved by priority is still visible. No legal authority, source completeness or policy correctness is verified."}


def _at(report,point):
    if not _day(report["start_date"])<=point<_day(report["end_date_exclusive"]):
        raise ValueError("selected review date must be inside the declared timeline window")
    return next(s for s in report["segments"] if _day(s["start_date"])<=point<_day(s["end_date_exclusive"]))


def _question_key(question):
    return question["question_id"]


def _question_basis(question):
    # The existing question ID identifies a model subject, not a document
    # revision. Exact prompts, witnesses and coverage bind changed evidence.
    # Do not silently call a changed title/offset/citation the same review.
    return _digest({k:question[k] for k in ("kind","target_id","prompt","citations",
                                           "contradictions","source_coverage","allowed_decisions")})


def _clarify(record,docs):
    return clarification.clarify(record,clarification.ClarificationRequest(documents=docs))


def review(record,spec):
    if not isinstance(spec,Review):
        spec=Review.model_validate(spec)
    report=timeline(record,spec)
    if report["timeline_sha256"]!=spec.expected_timeline_sha256:
        raise ValueError("policy timeline changed; prepare and review the current source versions")
    chosen=_at(report,_day(spec.as_of))
    baseline=_at(report,_day(spec.compare_to)) if spec.compare_to else None
    result={"format":"openworkgraph.effective-policy-date-review.v1",
            "timeline_sha256":report["timeline_sha256"],"workflow_id":record["workflow_id"],
            "revision":record["revision"],"content_sha256":record["content_sha256"],
            "as_of":spec.as_of,"compare_to":spec.compare_to,"selected_segment":chosen,
            "comparison_segment":baseline,"status":chosen["status"],"clarification":None,"comparison":None,
            "source_authority_verified":False,"policy_consistency_verified":False,
            "prior_decisions_imported":False,"ledger_changed":False,"model_changed":False,
            "execution_authorized":False}
    basis=[result["format"],report["timeline_sha256"],spec.as_of,spec.compare_to,
           clarification.ANALYSIS_VERSION,source_revision.FORMAT]
    pin=_digest(basis)
    if spec.question_offset and not spec.expected_review_sha256:
        raise ValueError("review the first question page before requesting later pages")
    if spec.expected_review_sha256 and spec.expected_review_sha256!=pin:
        raise ValueError("date review changed; reopen the first page")
    result["review_sha256"]=pin
    if chosen["status"]!="resolved_under_declared_assumptions":
        return result
    docs=_documents(spec,chosen["selected_version_ids"])
    current=_clarify(record,docs)
    questions=current["questions"]
    page=questions[spec.question_offset:spec.question_offset+spec.question_limit]
    result["clarification"]={k:current[k] for k in ("analysis_version","documents_sha256",
        "statement_count","source_coverage","instruction_like_statements")}
    result["clarification"].update(question_count=len(questions),
        question_kind_counts=dict(sorted(Counter(q["kind"] for q in questions).items())),
        question_page={"offset":spec.question_offset,"limit":spec.question_limit,"rows":page,
                       "next_offset":spec.question_offset+len(page) if page and spec.question_offset+len(page)<len(questions) else None})
    if baseline:
        if baseline["status"]!="resolved_under_declared_assumptions":
            result["comparison"]={"status":"comparison_date_unresolved"}
        else:
            previous_docs=_documents(spec,baseline["selected_version_ids"])
            previous=_clarify(record,previous_docs)
            old={_question_key(q):_question_basis(q) for q in previous["questions"]}
            new={_question_key(q):_question_basis(q) for q in questions}
            delta=source_revision.compare(record,source_revision.Review(
                expected_content_sha256=record["content_sha256"],baseline=previous_docs,candidate=docs,limit=1))
            result["comparison"]={"status":"compared","baseline_documents_sha256":previous["documents_sha256"],
                "candidate_documents_sha256":current["documents_sha256"],
                "question_ids_new":sorted(new.keys()-old.keys()),
                "question_ids_no_longer_generated":sorted(old.keys()-new.keys()),
                "question_ids_changed_evidence":sorted(k for k in old.keys()&new.keys() if old[k]!=new[k]),
                "question_ids_same_generated_evidence":sorted(k for k in old.keys()&new.keys() if old[k]==new[k]),
                "baseline_question_count":len(old),"candidate_question_count":len(new),
                "indexed_statement_occurrence_counts":delta["indexed_statement_occurrence_counts"],
                "both_bounded_document_scans_complete":delta["both_bounded_document_scans_complete"],
                "subjects_with_review_flags":delta["subjects_with_review_flags"],
                "subjects":delta["subjects"],
                "question_disappearance_is_not_resolution":True}
    return result
