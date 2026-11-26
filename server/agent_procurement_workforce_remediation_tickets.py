"""Read-only Jira ticket synchronization for exact local remediation findings.

Explicit observed-record binding, existing tenant/OAuth opt-in, minimized
source state history. Jira resolution never clears an OWG finding.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, StrictBool

from shared.agent_procurement import Strict, digest
from shared.process_shadow_review import Hash
from shared.agent_procurement_source_outcome import _ms
from . import agent_procurement_workforce_remediation as register
from . import agent_procurement_source_outcome as source
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_BINDINGS=200
MAX_CHECKS=1000
MAX_BYTES=16*1024*1024
PREDICATE="jira_issue_resolved_in_window"


class Scope(register.Scope):
    expected_revision: register.Revision
    expected_history_sha256: Hash
    expected_ticket_revision: register.Revision
    expected_ticket_history_sha256: Hash
    finding_id: Hash
    expected_finding_sha256: Hash


class Bind(Scope):
    resource_ref: source.Ref
    confirm_operator_mapping: StrictBool
    confirm_local_retention: StrictBool


class Check(Scope):
    confirm_source_read: StrictBool


def _schema(conn):
    register._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_audit_remediation_tickets
        (worklist_sha256 TEXT PRIMARY KEY, ledger_json TEXT NOT NULL)""")


def _empty(value):
    basis={"worklist_sha256":value["worklist_sha256"],"register_basis_sha256":value["basis_sha256"]}
    return {**basis,"revision":0,"history_sha256":digest(basis),"events":[]}


def _load(conn,value):
    raw=conn.execute("SELECT ledger_json FROM procurement_audit_remediation_tickets WHERE worklist_sha256=?",
                     (value["worklist_sha256"],)).fetchone()
    ledger=json.loads(raw[0]) if raw else _empty(value)
    empty=_empty(value)
    if any(ledger[k]!=empty[k] for k in ("worklist_sha256","register_basis_sha256")):
        raise ValueError("ticket ledger belongs to a different register")
    findings={f["finding_id"]:f for f in value["basis"]["findings"]}
    previous=empty["history_sha256"];bindings={};checks=[]
    for i,event in enumerate(ledger["events"],1):
        if (event["revision"]!=i or event["previous_sha256"]!=previous
                or digest({k:v for k,v in event.items() if k!="event_sha256"})!=event["event_sha256"]):
            raise ValueError("ticket history integrity mismatch")
        fid=event["finding_id"]
        if fid not in findings or event["finding_sha256"]!=findings[fid]["finding_sha256"]:
            raise ValueError("ticket finding scope mismatch")
        if event["kind"]=="bind":
            if fid in bindings or any(b["record_identity_hmac"]==event["record_identity_hmac"] for b in bindings.values()):
                raise ValueError("duplicate ticket binding")
            bindings[fid]=event
        elif event["kind"]=="check":
            if fid not in bindings or event["binding_sha256"]!=bindings[fid]["event_sha256"]:
                raise ValueError("ticket check binding mismatch")
            checks.append(event)
        else:
            raise ValueError("unknown ticket history event")
        previous=event["event_sha256"]
    if ledger["revision"]!=len(ledger["events"]) or ledger["history_sha256"]!=previous:
        raise ValueError("ticket history head mismatch")
    return ledger,bindings,checks


def _view(value,ledger,bindings,checks):
    latest={c["finding_id"]:c for c in checks}
    rows=[]
    for fid,binding in sorted(bindings.items()):
        check=latest.get(fid)
        rows.append({"finding_id":fid,"finding_sha256":binding["finding_sha256"],
                     "resource_ref":binding["resource_ref"],"binding_sha256":binding["event_sha256"],
                     "latest_check":check,
                     "check_matches_current_register":bool(check and check["register_history_sha256"]==value["history_sha256"])})
    return {"format":"openworkgraph.workforce-remediation-ticket-ledger.v1",
            "worklist_sha256":value["worklist_sha256"],"register_revision":value["revision"],
            "register_history_sha256":value["history_sha256"],"ticket_revision":ledger["revision"],
            "ticket_history_sha256":ledger["history_sha256"],"bindings":rows,"events":ledger["events"],
            "provider":"jira","external_write_performed":False,"incident_clearance_established":False,
            "work_completed_verified":False,"operator_mapping_verified":False,"execution_authorized":False}


def _prepare(conn,req):
    value,states=register._load(conn,req.worklist_sha256);register._cas(value,req)
    ledger,bindings,checks=_load(conn,value)
    if ledger["revision"]!=req.expected_ticket_revision or ledger["history_sha256"]!=req.expected_ticket_history_sha256:
        raise journal.JournalError("Ticket ledger changed; reload before action",409)
    f=next((x for x in value["basis"]["findings"] if x["finding_id"]==req.finding_id),None)
    if f is None or f["finding_sha256"]!=req.expected_finding_sha256:
        raise ValueError("unknown or changed ticket finding")
    return value,states,ledger,bindings,checks


def _append(conn,value,ledger,req,kind,details):
    event={"kind":kind,"revision":ledger["revision"]+1,"previous_sha256":ledger["history_sha256"],
           "finding_id":req.finding_id,"finding_sha256":req.expected_finding_sha256,
           "register_revision":value["revision"],"register_history_sha256":value["history_sha256"],**details}
    event["event_sha256"]=digest(event)
    ledger["events"].append(event);ledger["revision"]+=1;ledger["history_sha256"]=event["event_sha256"]
    encoded=register._encode(ledger)
    size=conn.execute("SELECT COALESCE(SUM(length(ledger_json)),0) FROM procurement_audit_remediation_tickets WHERE worklist_sha256<>?",
                      (req.worklist_sha256,)).fetchone()[0]
    if size+len(encoded)>MAX_BYTES:
        raise journal.JournalError("Ticket ledger capacity reached",409)
    conn.execute("""INSERT INTO procurement_audit_remediation_tickets VALUES (?,?)
        ON CONFLICT(worklist_sha256) DO UPDATE SET ledger_json=excluded.ledger_json""",
        (req.worklist_sha256,encoded))
    return _view(value,*_load(conn,value))


def status(document):
    req=register.Scope.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,_=register._load(conn,req.worklist_sha256)
        return _view(value,*_load(conn,value))


def bind(document):
    req=Bind.model_validate(document)
    if not req.confirm_operator_mapping or not req.confirm_local_retention:
        raise ValueError("explicit ticket mapping and local retention required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,states,ledger,bindings,checks=_prepare(conn,req)
        if req.finding_id in bindings or len(bindings)>=MAX_BINDINGS:
            raise ValueError("ticket binding already exists or capacity reached")
        if states[req.finding_id]["state"]=="unassigned":
            raise ValueError("assign a declared owner before binding a ticket")
        identity=source._identity(source.resolve_resource_reference(req.resource_ref),PREDICATE)
        if any(b["record_identity_hmac"]==identity for b in bindings.values()):
            raise ValueError("ticket already mapped in this register")
        return _append(conn,value,ledger,req,"bind",{"resource_ref":req.resource_ref,
                       "record_identity_hmac":identity,"recorded_at_ms":register._stamp(None)})


def project(receipt,now):
    """Only minimized provider lifecycle facts; never expose text or IDs."""
    result={"source_state":"unknown","resolution_reported":False,"resolved_at_ms":None,
            "source_read_at_ms":None,"audit_complete":False,"reason":"source_unavailable"}
    if not isinstance(receipt,dict) or receipt.get("availability")!="source_reported":
        return result
    at=_ms(receipt.get("verified_at"))
    if at is None or at>now or now-at>300000:
        return {**result,"reason":"invalid_source_read_time"}
    if (receipt.get("provider"),receipt.get("object_type"),receipt.get("verification_method"),
            receipt.get("source_state_reported"))!=("jira","issue","explicit_opt_in_site_verified_oauth_jira_issue_status_read",True):
        return {**result,"reason":"unrecognized_source_provenance"}
    state=receipt.get("source_state")
    if state not in ("new","indeterminate","done"):
        return {**result,"reason":"unknown_source_state"}
    result.update(source_state=state,source_read_at_ms=at,audit_complete=receipt.get("audit_complete") is True,
                  reason="source_state_reported")
    outcome=receipt.get("verified_operational_outcome")
    if state=="done" and isinstance(outcome,dict) and outcome.get("verified") is True and (
        outcome.get("kind"),outcome.get("status"),outcome.get("scope"))==(
        "jira_issue_resolved","resolved","jira_issue_only_not_customer_acknowledgement_or_refund"):
        resolved=_ms(outcome.get("source_timestamp"))
        if resolved is not None and resolved<=at:
            result.update(resolution_reported=True,resolved_at_ms=resolved)
    return result


def check(document):
    req=Check.model_validate(document)
    if not req.confirm_source_read:
        raise ValueError("explicit Jira read confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,states,ledger,bindings,checks=_prepare(conn,req)
        binding=bindings.get(req.finding_id)
        if binding is None or len(checks)>=MAX_CHECKS:
            raise ValueError("existing ticket binding and check capacity required")
    # No SQLite writer lock during the provider read. The existing source
    # adapter checks the exact locally observed record, tenant and OAuth.
    resolved=source.resolve_resource_reference(binding["resource_ref"])
    if source._identity(resolved,PREDICATE)!=binding["record_identity_hmac"]:
        raise ValueError("bound Jira record identity changed")
    receipt=source._read_source(binding["resource_ref"],dict(resolved),PREDICATE)
    now=register._stamp(None)
    projection=project(receipt,now)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        value,states,ledger,bindings,checks=_prepare(conn,req)
        if bindings.get(req.finding_id)!=binding or len(checks)>=MAX_CHECKS:
            raise journal.JournalError("Ticket binding changed during read",409)
        return _append(conn,value,ledger,req,"check",{"binding_sha256":binding["event_sha256"],
                       "recorded_at_ms":now,**projection})
