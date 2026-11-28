"""R11 exact source-statement revision comparison against one saved design.

No document retention, policy interpretation, ledger writes or execution.
Exact statement text is compared as a multiset across explicitly supplied
bundles. Duplicate occurrences remain distinct. Changed text is never
automatically paired into a semantic replacement.
"""
from collections import Counter, defaultdict, deque
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import process_clarification as c
from . import process_source_provenance as provenance
from .process_model import ProcessModel

FORMAT = "openworkgraph.source-revision-review.v1"
KINDS = ("removed", "added", "retained")
MAX_PAGE = 50


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline: list[c.Document] = Field(min_length=1,max_length=c.MAX_DOCUMENTS)
    candidate: list[c.Document] = Field(min_length=1,max_length=c.MAX_DOCUMENTS)
    expected_review_sha256: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")
    change_kind: Literal["removed","added","retained","all"] = "all"
    subject_type: Literal["rule","activity","outcome"] | None = None
    target_id: str | None = Field(default=None,min_length=1,max_length=200)
    offset: StrictInt = Field(default=0,ge=0,le=2*c.MAX_STATEMENTS)
    limit: StrictInt = Field(default=25,ge=1,le=MAX_PAGE)


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,
                             allow_nan=False).encode()).hexdigest()


def _inventory_delta(left,right):
    # Stable occurrence pairing of identical text only, not a claim of
    # same source identity, policy scope, or governing authority.
    remaining=defaultdict(deque)
    for row in right:
        remaining[row["text"]].append(row)
    used=set()
    changes=[]
    for a in left:
        candidates=remaining[a["text"]]
        if candidates:
            b=candidates.popleft()
            used.add(b["statement_id"])
            changes.append(("retained",a,b))
        else:
            changes.append(("removed",a,None))
    changes.extend(("added",None,b) for b in right if b["statement_id"] not in used)
    return changes


def _complete(stats):
    return not any(d["overlong_statements"] or d["omitted_by_budget"] for d in stats)


def _subjects(model):
    for rule in model.business_rules:
        yield "rule",rule.id,rule.text
    for a in model.activities:
        if a.kind in {"activity","decision"}:
            yield "activity",a.id,a.label
    labels={a.id:a.label for a in model.activities}
    for t in model.terminal_activity_ids:
        yield "outcome",t,labels.get(t,"")


def compare(record,spec):
    if not isinstance(spec,Review):
        spec=Review.model_validate(spec)
    if record["content_sha256"]!=spec.expected_content_sha256:
        raise ValueError("saved process digest changed; reopen the selected revision")
    if spec.offset and not spec.expected_review_sha256:
        raise ValueError("review page one before requesting more source evidence")
    if (spec.subject_type is None)!=(spec.target_id is None):
        raise ValueError("select both subject type and target ID")
    model=ProcessModel.model_validate(record["process_model"])
    before,b_stats=c._statement_inventory(spec.baseline)
    after,a_stats=c._statement_inventory(spec.candidate)
    complete=_complete(b_stats) and _complete(a_stats)
    basis={"workflow_id":record["workflow_id"],"revision":record["revision"],
           "content_sha256":record["content_sha256"],
           "baseline_documents_sha256":c.documents_sha256(spec.baseline),
           "candidate_documents_sha256":c.documents_sha256(spec.candidate),
           "clarification_analysis_version":c.ANALYSIS_VERSION,
           "source_comparison_version":FORMAT}
    pin=_digest(basis)
    if spec.expected_review_sha256 and spec.expected_review_sha256!=pin:
        raise ValueError("source review changed; review both bundles again")
    changes=_inventory_delta(before,after)
    counts=dict.fromkeys(KINDS,0)
    counts.update(Counter(kind for kind,_,_ in changes))
    title_summary,title_groups=provenance.summarize(before,after)
    title_summary['both_bounded_document_scans_complete']=complete
    if title_summary['retained_occurrences']!=counts['retained']:
        raise ValueError('indexed source occurrence accounting mismatch')
    subjects=[]
    matches={}
    for kind,target,text in _subjects(model):
        b=c._candidates(text,before)
        a=c._candidates(text,after)
        b_ids={s["statement_id"] for s in b}
        a_ids={s["statement_id"] for s in a}
        matches[(kind,target)]=(b_ids,a_ids)
        relevant=Counter(change for change,l,r in changes
            if (l and l["statement_id"] in b_ids) or (r and r["statement_id"] in a_ids))
        # Conflict review is independently bounded by the existing
        # clarification engine. Never treat incomplete source scans as clean.
        bc=c._contradictions(b[:c.MAX_CONFLICT_CANDIDATES],text)
        ac=c._contradictions(a[:c.MAX_CONFLICT_CANDIDATES],text)
        conflict_complete=complete and len(b)<=c.MAX_CONFLICT_CANDIDATES and len(a)<=c.MAX_CONFLICT_CANDIDATES
        flags=[]
        if not conflict_complete:flags.append("incomplete_source_or_conflict_scan")
        if relevant["removed"]:flags.append("matching_statement_removed")
        if relevant["added"]:flags.append("matching_statement_added")
        if bc:flags.append("baseline_possible_conflicts")
        if ac:flags.append("candidate_possible_conflicts")
        if b and not a:flags.append("no_candidate_lexical_match")
        if not b and not a:flags.append("no_lexical_match_in_either_bundle")
        subjects.append({"subject_type":kind,"target_id":target,
            "baseline_matching_statements":len(b),"candidate_matching_statements":len(a),
            "added_matching_occurrences":relevant["added"],
            "removed_matching_occurrences":relevant["removed"],
            "retained_matching_occurrences":relevant["retained"],
            "baseline_possible_conflict_count":len(bc),"candidate_possible_conflict_count":len(ac),
            "conflict_scan_complete":conflict_complete,"review_flags":flags,
            "renewed_review_suggested":bool(flags)})
    selected=None
    if spec.subject_type:
        selected=matches.get((spec.subject_type,spec.target_id))
        if selected is None:
            raise ValueError("selected subject is absent from this saved revision")
    visible=[]
    for kind,l,r in changes:
        if spec.change_kind!="all" and kind!=spec.change_kind:
            continue
        if selected is not None and not ((l and l["statement_id"] in selected[0])
                                         or (r and r["statement_id"] in selected[1])):
            continue
        visible.append((kind,l,r))
    def citation(row):
        return c._cite(row) if row else None
    page=visible[spec.offset:spec.offset+spec.limit]
    evidence=[{"kind":k,"baseline":citation(l),"candidate":citation(r),
               "exact_text_title_distribution":title_groups[(l or r)["text"]]} for k,l,r in page]
    def coverage(rows,stats):
        return {"indexed_statements":len(rows),"document_scan_complete":_complete(stats),
                "instruction_like_statements":sum(r["instruction_like"] for r in rows),
                "documents":stats}
    return {"format":FORMAT,**basis,"review_sha256":pin,
            "document_bundles_identical":basis["baseline_documents_sha256"]==basis["candidate_documents_sha256"],
            "baseline":coverage(before,b_stats),"candidate":coverage(after,a_stats),
            "both_bounded_document_scans_complete":complete,
            "indexed_statement_occurrence_counts":counts,
            "document_title_provenance":title_summary,
            "subjects":subjects,"subjects_with_review_flags":sum(s["renewed_review_suggested"] for s in subjects),
            "page":{"change_kind":spec.change_kind,"subject_type":spec.subject_type,"target_id":spec.target_id,
                    "matching_occurrences":len(visible),"offset":spec.offset,
                    "next_offset":spec.offset+len(page) if page and spec.offset+len(page)<len(visible) else None,
                    "rows":evidence},
            "exact_text_multiset_not_semantic_revision":True,
            "source_authority_verified":False,"policy_consistency_verified":False,
            "ledger_changed":False,"model_changed":False,"execution_authorized":False,
            "interpretation":[
                "Counts compare indexed, trimmed full statement text as occurrences. Formatting within a statement, punctuation or wording differences can produce removal and addition. Identical occurrences are paired in stable bundle order, not by verified document identity.",
                "Source reorder, title changes and changes outside indexed statements may change the bundle digest without changing indexed statement counts.",
                "Title-label provenance gives the minimum cross-title exact-text matches among maximum-cardinality matches; it is not the specific stable-order occurrence pairing shown on the page. Duplicate document titles are grouped. A title change or copied text does not establish a document move, identity or authority.",
                "Subject matches and possible conflicts are lexical review prompts; absence of a flag does not prove that the policy is unchanged or consistent.",
                "Instruction-like statements remain visible in the raw evidence delta but are excluded from subject matching. Omitted statements make the comparison incomplete.",
                "No existing human decision is transferred, updated or invalidated by this read-only report. Run the existing clarification review on the candidate bundle to record new decisions."]}
