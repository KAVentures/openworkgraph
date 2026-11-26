"""Evidence-bound historical audit findings and portable triage receipts.

A receipt documents a declared reviewer referral, not an incident closure,
identity attestation, production authorization or source verification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool

from shared.agent_procurement import Strict, Alias, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit
from . import agent_procurement_workforce_timeline as timeline

MAX_FINDINGS = 30000
MAX_SELECTION = 200
ACTIONS = ("escalate", "investigate", "request_evidence", "acknowledge")
PRIORITIES = {"rollback_referral": 0, "cutoff_regression": 1,
              "unreadable_journals": 1, "site_absent": 1,
              "journal_absent_latest": 1, "cohort_change": 2,
              "excluded_journal": 2, "key_change": 2, "freshness_policy_change": 2}


def _finding(kind, scope, evidence):
    # Stable identity across worklists, separate exact evidence pin.
    identity = {"kind": kind, "scope": scope}
    body = {**identity, "finding_id": digest(identity),
            "priority": PRIORITIES[kind], "evidence": evidence}
    return {**body, "finding_sha256": digest(body)}


def from_timeline(result):
    """Internal use only, on a server-recomputed timeline."""
    snapshots = {s["snapshot"]: s for s in result["snapshots"]}
    findings = []
    def add(kind, scope, indices, details=None):
        evidence = {"snapshots": [{"snapshot": i,
                     "dossier_sha256": snapshots[i]["dossier_sha256"],
                     "report_sha256": snapshots[i]["report_sha256"]} for i in sorted(set(indices))]}
        if details is not None:
            evidence["details"] = details
        findings.append(_finding(kind, scope, evidence))
    for row in result["journals"]:
        scope = {"installation_alias": row["installation_alias"], "journal_tag": row["journal_tag"]}
        for kind, field in (("rollback_referral","rollback_referral_snapshots"),
                            ("cutoff_regression","cutoff_regression_snapshots"),
                            ("cohort_change","changed_cohort_snapshots"),
                            ("excluded_journal","excluded_snapshots")):
            if row[field]:
                add(kind, scope, row[field])
        if not row["present_in_latest_snapshot"]:
            add("journal_absent_latest",scope,[row["last_snapshot"],len(snapshots)])
    for i, snap in sorted(snapshots.items()):
        for alias in snap["aliases_absent_since_previous"]:
            add("site_absent",{"installation_alias":alias,"snapshot":i},[i-1,i])
        for change in snap["declared_key_changes"]:
            add("key_change",{"installation_alias":change["installation_alias"],"snapshot":i},
                [i], {"previous_key_sha256":change["previous_key_sha256"],
                      "current_key_sha256":change["current_key_sha256"]})
        if snap["unreadable_journals"]:
            add("unreadable_journals",{"snapshot":i},[i],
                {"declared_unreadable_journals":snap["unreadable_journals"]})
        if snap["packet_age_policy_changed"]:
            add("freshness_policy_change",{"snapshot":i},[i-1,i],
                {"previous_maximum_packet_age_ms":snapshots[i-1]["maximum_packet_age_ms"],
                 "current_maximum_packet_age_ms":snap["maximum_packet_age_ms"]})
    if len(findings)>MAX_FINDINGS:
        raise ValueError("audit finding limit exceeded")
    findings.sort(key=lambda f:(f["priority"],f["kind"],f["finding_id"]))
    if len({f["finding_id"] for f in findings})!=len(findings):
        raise ValueError("duplicate audit finding identity")
    counts = Counter(f["kind"] for f in findings)
    body = {"format":"openworkgraph.workforce-audit-findings.v1",
            "timeline_sha256":result["timeline_sha256"],
            "manifest_sha256":result["manifest_sha256"],
            "finding_count":len(findings),"counts_by_kind":dict(sorted(counts.items())),
            "findings":findings,"incident_clearance_established":False,
            "execution_authorized":False,
            "limitations":["Findings refer only to supplied recomputed historical evidence.",
                           "No finding is an independently verified incident or a clearance decision."]}
    return {**body,"worklist_sha256":digest(body)}


class Selection(Strict):
    finding_id: Hash
    expected_finding_sha256: Hash
    disposition: Literal["escalate","investigate","request_evidence","acknowledge"]
    owner_alias: Alias | None = None
    external_reference: Alias | None = None


class Decision(Strict):
    expected_worklist_sha256: Hash
    reviewer_alias: Alias
    confirmed: StrictBool
    selections: list[Selection] = Field(min_length=1,max_length=MAX_SELECTION)


def _record(worklist, request):
    req=Decision.model_validate(request)
    if not req.confirmed or req.expected_worklist_sha256!=worklist["worklist_sha256"]:
        raise ValueError("explicit exact worklist confirmation required")
    indexed={f["finding_id"]:f for f in worklist["findings"]}
    selected={}
    for choice in req.selections:
        if choice.finding_id in selected:
            raise ValueError("duplicate finding disposition")
        finding=indexed.get(choice.finding_id)
        if finding is None or finding["finding_sha256"]!=choice.expected_finding_sha256:
            raise ValueError("unknown or changed finding")
        # A critical historical referral can be escalated/investigated or
        # sent for evidence, but not dismissed by a simple acknowledgment.
        if finding["priority"]==0 and choice.disposition=="acknowledge":
            raise ValueError("rollback referral requires further review")
        if choice.disposition!="acknowledge" and choice.owner_alias is None:
            raise ValueError("follow-up requires a declared owner")
        selected[choice.finding_id]={**choice.model_dump(),
                                     "kind":finding["kind"],"priority":finding["priority"],
                                     "scope":finding["scope"]}
    remaining=[f for f in worklist["findings"] if f["finding_id"] not in selected]
    body={"format":"openworkgraph.workforce-audit-triage.v1",
          "worklist_sha256":worklist["worklist_sha256"],
          "timeline_sha256":worklist["timeline_sha256"],
          "manifest_sha256":worklist["manifest_sha256"],
          "declared_reviewer_alias":req.reviewer_alias,
          "reviewer_identity_verified":False,
          "finding_count":worklist["finding_count"],
          "selected_count":len(selected),"unselected_count":len(remaining),
          "unselected_rollback_referrals":sum(f["priority"]==0 for f in remaining),
          "dispositions":[selected[k] for k in sorted(selected)],
          "all_findings_addressed_in_receipt":not remaining,
          "incident_clearance_established":False,"work_completed_verified":False,
          "external_ticket_created":False,"result_persisted":False,"execution_authorized":False,
          "limitations":["Declared reviewer and owner aliases are not authenticated identities.",
                         "Partial receipts do not dismiss unselected findings; receipts do not clear any finding.",
                         "References are declarations, not verified external tickets. No external action was taken."]}
    return {**body,"triage_sha256":digest(body)}


def worklist(documents,manifest,*,expected_manifest_sha256):
    return from_timeline(timeline.review(documents,manifest,
                         expected_manifest_sha256=expected_manifest_sha256))


def record(documents,manifest,request,*,expected_manifest_sha256):
    # Never authorize against a user-supplied worklist or receipt alone.
    return _record(worklist(documents,manifest,expected_manifest_sha256=expected_manifest_sha256),request)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path)
    parser.add_argument("--manifest-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--decision",type=Path)
    parser.add_argument("dossiers",nargs="+",type=Path)
    args=parser.parse_args(argv)
    try:
        if not 2<=len(args.dossiers)<=timeline.MAX_SNAPSHOTS:
            raise ValueError("invalid timeline size")
        docs=[];size=0
        for path in args.dossiers:
            size+=path.stat().st_size
            if size>timeline.MAX_INPUT_BYTES:
                raise ValueError("aggregate input exceeds limit")
            docs.append(audit.read_json(path))
        if sum(len(audit._canonical(d)) for d in docs)>timeline.MAX_INPUT_BYTES:
            raise ValueError("aggregate canonical input exceeds limit")
        manifest=audit.read_json(args.manifest)
        result=worklist(docs,manifest,expected_manifest_sha256=args.manifest_sha256)
        if args.decision:
            result=_record(result,audit.read_json(args.decision))
        audit.write_json(args.output,result)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Audit triage refused: invalid evidence, decision, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
