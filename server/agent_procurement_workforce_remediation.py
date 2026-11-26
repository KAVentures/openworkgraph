"""Opt-in local audit remediation register, separate from transient review.

Tracks declared follow-up progress against a frozen, independently recomputed
worklist. Never establishes incident clearance, source truth or execution
authority. No raw dossiers, keys, receipts or case tags are retained.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from shared.agent_procurement import Strict, Alias, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit
from . import agent_procurement_workforce_audit_triage as triage
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_REGISTERS=20
MAX_FINDINGS=2000
MAX_EVENTS=5000
MAX_TOTAL_BYTES=32*1024*1024
Revision=Annotated[StrictInt,Field(ge=0,le=2**53-1)]
State=Literal["unassigned","assigned","investigating","awaiting_evidence","follow_up_reported"]
TRANSITIONS={
    "unassigned":{"assigned"},
    "assigned":{"investigating","awaiting_evidence","assigned"},
    "investigating":{"awaiting_evidence","follow_up_reported","assigned"},
    "awaiting_evidence":{"investigating","assigned"},
    "follow_up_reported":{"investigating","assigned"},
}


class Scope(Strict):
    worklist_sha256: Hash


class Change(Scope):
    expected_revision: Revision
    expected_history_sha256: Hash
    finding_id: Hash
    expected_finding_sha256: Hash
    state: State
    owner_alias: Alias
    reviewer_alias: Alias
    evidence_reference: Alias | None = None
    confirmed: StrictBool


class Forget(Scope):
    expected_revision: Revision
    expected_history_sha256: Hash
    confirmed_exported: StrictBool


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_audit_remediation (
        worklist_sha256 TEXT PRIMARY KEY, register_json TEXT NOT NULL)""")


def _encode(value):
    return audit._canonical(value).decode("ascii")


def _stamp(now):
    now=int(time.time()*1000) if now is None else now
    if type(now) is not int or not 0<=now<=10**15:
        raise ValueError("invalid local clock")
    return now


def _check_size(conn,pin,encoded):
    size=conn.execute("""SELECT COALESCE(SUM(length(register_json)),0)
        FROM procurement_audit_remediation WHERE worklist_sha256<>?""",(pin,)).fetchone()[0]
    if size+len(encoded)>MAX_TOTAL_BYTES:
        raise journal.JournalError("Remediation register capacity reached; export and remove a register",409)


def _transition(current,state,owner,reference):
    if state not in TRANSITIONS[current["state"]]:
        raise ValueError("unsupported follow-up transition")
    if state=="follow_up_reported" and not reference:
        raise ValueError("reported follow-up requires a declared evidence reference")
    if state!="assigned" and owner!=current["owner_alias"]:
        raise ValueError("owner change requires explicit assignment transition")
    if (state,owner,reference)==(current["state"],current["owner_alias"],current["evidence_reference"]):
        raise ValueError("unchanged follow-up state")


def _load(conn,pin):
    row=conn.execute("SELECT register_json FROM procurement_audit_remediation WHERE worklist_sha256=?",(pin,)).fetchone()
    if row is None:
        raise journal.JournalError("Local remediation register not found",404)
    value=json.loads(row[0])
    if value["worklist_sha256"]!=pin or digest(value["basis"])!=value["basis_sha256"]:
        raise ValueError("stored remediation basis mismatch")
    previous=value["basis_sha256"]
    if value["basis"]["worklist_sha256"]!=pin:
        raise ValueError("stored remediation scope mismatch")
    findings={f["finding_id"]:f for f in value["basis"]["findings"]}
    if len(findings)!=len(value["basis"]["findings"]):
        raise ValueError("duplicate stored finding")
    states={key:{"state":"unassigned","owner_alias":None,"evidence_reference":None} for key in findings}
    previous_stamp=_stamp(value["basis"]["created_at_ms"])
    for index,event in enumerate(value["events"],1):
        body={k:v for k,v in event.items() if k!="event_sha256"}
        if (event["previous_sha256"]!=previous or event["revision"]!=index
                or digest(body)!=event["event_sha256"]):
            raise ValueError("stored remediation history mismatch")
        current=states[event["finding_id"]]
        if current["state"]!=event["previous_state"] or current["owner_alias"]!=event["previous_owner_alias"]:
            raise ValueError("stored remediation history state mismatch")
        if event["finding_sha256"]!=findings[event["finding_id"]]["finding_sha256"] or _stamp(event["recorded_at_ms"])<previous_stamp:
            raise ValueError("stored remediation event scope or clock mismatch")
        _transition(current,event["state"],event["owner_alias"],event["evidence_reference"])
        previous_stamp=event["recorded_at_ms"]
        states[event["finding_id"]]={k:event[k] for k in ("state","owner_alias","evidence_reference")}
        previous=event["event_sha256"]
    if value["revision"]!=len(value["events"]) or value["history_sha256"]!=previous:
        raise ValueError("stored remediation history head mismatch")
    return value,states


def _view(value,states):
    rows=[{**f,**states[f["finding_id"]]} for f in value["basis"]["findings"]]
    return {"format":"openworkgraph.workforce-audit-remediation.v1",
            "worklist_sha256":value["worklist_sha256"],
            "timeline_sha256":value["basis"]["timeline_sha256"],
            "manifest_sha256":value["basis"]["manifest_sha256"],
            "created_at_ms":value["basis"]["created_at_ms"],
            "basis_sha256":value["basis_sha256"],"lineage":value["basis"].get("lineage"),"revision":value["revision"],
            "history_sha256":value["history_sha256"],"finding_count":len(rows),
            "findings":rows,"events":value["events"],
            "counts_by_state":{s:sum(r["state"]==s for r in rows) for s in TRANSITIONS},
            "rollback_referrals_retained":sum(r["priority"]==0 for r in rows),
            "all_findings_remain_reviewable":True,"result_persisted":True,
            "reviewer_identity_verified":False,"work_completed_verified":False,
            "incident_clearance_established":False,"execution_authorized":False,
            "limitations":["Local hash chain detects inconsistent history; it is not a trusted external timestamp or tamper-proof ledger.",
                           "Reported follow-up is an operator declaration, not verified completion or clearance.",
                           "Original signed evidence and independent pins must be retained separately."]}


def create(documents,manifest,*,expected_manifest_sha256,expected_worklist_sha256,confirmed,now=None):
    if confirmed is not True:
        raise ValueError("explicit register retention confirmation required")
    work=triage.worklist(documents,manifest,expected_manifest_sha256=expected_manifest_sha256)
    pin=work["worklist_sha256"]
    if pin!=expected_worklist_sha256 or not 1<=work["finding_count"]<=MAX_FINDINGS:
        raise ValueError("exact nonempty worklist within local register capacity required")
    basis={"worklist_sha256":pin,"timeline_sha256":work["timeline_sha256"],
           "manifest_sha256":work["manifest_sha256"],"created_at_ms":_stamp(now),
           "findings":[{k:f[k] for k in ("finding_id","finding_sha256","kind","priority","scope")}
                       for f in work["findings"]]}
    h=digest(basis)
    value={"worklist_sha256":pin,"basis":basis,"basis_sha256":h,
           "history_sha256":h,"revision":0,"events":[]}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        if conn.execute("SELECT 1 FROM procurement_audit_remediation WHERE worklist_sha256=?",(pin,)).fetchone():
            old,states=_load(conn,pin)
            return _view(old,states)
        if conn.execute("SELECT COUNT(*) FROM procurement_audit_remediation").fetchone()[0]>=MAX_REGISTERS:
            raise journal.JournalError("Local remediation register limit reached",409)
        encoded=_encode(value);_check_size(conn,pin,encoded)
        conn.execute("INSERT INTO procurement_audit_remediation VALUES (?,?)",(pin,encoded))
        stored,states=_load(conn,pin)
        return _view(stored,states)


def list_registers(document):
    if document!={}:
        raise ValueError("empty list request required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        out=[]
        for row in conn.execute("SELECT worklist_sha256 FROM procurement_audit_remediation ORDER BY worklist_sha256"):
            value,states=_load(conn,row[0])
            view=_view(value,states)
            out.append({k:view[k] for k in ("worklist_sha256","timeline_sha256","created_at_ms",
                       "revision","history_sha256","finding_count","counts_by_state","rollback_referrals_retained")})
        return {"registers":out,"execution_authorized":False}


def status(document):
    req=Scope.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        return _view(*_load(conn,req.worklist_sha256))


def _cas(value,req):
    if value["revision"]!=req.expected_revision or value["history_sha256"]!=req.expected_history_sha256:
        raise journal.JournalError("Remediation register changed; reload before action",409)


def change(document,*,now=None):
    req=Change.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit follow-up confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,states=_load(conn,req.worklist_sha256);_cas(value,req)
        f=next((x for x in value["basis"]["findings"] if x["finding_id"]==req.finding_id),None)
        if f is None or f["finding_sha256"]!=req.expected_finding_sha256:
            raise ValueError("unknown or changed finding")
        current=states[req.finding_id]
        _transition(current,req.state,req.owner_alias,req.evidence_reference)
        if value["revision"]>=MAX_EVENTS:
            raise journal.JournalError("Remediation history capacity reached",409)
        stamp=_stamp(now)
        previous_stamp=value["events"][-1]["recorded_at_ms"] if value["events"] else value["basis"]["created_at_ms"]
        if stamp<previous_stamp:
            raise ValueError("local clock regressed")
        event={"revision":value["revision"]+1,"previous_sha256":value["history_sha256"],
               "finding_id":req.finding_id,"finding_sha256":req.expected_finding_sha256,
               "previous_state":current["state"],"previous_owner_alias":current["owner_alias"],
               "state":req.state,"owner_alias":req.owner_alias,"declared_reviewer_alias":req.reviewer_alias,
               "evidence_reference":req.evidence_reference,"recorded_at_ms":stamp}
        event["event_sha256"]=digest(event)
        value["events"].append(event);value["revision"]+=1;value["history_sha256"]=event["event_sha256"]
        encoded=_encode(value);_check_size(conn,req.worklist_sha256,encoded)
        conn.execute("UPDATE procurement_audit_remediation SET register_json=? WHERE worklist_sha256=?",
                     (encoded,req.worklist_sha256))
        return _view(*_load(conn,req.worklist_sha256))


def forget(document):
    req=Forget.model_validate(document)
    if not req.confirmed_exported:
        raise ValueError("explicit export and removal confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,_=_load(conn,req.worklist_sha256);_cas(value,req)
        conn.execute("DELETE FROM procurement_audit_remediation WHERE worklist_sha256=?",(req.worklist_sha256,))
        return {"removed":True,"worklist_sha256":req.worklist_sha256,
                "removed_history_sha256":req.expected_history_sha256,
                "backups_erased":False,"execution_authorized":False}
