"""Read-only whole-window effective policy review, reusing the date engine."""
from collections import Counter
from typing import Annotated
from pydantic import Field

from . import process_effective_policy as ep

FORMAT = "openworkgraph.effective-policy-window-review.v1"
MAX_BUNDLES = 12
MAX_SCAN_CHARS = 1200000
MAX_MEMBERSHIPS = 5000
RESOLVED = "resolved_under_declared_assumptions"


class Review(ep.Timeline):
    expected_timeline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def summarize(segments, bundles):
    """Question evidence persistence over resolved days only; never bridge gaps."""
    questions = {}
    intervals = []
    transitions = []
    previous = None
    for s in segments:
        pin = s["documents_sha256"]
        row = {k:s[k] for k in ("start_date","end_date_exclusive","days","status","selected_version_ids","documents_sha256")}
        if s["status"] != RESOLVED:
            row.update(question_count=None, coverage_complete=None)
            current = None
        else:
            current = bundles[pin]
            row.update(question_count=len(current["questions"]), coverage_complete=current["coverage_complete"])
            for qid, q in current["questions"].items():
                item = questions.setdefault(qid, {"question_id":qid,"kind":q["kind"],"target_id":q["target_id"],
                    "generated_days":0,"coverage_incomplete_days":0,"evidence_digests":set(),"intervals":[]})
                item["generated_days"] += s["days"]
                item["coverage_incomplete_days"] += s["days"] if not current["coverage_complete"] or q["coverage_blocked"] else 0
                item["evidence_digests"].add(q["evidence_sha256"])
                item["intervals"].append({"start_date":s["start_date"],"end_date_exclusive":s["end_date_exclusive"],
                    "days":s["days"],"documents_sha256":pin,"evidence_sha256":q["evidence_sha256"]})
        if intervals:
            if current is None or previous is None:
                transitions.append({"date":s["start_date"],"status":"not_compared_unresolved_boundary",
                    "new":None,"no_longer_generated":None,"changed_evidence":None,"same_evidence":None})
            else:
                old,new = previous["questions"],current["questions"]
                common = old.keys() & new.keys()
                transitions.append({"date":s["start_date"],"status":"compared",
                    "new":sorted(new.keys()-old.keys()),"no_longer_generated":sorted(old.keys()-new.keys()),
                    "changed_evidence":sorted(k for k in common if old[k]["evidence_sha256"]!=new[k]["evidence_sha256"]),
                    "same_evidence":sorted(k for k in common if old[k]["evidence_sha256"]==new[k]["evidence_sha256"])})
        intervals.append(row)
        previous = current
    resolved_days = sum(s["days"] for s in segments if s["status"]==RESOLVED)
    for q in questions.values():
        q["distinct_evidence_count"] = len(q.pop("evidence_digests"))
        q["not_generated_in_resolved_days"] = resolved_days-q["generated_days"]
        q["generated_on_every_resolved_day"] = resolved_days>0 and q["generated_days"]==resolved_days
    return {"intervals":intervals,"transitions":transitions,"questions":sorted(questions.values(),
        key=lambda q:(-q["generated_days"],q["kind"],q["question_id"])),
        "resolved_days":resolved_days,"unresolved_days":sum(s["days"] for s in segments)-resolved_days,
        "resolved_days_with_incomplete_scan":sum(s["days"] for s in intervals if s["coverage_complete"] is False)}


def review(record, spec):
    if not isinstance(spec, Review):
        spec = Review.model_validate(spec)
    timeline = ep.timeline(record,spec)
    if timeline["timeline_sha256"] != spec.expected_timeline_sha256:
        raise ValueError("policy timeline changed; prepare the current source versions")
    selected = {}
    for s in timeline["segments"]:
        if s["status"] == RESOLVED:
            selected.setdefault(s["documents_sha256"],s["selected_version_ids"])
    if len(selected)>MAX_BUNDLES:
        raise ValueError("whole-window review exceeds 12 distinct source bundles; shorten the window")
    docs = {pin:ep._documents(spec, ids) for pin,ids in selected.items()}
    if sum(len(d.text) for bundle in docs.values() for d in bundle)>MAX_SCAN_CHARS:
        raise ValueError("whole-window scan exceeds the text budget; shorten the window")
    bundles = {}
    for pin, sources in docs.items():
        current = ep._clarify(record,sources)
        if current["documents_sha256"] != pin:
            raise ValueError("source bundle digest mismatch")
        coverage = current["source_coverage"]
        questions = {}
        for q in current["questions"]:
            qid = q["question_id"]
            if qid in questions:
                raise ValueError("duplicate clarification question identifier")
            questions[qid] = {"kind":q["kind"],"target_id":q["target_id"],
                "coverage_blocked":q["coverage_blocked"],"evidence_sha256":ep._question_basis(q)}
        bundles[pin] = {"questions":questions,"statement_count":current["statement_count"],
            "coverage_complete":coverage["document_scan_complete"] and coverage["all_subject_conflict_reviews_complete"],
            "visible_instruction_like_count":len(current["instruction_like_statements"])}
    if sum(len(bundles[s["documents_sha256"]]["questions"]) for s in timeline["segments"] if s["status"]==RESOLVED)>MAX_MEMBERSHIPS:
        raise ValueError("whole-window question membership budget exceeded; shorten the window")
    data = summarize(timeline["segments"],bundles)
    return {"format":FORMAT,"workflow_id":record["workflow_id"],"revision":record["revision"],
        "content_sha256":record["content_sha256"],"timeline_sha256":timeline["timeline_sha256"],
        "review_sha256":ep._digest([FORMAT,timeline["timeline_sha256"],ep.clarification.ANALYSIS_VERSION]),
        "analysis_version":ep.clarification.ANALYSIS_VERSION,"window_days":timeline["window_days"],
        "distinct_bundle_count":len(bundles),"bundle_summaries":[{"documents_sha256":pin,
            "question_count":len(b["questions"]),"question_kind_counts":dict(Counter(q["kind"] for q in b["questions"].values())),
            **{k:b[k] for k in ("statement_count","coverage_complete","visible_instruction_like_count")}} for pin,b in bundles.items()],
        **data,"prior_decisions_imported":False,"source_authority_verified":False,
        "policy_consistency_verified":False,"question_disappearance_is_not_resolution":True,
        "ledger_changed":False,"model_changed":False,"execution_authorized":False,
        "interpretation":"Generated lexical clarification questions over resolved declared source bundles, not historical decisions or proof of policy consistency. Days without generated questions do not prove compliance; unresolved intervals are never compared across gaps. Incomplete scans remain visible. Exact excerpts stay in the existing date review, not this metadata export."}
