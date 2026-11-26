"""Explicit local source predicate verification for retained monitoring cases.

Reuses the existing default-off Stripe read adapter. Operator-declared
case-to-record mapping is not independently established. No source writes.
"""
from __future__ import annotations

import time
import hashlib
import hmac
import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from shared.agent_procurement import Strict,digest
from shared.agent_procurement_source_outcome import project
from shared.process_shadow_review import Hash
from . import agent_procurement_monitor_journal as journal
from . import agent_procurement_corroboration as corroboration
from . import stripe_source_evidence
from .local_reference_lookup import resolve_resource_reference
from .local_auth import ensure_api_token
from . import db

MAX_BATCH=10
MAX_CHECKS=1500
Ref=Annotated[str,Field(pattern=r"^owg:r:[0-9a-f]{32}$")]


class Binding(Strict):
    slot_id: Hash
    case_token_sha256: Hash
    resource_ref: Ref
    predicate: Literal["invoice_paid_in_window"]


class Bind(corroboration.WindowRequest):
    confirm_operator_case_mapping: StrictBool
    confirm_case_outcome_defined_by_predicate: StrictBool
    bindings: list[Binding]=Field(min_length=1,max_length=MAX_BATCH)

    @model_validator(mode="after")
    def unique(self):
        for k in ("slot_id","case_token_sha256","resource_ref"):
            if len({getattr(x,k) for x in self.bindings})!=len(self.bindings):
                raise ValueError("duplicate source binding")
        return self


class Check(corroboration.WindowRequest):
    slot_ids: list[Hash]=Field(min_length=1,max_length=MAX_BATCH)
    confirm_source_read: StrictBool

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.slot_ids))!=len(self.slot_ids):raise ValueError("duplicate slot")
        return self


def _identity(record):
    if not isinstance(record,dict) or (record.get("provider"),record.get("resource_kind"),record.get("host"))!=("stripe","invoice","dashboard.stripe.com"):
        raise ValueError("observed Stripe invoice reference required")
    locator=record.get("resolver_locator")
    if not isinstance(locator,str) or not re.fullmatch(r"invoice:in_[A-Za-z0-9]{10,90}",locator):
        raise ValueError("invalid observed Stripe invoice locator")
    return hmac.new(ensure_api_token().encode("utf-8"),("owg-source-invoice-v1:"+locator).encode("ascii"),hashlib.sha256).hexdigest()


def bind(workflow_id,revision,document):
    req=Bind.model_validate(document)
    if not req.confirm_operator_case_mapping or not req.confirm_case_outcome_defined_by_predicate:
        raise ValueError("explicit case mapping and outcome definition confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");key,state,history=corroboration._read(conn,workflow_id,revision,req)
        window=corroboration._window(state,req)
        slots={s["slot_id"]:s["case_token_sha256"] for s in window["plan"]["slots"]}
        prior=state.get("source_outcome_bindings",[])
        if any(slots.get(b.slot_id)!=b.case_token_sha256 for b in req.bindings):
            raise ValueError("binding outside frozen window")
        if {b.slot_id for b in req.bindings}&{b["slot_id"] for b in prior}:
            raise ValueError("source binding is immutable")
        # Never pass caller-supplied native IDs or URLs to the source reader.
        identities={b.resource_ref:_identity(resolve_resource_reference(b.resource_ref)) for b in req.bindings}
        refs=set(identities)
        identity_pins=set(identities.values())
        if len(identity_pins)!=len(refs):raise ValueError("duplicate resolved source record")
        for other in conn.execute("SELECT workflow_id,revision,monitor_id FROM procurement_monitor_journals"):
            other_key=tuple(other)
            other_state=state if other_key==key else journal._read(conn,other_key)[0]
            if refs & {b["resource_ref"] for b in other_state.get("source_outcome_bindings",[])} or identity_pins & {b.get("record_identity_hmac") for b in other_state.get("source_outcome_bindings",[])}:
                raise ValueError("source record already bound")
        state["source_outcome_bindings"]=[*prior,*[
            {**b.model_dump(),"window_id":req.window_id,"binding_report_sha256":req.expected_report_sha256,
             "record_identity_hmac":identities[b.resource_ref]}
            for b in sorted(req.bindings,key=lambda x:x.slot_id)]]
        return journal._save(conn,key,state,history,"source_outcome_bind",{
            "window_id":req.window_id,"bound_cases":len(req.bindings)})


def check(workflow_id,revision,document):
    req=Check.model_validate(document)
    if not req.confirm_source_read:raise ValueError("explicit source read confirmation required")
    # Read and validate exact pins before any network call. Do not hold the
    # SQLite writer lock during external I/O; check pins again before commit.
    with db.connect() as conn:
        conn.execute("BEGIN");key,state,history=corroboration._read(conn,workflow_id,revision,req)
        window=corroboration._window(state,req)
        bindings={b["slot_id"]:b for b in state.get("source_outcome_bindings",[]) if b["window_id"]==req.window_id}
        if set(req.slot_ids)-set(bindings):raise ValueError("unbound source case")
        if len(state.get("source_outcome_checks",[]))+len(req.slot_ids)>MAX_CHECKS:
            raise journal.JournalError("Source check capacity reached",409)
        confirmed={x["slot_id"] for x in state.get("source_outcome_checks",[]) if x["status"]=="confirmed"}
        if confirmed&set(req.slot_ids):raise ValueError("confirmed source check is immutable")
        start=window["plan"]["request"]["window_start_ms"]
        end=window["plan"]["request"]["window_end_exclusive_ms"]
    rows=[]
    for sid in sorted(req.slot_ids):
        binding=bindings[sid];ref=binding["resource_ref"]
        resolved=resolve_resource_reference(ref)
        if _identity(resolved)!=binding["record_identity_hmac"]:
            raise ValueError("bound source record identity changed")
        receipt=stripe_source_evidence.stripe_record_receipt(ref,include_lifecycle=True,
            resolver=lambda requested:dict(resolved) if requested==ref else None)
        now=int(time.time()*1000)
        rows.append({"slot_id":sid,"window_id":req.window_id,"checked_at_ms":now,
                     "report_sha256":req.expected_report_sha256,**project(receipt,start,end,now)})
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");key,state,history=corroboration._read(conn,workflow_id,revision,req)
        corroboration._window(state,req)
        # Exact same retained state is required after the source reads.
        state["source_outcome_checks"]=[*state.get("source_outcome_checks",[]),*rows]
        return journal._save(conn,key,state,history,"source_outcome_check",{
            "window_id":req.window_id,"checked_cases":len(rows),
            "confirmed_cases":sum(x["status"]=="confirmed" for x in rows),"checks_sha256":digest(rows)})
