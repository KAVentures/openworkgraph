"""Reconcile declared audit follow-ups against independently recomputed evidence.

Receipt pins prove exact bytes in the canonical JSON representation, not
reviewer identity, independent votes, actual assignments or incident closure.
No receipt can supersede another through upload order.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from pathlib import Path

from pydantic import Field, StrictBool

from shared.agent_procurement import Strict, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit
from . import agent_procurement_workforce_timeline as timeline
from . import agent_procurement_workforce_audit_triage as triage

MAX_RECEIPTS = 40
SELECTION_FIELDS = tuple(triage.Selection.model_fields)


class PinnedReceipt(Strict):
    expected_triage_sha256: Hash
    receipt: dict


class ReceiptSet(Strict):
    confirmed: StrictBool
    receipts: list[PinnedReceipt] = Field(min_length=1,max_length=MAX_RECEIPTS)


def _recompute_receipt(work, entry):
    receipt = entry.receipt
    if receipt.get("triage_sha256") != entry.expected_triage_sha256:
        raise ValueError("receipt digest does not match independently supplied pin")
    selections = receipt.get("dispositions")
    if not isinstance(selections,list) or not 1<=len(selections)<=triage.MAX_SELECTION:
        raise ValueError("invalid receipt selection count")
    request = {"expected_worklist_sha256":receipt.get("worklist_sha256"),
               "reviewer_alias":receipt.get("declared_reviewer_alias"),
               "confirmed":True,"selections":[]}
    for selection in selections:
        if not isinstance(selection,dict) or not set(SELECTION_FIELDS)<=selection.keys():
            raise ValueError("incomplete receipt disposition")
        request["selections"].append({key:selection[key] for key in SELECTION_FIELDS})
    expected = triage._record(work,request)
    if audit._canonical(expected)!=audit._canonical(receipt):
        raise ValueError("receipt does not match exact recomputation against current worklist")
    return expected


def _classify(declarations):
    """No voting, last-write-wins, or implicit closure."""
    if not declarations:
        return "unaddressed",[]
    dimensions = []
    for field in ("disposition","owner_alias","external_reference"):
        if len({d[field] for d in declarations})>1:
            dimensions.append(field)
    if dimensions:
        return "conflicting_declarations",dimensions
    if len(declarations)==1:
        return "one_declaration",[]
    return "matching_declarations",[]


def _reconcile(work, document):
    req=ReceiptSet.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit reconciliation confirmation required")
    pins=[e.expected_triage_sha256 for e in req.receipts]
    if len(pins)!=len(set(pins)):
        raise ValueError("duplicate receipt pin")
    grouped=defaultdict(list)
    receipt_summaries=[]
    alias_spellings=defaultdict(set)
    for entry in sorted(req.receipts,key=lambda e:e.expected_triage_sha256):
        receipt=_recompute_receipt(work,entry)
        reviewer=receipt["declared_reviewer_alias"]
        alias_spellings[reviewer.casefold()].add(reviewer)
        receipt_summaries.append({"triage_sha256":receipt["triage_sha256"],
                                  "declared_reviewer_alias":reviewer,
                                  "selected_count":receipt["selected_count"]})
        for choice in receipt["dispositions"]:
            grouped[choice["finding_id"]].append({
                "triage_sha256":receipt["triage_sha256"],
                "declared_reviewer_alias":reviewer,
                "disposition":choice["disposition"],
                "owner_alias":choice["owner_alias"],
                "external_reference":choice["external_reference"]})
    rows=[]
    for f in work["findings"]:
        declarations=grouped[f["finding_id"]]
        status,dimensions=_classify(declarations)
        by_reviewer=defaultdict(set)
        for d in declarations:
            by_reviewer[d["declared_reviewer_alias"].casefold()].add(
                (d["disposition"],d["owner_alias"],d["external_reference"]))
        rows.append({"finding_id":f["finding_id"],"finding_sha256":f["finding_sha256"],
                     "kind":f["kind"],"priority":f["priority"],"scope":f["scope"],
                     "status":status,"conflict_dimensions":dimensions,
                     "declared_reviewer_alias_count":len(by_reviewer),
                     "same_reviewer_conflict":any(len(v)>1 for v in by_reviewer.values()),
                     "declarations":declarations})
    counts=Counter(r["status"] for r in rows)
    # A referral is still outstanding even when every supplied declaration agrees.
    body={"format":"openworkgraph.workforce-audit-reconciliation.v1",
          "worklist_sha256":work["worklist_sha256"],"timeline_sha256":work["timeline_sha256"],
          "manifest_sha256":work["manifest_sha256"],
          "receipt_count":len(receipt_summaries),"receipts":receipt_summaries,
          "finding_count":len(rows),"counts_by_status":{k:counts[k] for k in
              ("unaddressed","one_declaration","matching_declarations","conflicting_declarations")},
          "unaddressed_rollback_referrals":sum(r["priority"]==0 and r["status"]=="unaddressed" for r in rows),
          "conflicting_rollback_referrals":sum(r["priority"]==0 and r["status"]=="conflicting_declarations" for r in rows),
          "casefold_reviewer_alias_variants":[sorted(v) for _,v in sorted(alias_spellings.items()) if len(v)>1],
          "findings":rows,
          "reviewer_identity_verified":False,"reviewer_independence_verified":False,
          "conflicts_resolved":False,"incident_clearance_established":False,
          "work_completed_verified":False,"external_ticket_created":False,
          "result_persisted":False,"execution_authorized":False,
          "limitations":["Only receipts for this exact recomputed worklist are accepted.",
                         "Matching declarations do not establish independent agreement or completion.",
                         "Upload order never resolves a conflict; no external assignments were made."]}
    result={**body,"reconciliation_sha256":digest(body)}
    if len(audit._canonical(result))>audit.MAX_BYTES:
        raise ValueError("reconciliation exceeds portable output limit")
    return result


def reconcile(documents,manifest,receipts,*,expected_manifest_sha256):
    work=triage.worklist(documents,manifest,expected_manifest_sha256=expected_manifest_sha256)
    return _reconcile(work,receipts)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path)
    parser.add_argument("--manifest-sha256",required=True)
    parser.add_argument("--receipts",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("dossiers",nargs="+",type=Path)
    args=parser.parse_args(argv)
    try:
        if not 2<=len(args.dossiers)<=timeline.MAX_SNAPSHOTS:
            raise ValueError("invalid timeline size")
        docs=[];size=0
        for path in args.dossiers:
            size+=path.stat().st_size
            if size>timeline.MAX_INPUT_BYTES:
                raise ValueError("aggregate dossier input too large")
            docs.append(audit.read_json(path))
        if sum(len(audit._canonical(d)) for d in docs)>timeline.MAX_INPUT_BYTES:
            raise ValueError("aggregate canonical input too large")
        result=reconcile(docs,audit.read_json(args.manifest),audit.read_json(args.receipts),
                         expected_manifest_sha256=args.manifest_sha256)
        audit.write_json(args.output,result)
    except (ValueError,TypeError,KeyError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Audit reconciliation refused: invalid evidence, receipt, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
