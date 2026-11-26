"""Retain separate reviewer outcome declarations, pinned to a closed window."""
from __future__ import annotations

import time
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_corroboration import summarize
from shared.process_shadow_review import Hash
from . import agent_procurement_monitor_journal as journal
from . import db


class Identify(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    confirmed: StrictBool


class WindowRequest(Identify):
    window_id: Alias
    expected_report_sha256: Hash


class ReviewRow(Strict):
    slot_id: Hash
    case_token_sha256: Hash
    business_outcome: Literal["success","failure"]
    critical_incident: StrictBool
    forbidden_action: StrictBool
    source_reference_sha256: Hash


class Review(WindowRequest):
    reviewer_alias: Alias
    confirm_separate_source_review: StrictBool
    rows: list[ReviewRow]=Field(min_length=1,max_length=500)

    @model_validator(mode="after")
    def unique(self):
        for k in ("slot_id","case_token_sha256","source_reference_sha256"):
            if len({getattr(x,k) for x in self.rows})!=len(self.rows):
                raise ValueError("duplicate corroboration row")
        return self


def _read(conn,workflow_id,revision,req):
    key=journal._key(workflow_id,revision,req.monitor_id)
    journal._schema(conn)
    state,history=journal._read(conn,key)
    if state is None:raise journal.JournalError("Monitoring journal not found",404)
    journal._check_head(history,req.expected_head_sha256)
    journal._require_current(conn,workflow_id,revision)
    if not state.get("monitor_plan"):
        raise journal.JournalError("Retained verified monitoring plan required",409)
    return key,state,history


def _window(state,req):
    if not req.confirmed:raise ValueError("explicit corroboration consent required")
    if state["report"]["report_sha256"]!=req.expected_report_sha256:
        raise journal.JournalError("Monitoring report changed",409)
    w=next((x for x in state["monitor_plan"]["windows"] if x["window_id"]==req.window_id),None)
    if w is None:raise ValueError("unknown monitoring window")
    end=w["plan"]["request"]["window_end_exclusive_ms"]
    if min(state["report"]["as_of_ms"],int(time.time()*1000))<end:
        raise ValueError("corroboration requires closed retained window")
    return w


def require(workflow_id,revision,document):
    req=Identify.model_validate(document)
    if not req.confirmed:raise ValueError("explicit corroboration requirement confirmation needed")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");key,state,history=_read(conn,workflow_id,revision,req)
        if state.get("outcome_corroboration_required",False):
            return journal._view(key,state,history)
        state["outcome_corroboration_required"]=True
        return journal._save(conn,key,state,history,"corroboration_required",{"required":True})


def manifest(workflow_id,revision,document):
    req=WindowRequest.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN");_,state,history=_read(conn,workflow_id,revision,req)
        window=_window(state,req)
        reviewed={x["slot_id"] for b in state.get("outcome_corroborations",[]) for x in b["rows"]}
        slots=[{"slot_id":s["slot_id"],"case_token_sha256":s["case_token_sha256"]}
               for s in window["plan"]["slots"] if s["slot_id"] not in reviewed]
        result={"format":"openworkgraph.procurement-corroboration-manifest.v1",
            "monitor_id":req.monitor_id,"window_id":req.window_id,
            "expected_head_sha256":req.expected_head_sha256,"expected_report_sha256":req.expected_report_sha256,
            "slots":slots,"contains_producer_outcomes":False,"contains_arm_assignments":False,
            "execution_authorized":False}
        return {**result,"manifest_sha256":digest(result)}


def record(workflow_id,revision,document):
    req=Review.model_validate(document)
    if not req.confirm_separate_source_review:raise ValueError("separate source review confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");key,state,history=_read(conn,workflow_id,revision,req)
        window=_window(state,req)
        aliases={state["coordinator_alias"].casefold()}|{e["details"]["producer_alias"].casefold()
                    for e in history if e["kind"]=="producer_grant"}
        if req.reviewer_alias.casefold() in aliases:
            raise ValueError("distinct declared reviewer alias required")
        slots={s["slot_id"]:s["case_token_sha256"] for s in window["plan"]["slots"]}
        if any(slots.get(x.slot_id)!=x.case_token_sha256 for x in req.rows):
            raise ValueError("review outside frozen window")
        prior=state.get("outcome_corroborations",[])
        seen={x["slot_id"] for b in prior for x in b["rows"]}
        if any(x.slot_id in seen for x in req.rows):
            raise ValueError("already corroborated slot cannot be rewritten")
        refs={x.source_reference_sha256 for x in req.rows}
        # Check all retained journals, including source observations and initial
        # pilot evidence. Distinct opaque references are not proof of independence.
        for other in conn.execute("SELECT workflow_id,revision,monitor_id FROM procurement_monitor_journals"):
            other_key=tuple(other)
            other_state=state if other_key==key else journal._read(conn,other_key)[0]
            existing=set(other_state.get("initial_sources",[]))
            existing.update(o["source_evidence_sha256"] for w in other_state["snapshot"]["windows"] for o in w["observations"])
            existing.update(x["source_reference_sha256"] for b in other_state.get("outcome_corroborations",[]) for x in b["rows"])
            if refs & existing:raise ValueError("corroboration source reference reused")
        result={"window_id":req.window_id,"reviewer_alias":req.reviewer_alias,
            "report_sha256":req.expected_report_sha256,"snapshot_sha256":state["report"]["snapshot_sha256"],
            "rows":sorted((x.model_dump() for x in req.rows),key=lambda x:x["slot_id"]),
            "reviewer_identity_verified":False,"source_system_verified":False}
        result["corroboration_sha256"]=digest(result)
        state["outcome_corroborations"]=[*prior,result]
        return journal._save(conn,key,state,history,"outcome_corroboration",{
            "window_id":req.window_id,"reviewer_alias":req.reviewer_alias,
            "reviewed_cases":len(req.rows),"corroboration_sha256":result["corroboration_sha256"]})
