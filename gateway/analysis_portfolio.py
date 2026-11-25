"""Bounded, explicit, tenant-authorized shared aggregate portfolio review.

Each selected revision is independently authorized and checked against the
caller's reviewed digest. All reads and the counts-only audit share one
transaction. No case attachment, group-code join, pooling or ranking.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .analysis_publications import PublicationError
from .analysis_revision_comparisons import compare_snapshots

FORMAT = "openworkgraph.shared-analysis-portfolio.v1"
MAX_SELECTIONS = 8
ID = r"^ana_[a-f0-9]{32}$"


class Selection(BaseModel):
    model_config=ConfigDict(extra="forbid")
    view_id: Annotated[str,Field(pattern=ID)]
    revision: Annotated[StrictInt,Field(ge=1,le=10)]
    expected_content_sha256: Annotated[str,Field(pattern=r"^[a-f0-9]{64}$")]


class PortfolioRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    selections: Annotated[list[Selection],Field(min_length=2,max_length=MAX_SELECTIONS)]
    baseline_index: Annotated[StrictInt,Field(ge=0,lt=MAX_SELECTIONS)] = 0

    @model_validator(mode="after")
    def distinct(self):
        if self.baseline_index>=len(self.selections):
            raise ValueError("Choose a selected baseline")
        if len({s.view_id for s in self.selections})!=len(self.selections):
            raise ValueError("Choose distinct publications; use revision comparison within one publication")
        return self


def _digest(report):
    return sha256(json.dumps(report,sort_keys=True,ensure_ascii=True,
                             separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _basis(detail):
    snapshot=detail["snapshot"]
    return {"view_id":detail["view_id"],"revision":detail["viewing_revision"],
            "title":detail["title"],"content_sha256":detail["content_sha256"],
            "published_at":detail["published_at"],"expires_at":detail["expires_at"],
            "source_content_sha256":snapshot["source_content_sha256"],
            "analysis_spec_sha256":snapshot["analysis_spec_sha256"],
            "matching_cases":snapshot["matching_cases"],"matching_rows":snapshot["matching_rows"]}


def review(store,principal,request: PortfolioRequest):
    """Acquire all publication locks in canonical order, then check all pins.

    A missing, unauthorized or expired member invalidates the entire request;
    no partial portfolio or audit of other selected content is returned.
    """
    with store.transaction(principal) as conn:
        metas={s.view_id:store.meta(conn,principal,s.view_id)
               for s in sorted(request.selections,key=lambda s:s.view_id)}
        details=[store.detail_in(conn,principal,metas[s.view_id],s.revision)
                 for s in request.selections]
        # Check all access before disclosing any digest mismatch.
        if any(d["content_sha256"]!=s.expected_content_sha256
               for d,s in zip(details,request.selections,strict=True)):
            raise PublicationError("A selected revision differs from its reviewed digest; reopen the selections",409)
        base=details[request.baseline_index]
        # Detect contradictions between alternatives as well as the baseline.
        # Only compare complete, ungrouped reports of the same claimed basis.
        contradictions=set()
        conflicts=[]
        for i,left in enumerate(details):
            l=left["snapshot"]
            for j in range(i+1,len(details)):
                r=details[j]["snapshot"]
                fields=("source_content_sha256","analysis_spec_sha256","metric_definition_sha256",
                        "fingerprint_algorithm","level","columns")
                if not all(l[k]==r[k] for k in fields):
                    continue
                pair=compare_snapshots(left,details[j])
                if pair["refusal_reasons"]!=["same_fingerprint_but_different_values"]:
                    continue
                contradictions.update((i,j))
                conflicts.append({"selection_indices":[i,j],
                                  "reason":"same_claimed_basis_conflicting_values"})
        comparisons=[]
        for i,detail in enumerate(details):
            if i==request.baseline_index:
                continue
            pair=compare_snapshots(base,detail)
            if i in contradictions or request.baseline_index in contradictions:
                pair["comparable"]=False
                pair["changes"]=[]
                pair["refusal_reasons"]=[*pair["refusal_reasons"],"portfolio_conflicting_claimed_basis"]
            comparisons.append({"selection_index":i,"comparable":pair["comparable"],
                "refusal_reasons":pair["refusal_reasons"],"changes":pair["changes"],
                "same_claimed_source":pair["same_claimed_source"],
                "same_claimed_analysis_spec":pair["same_claimed_analysis_spec"]})
        report={"format":FORMAT,"baseline_index":request.baseline_index,
                "selections":[_basis(d) for d in details],"comparisons":comparisons,
                "conflicting_claimed_basis_pairs":conflicts,
                "comparable_count":sum(p["comparable"] for p in comparisons),
                "incomparable_count":sum(not p["comparable"] for p in comparisons),
                "source_independently_verified":False,"case_identities_joined":False,
                "pooled_totals_reported":False,"ranking_reported":False,
                "causal_effect_estimated":False,"execution_authorized":False,
                "interpretation":[
                    "Each comparison uses the same explicitly chosen baseline. Exact selected revisions are immutable; current authorization and expiry are checked at review time.",
                    "Only complete ungrouped numeric snapshots with matching specifications, measure definitions, units and metric coverage are compared. Group codes never establish cross-publication identity.",
                    "Matching source and specification digests are publisher claims, not verification. Conflicting complete numeric reports with the same claimed source and analysis basis are flagged across all selections, even between two alternatives. Comparisons involving conflicting members are withheld.",
                    "Populations may overlap or differ. No aggregation, ranking, savings, intervention effect, case join or execution permission is inferred.",
                    "No case attachments or case-viewer grants are read. Aggregate permissions alone do not authorize access to cases."]}
        report["report_sha256"]=_digest(report)
        # Audit contains identifiers/digests/counts, never titles, values or
        # any case codes. The same transaction rolls back if audit fails.
        store.audit(conn,principal,"analysis.portfolio_compared",
                    selections=[{"view_id":s.view_id,"revision":s.revision,
                                 "sha256":s.expected_content_sha256} for s in request.selections],
                    baseline_index=request.baseline_index,
                    comparable_count=report["comparable_count"],
                    incomparable_count=report["incomparable_count"],
                    conflicting_pair_count=len(conflicts),
                    report_sha256=report["report_sha256"])
        return report
