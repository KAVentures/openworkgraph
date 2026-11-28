"""Explicit source-invoice line comparison tied to a retained finance statement."""
from __future__ import annotations

import time
from typing import Annotated

from pydantic import Field,StrictBool,StrictInt,model_validator

from shared.agent_procurement import Strict,digest
from shared.agent_procurement_source_finance import compare
from shared.process_shadow_review import Hash
from . import agent_procurement_corroboration as corroboration
from . import agent_procurement_monitor_journal as journal
from . import agent_procurement_source_outcome as source
from . import db

MAX_CHECKS=1500


class Line(Strict):
    slot_id: Hash
    declared_amount_paid_minor_units: Annotated[StrictInt,Field(ge=0,le=10**18)]


class Request(corroboration.WindowRequest):
    expected_reconciliation_sha256: Hash
    confirm_source_amount_read: StrictBool
    confirm_gross_invoice_amount_not_booked_revenue: StrictBool
    rows: list[Line]=Field(min_length=1,max_length=10)

    @model_validator(mode="after")
    def unique(self):
        if len({x.slot_id for x in self.rows})!=len(self.rows):raise ValueError("duplicate invoice slot")
        return self


def _prepare(state,window,req):
    finance=next((x for x in state.get("finance_reconciliations",[]) if x["window_id"]==req.window_id),None)
    if finance is None or finance["reconciliation_sha256"]!=req.expected_reconciliation_sha256:
        raise journal.JournalError("Exact retained finance statement required",409)
    bindings={x["slot_id"]:x for x in state.get("source_outcome_bindings",[])
              if x["window_id"]==req.window_id and x["predicate"]=="invoice_paid_in_window"}
    confirmed={x["slot_id"]:x for x in state.get("source_outcome_checks",[])
               if x["window_id"]==req.window_id and x["status"]=="confirmed"}
    prior=state.get("source_finance_checks",[])
    latest={x["slot_id"]:x for x in prior}
    if len(prior)+len(req.rows)>MAX_CHECKS:raise journal.JournalError("Source finance check capacity reached",409)
    slots={x["slot_id"]:x for x in window["plan"]["slots"]}
    selected=[]
    for row in sorted(req.rows,key=lambda x:x.slot_id):
        sid=row.slot_id
        if sid not in bindings or sid not in confirmed or sid not in slots:
            raise ValueError("confirmed bound invoice slot required")
        previous=latest.get(sid)
        if previous is not None and (previous["status"]!="unknown" or
                previous["declared_amount_paid_minor_units"]!=str(row.declared_amount_paid_minor_units)):
            raise ValueError("retained invoice declaration or resolved check is immutable")
        selected.append((row,bindings[sid],confirmed[sid],slots[sid]["arm"]))
    return finance,selected


def check(workflow_id,revision,document):
    req=Request.model_validate(document)
    if not req.confirm_source_amount_read or not req.confirm_gross_invoice_amount_not_booked_revenue:
        raise ValueError("explicit financial source-read and scope confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN");key,state,history=corroboration._read(conn,workflow_id,revision,req)
        window=corroboration._window(state,req)
        if len(history)+1+journal._reserved_entries(state,history)>journal.MAX_ENTRIES:
            raise journal.JournalError("No ordinary audit entry available",409)
        finance,selected=_prepare(state,window,req)
        start=window["plan"]["request"]["window_start_ms"]
        end=window["plan"]["request"]["window_end_exclusive_ms"]
    # Preflight every exact resolved identity before initiating external reads.
    resolved={}
    for row,binding,_,_ in selected:
        record=source.resolve_resource_reference(binding["resource_ref"])
        if source._identity(record,binding["predicate"])!=binding["record_identity_hmac"]:
            raise ValueError("bound source record identity changed")
        resolved[row.slot_id]=dict(record)
    results=[]
    for row,binding,previous,arm in selected:
        receipt=source._read_source(binding["resource_ref"],resolved[row.slot_id],binding["predicate"],include_invoice_amounts=True)
        now=int(time.time()*1000)
        result=compare(receipt,start,end,now,previous["paid_at_ms"],finance["currency"],row.declared_amount_paid_minor_units)
        results.append({"slot_id":row.slot_id,"window_id":req.window_id,"arm":arm,
            "currency":finance["currency"],"reconciliation_sha256":req.expected_reconciliation_sha256,
            "monitor_report_sha256":req.expected_report_sha256,"checked_at_ms":now,
            "declared_amount_paid_minor_units":str(row.declared_amount_paid_minor_units),**result})
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");key,state,history=corroboration._read(conn,workflow_id,revision,req)
        window=corroboration._window(state,req)
        _prepare(state,window,req)
        state["source_finance_checks"]=[*state.get("source_finance_checks",[]),*results]
        return journal._save(conn,key,state,history,"source_finance_check",{
            "window_id":req.window_id,"checked_cases":len(results),
            "matched_cases":sum(x["status"]=="match" for x in results),
            "mismatched_cases":sum(x["status"]=="mismatch" for x in results),
            "checks_sha256":digest(results)})
