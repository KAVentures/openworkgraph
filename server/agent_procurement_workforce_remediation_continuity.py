"""Cross-worklist remediation continuity for an exact appended audit history.

The previous register remains intact. Matching identifiers are not proof of
business identity or clearance. Only explicitly selected owners carry forward
as NEW assignments; prior progress and references never become current facts.
"""
from __future__ import annotations

from pydantic import Field, StrictBool

from shared.agent_procurement import Strict, Alias, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit
from . import agent_procurement_workforce_audit_triage as triage
from . import agent_procurement_workforce_timeline as timeline
from . import agent_procurement_workforce_remediation as register
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_CARRY=200


class Preview(Strict):
    previous_worklist_sha256: Hash
    expected_previous_revision: register.Revision
    expected_previous_history_sha256: Hash
    previous_manifest_sha256: Hash
    expected_current_worklist_sha256: Hash
    confirmed: StrictBool


class Carry(Preview):
    expected_continuity_sha256: Hash
    reviewer_alias: Alias
    carry_finding_ids: list[Hash] = Field(max_length=MAX_CARRY)
    confirm_local_retention: StrictBool


def _worklists(documents,manifest,expected_manifest_sha256,req):
    # The complete timeline is independently recomputed first. Then locate
    # the exact independently pinned previous manifest as a strict prefix.
    current_timeline=timeline.review(documents,manifest,
                                    expected_manifest_sha256=expected_manifest_sha256)
    current=triage.from_timeline(current_timeline)
    if current["worklist_sha256"]!=req.expected_current_worklist_sha256:
        raise ValueError("current worklist pin mismatch")
    if current["worklist_sha256"]==req.previous_worklist_sha256:
        raise ValueError("distinct successor worklist required")
    matches=[]
    for n in range(2,len(documents)):
        prefix={**manifest,"entries":manifest["entries"][:n]}
        if digest(prefix)==req.previous_manifest_sha256:
            matches.append((n,prefix))
    if len(matches)!=1:
        raise ValueError("previous manifest must be an exact strict prefix")
    n,prefix=matches[0]
    prior=triage.worklist(documents[:n],prefix,
                         expected_manifest_sha256=req.previous_manifest_sha256)
    if prior["worklist_sha256"]!=req.previous_worklist_sha256:
        raise ValueError("previous worklist does not match appended evidence")
    return prior,current,n


def _compare(previous_view,prior,current,prefix_count):
    # Ensure stored minimized basis really describes the recomputed prior
    # worklist, not merely a matching top-level digest string.
    fields=("finding_id","finding_sha256","kind","priority","scope")
    stored=[{k:f[k] for k in fields} for f in previous_view["findings"]]
    original=[{k:f[k] for k in fields} for f in prior["findings"]]
    if (audit._canonical(stored)!=audit._canonical(original)
            or previous_view["timeline_sha256"]!=prior["timeline_sha256"]
            or previous_view["manifest_sha256"]!=prior["manifest_sha256"]):
        raise ValueError("stored register basis does not match original evidence")
    old={f["finding_id"]:f for f in previous_view["findings"]}
    new={f["finding_id"]:f for f in current["findings"]}
    rows=[]
    for key in sorted(set(old)|set(new)):
        a,b=old.get(key),new.get(key)
        if a is None:
            relation="new_finding"
        elif b is None:
            relation="not_in_successor"
        elif a["finding_sha256"]==b["finding_sha256"]:
            relation="same_evidence_pin"
        else:
            relation="changed_evidence_pin"
        carry=bool(a and b and a["owner_alias"] and relation=="same_evidence_pin")
        rows.append({"finding_id":key,"kind":(b or a)["kind"],
                     "priority":(b or a)["priority"],"scope":(b or a)["scope"],
                     "relation":relation,
                     "previous_finding_sha256":a["finding_sha256"] if a else None,
                     "current_finding_sha256":b["finding_sha256"] if b else None,
                     "previous_state":a["state"] if a else None,
                     "previous_owner_alias":a["owner_alias"] if a else None,
                     "owner_carry_eligible":carry})
    body={"format":"openworkgraph.workforce-remediation-continuity.v1",
          "previous_worklist_sha256":prior["worklist_sha256"],
          "previous_manifest_sha256":prior["manifest_sha256"],
          "previous_revision":previous_view["revision"],
          "previous_history_sha256":previous_view["history_sha256"],
          "current_worklist_sha256":current["worklist_sha256"],
          "current_manifest_sha256":current["manifest_sha256"],
          "verified_prefix_snapshot_count":prefix_count,
          "findings":rows,
          "counts_by_relation":{k:sum(r["relation"]==k for r in rows) for k in
                ("same_evidence_pin","changed_evidence_pin","new_finding","not_in_successor")},
          "owner_carry_eligible_count":sum(r["owner_carry_eligible"] for r in rows),
          "prior_rollback_referrals_absent_from_successor":sum(
              r["priority"]==0 and r["relation"]=="not_in_successor" for r in rows),
          "progress_carried":False,"incident_clearance_established":False,
          "execution_authorized":False,
          "limitations":["Strict manifest extension does not prove complete history or trusted chronology.",
                         "Missing or changed findings remain reviewable in the previous register.",
                         "Only exact-pin matching owners may be explicitly carried as new assignments."]}
    return {**body,"continuity_sha256":digest(body)}


def _previous(conn,req):
    value,states=register._load(conn,req.previous_worklist_sha256)
    if (value["revision"]!=req.expected_previous_revision
            or value["history_sha256"]!=req.expected_previous_history_sha256):
        raise journal.JournalError("Previous register changed; reload continuity preview",409)
    return register._view(value,states)


def preview(documents,manifest,request,*,expected_manifest_sha256):
    req=Preview.model_validate(request)
    if not req.confirmed:
        raise ValueError("explicit comparison confirmation required")
    prior,current,n=_worklists(documents,manifest,expected_manifest_sha256,req)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");register._schema(conn)
        return _compare(_previous(conn,req),prior,current,n)


def carry(documents,manifest,request,*,expected_manifest_sha256,now=None):
    req=Carry.model_validate(request)
    if not req.confirmed or not req.confirm_local_retention:
        raise ValueError("explicit successor retention confirmation required")
    if len(set(req.carry_finding_ids))!=len(req.carry_finding_ids):
        raise ValueError("duplicate carry selection")
    prior,current,n=_worklists(documents,manifest,expected_manifest_sha256,req)
    pin=current["worklist_sha256"]
    if not 1<=current["finding_count"]<=register.MAX_FINDINGS:
        raise ValueError("successor worklist exceeds local register capacity or is empty")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");register._schema(conn)
        previous=_previous(conn,req)
        comparison=_compare(previous,prior,current,n)
        if comparison["continuity_sha256"]!=req.expected_continuity_sha256:
            raise ValueError("continuity preview pin mismatch")
        if conn.execute("SELECT 1 FROM procurement_audit_remediation WHERE worklist_sha256=?",(pin,)).fetchone():
            raise journal.JournalError("Successor register already exists; never overwrite existing progress",409)
        if conn.execute("SELECT COUNT(*) FROM procurement_audit_remediation").fetchone()[0]>=register.MAX_REGISTERS:
            raise journal.JournalError("Local remediation register limit reached",409)
        selected=set(req.carry_finding_ids)
        eligible={r["finding_id"]:r for r in comparison["findings"] if r["owner_carry_eligible"]}
        if not selected<=eligible.keys():
            raise ValueError("carry requires exact unchanged finding evidence and a prior owner")
        stamp=register._stamp(now)
        lineage={"previous_worklist_sha256":prior["worklist_sha256"],
                 "previous_manifest_sha256":prior["manifest_sha256"],
                 "previous_revision":previous["revision"],
                 "previous_history_sha256":previous["history_sha256"],
                 "continuity_sha256":comparison["continuity_sha256"],
                 "verified_prefix_snapshot_count":n,
                 "carried_owner_finding_ids":sorted(selected),
                 "declared_reviewer_alias":req.reviewer_alias}
        basis={"worklist_sha256":pin,"timeline_sha256":current["timeline_sha256"],
               "manifest_sha256":current["manifest_sha256"],"created_at_ms":stamp,
               "lineage":lineage,
               "findings":[{k:f[k] for k in ("finding_id","finding_sha256","kind","priority","scope")}
                           for f in current["findings"]]}
        h=digest(basis)
        value={"worklist_sha256":pin,"basis":basis,"basis_sha256":h,
               "history_sha256":h,"revision":0,"events":[]}
        if len(selected)>register.MAX_EVENTS:
            raise ValueError("carry exceeds remediation history capacity")
        for key in sorted(selected):
            row=eligible[key]
            event={"revision":value["revision"]+1,"previous_sha256":value["history_sha256"],
                   "finding_id":key,"finding_sha256":row["current_finding_sha256"],
                   "previous_state":"unassigned","previous_owner_alias":None,
                   "state":"assigned","owner_alias":row["previous_owner_alias"],
                   "declared_reviewer_alias":req.reviewer_alias,
                   "evidence_reference":None,"recorded_at_ms":stamp}
            event["event_sha256"]=digest(event)
            value["events"].append(event);value["revision"]+=1
            value["history_sha256"]=event["event_sha256"]
        encoded=register._encode(value);register._check_size(conn,pin,encoded)
        conn.execute("INSERT INTO procurement_audit_remediation VALUES (?,?)",(pin,encoded))
        return register._view(*register._load(conn,pin))
