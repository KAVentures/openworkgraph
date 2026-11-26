"""Opt-in local clock reviews of already retained monitoring evidence.

Uses the existing local scheduler. No new source reads, producer submissions,
provider calls, notifications or deployment actions. Only crossed frozen
window boundaries can consume an audit entry; idle ticks are read-only.
"""
from __future__ import annotations

import time

from pydantic import StrictBool

from shared.agent_procurement import Alias, Strict
from shared.process_shadow_review import Hash
from . import agent_procurement_monitor_journal as journal
from . import db

KIND="procurement_monitor_deadlines"


class Consent(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    enabled: StrictBool
    confirmed: StrictBool


def configure(workflow_id,revision,document):
    req=Consent.model_validate(document)
    if not req.confirmed:
        raise journal.JournalError("Explicit scheduled review confirmation required",403)
    key=journal._key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");journal._schema(conn)
        state,history=journal._read(conn,key)
        if state is None:raise journal.JournalError("Monitoring journal not found",404)
        journal._check_head(history,req.expected_head_sha256)
        if req.enabled:
            journal._require_current(conn,workflow_id,revision)
            if "monitor_plan" not in state:
                raise journal.JournalError("Verified retained plan required",409)
        # Disabling an already opted-in historical journal remains possible.
        if state.get("scheduled_deadline_review_enabled",False)==req.enabled:
            return journal._view(key,state,history)
        state["scheduled_deadline_review_enabled"]=req.enabled
        return journal._save(conn,key,state,history,"schedule_consent",{"enabled":req.enabled})


def crossed_deadlines(plan,previous,now):
    if now<=previous:return []
    delay=plan["request"]["maximum_reporting_delay_ms"]
    result=[]
    for w in plan["windows"]:
        p=w["plan"]["request"]
        for phase,at in (("start",p["window_start_ms"]),("close",p["window_end_exclusive_ms"]),
                         ("reporting_deadline",p["window_end_exclusive_ms"]+delay)):
            if previous<at<=now:
                result.append({"window_id":w["window_id"],"phase":phase,"at_ms":at})
    return sorted(result,key=lambda x:(x["at_ms"],x["window_id"],x["phase"]))


def _tick_one(key,now):
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");journal._schema(conn)
        state,history=journal._read(conn,key)
        if state is None or not state.get("scheduled_deadline_review_enabled",False):
            return "not_enabled"
        current=conn.execute("SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                             ("local",key[0])).fetchone()[0]
        if current!=key[1]:return "historical"
        plan=state.get("monitor_plan")
        if not plan:raise journal.JournalError("Retained scheduled plan unavailable",409)
        crossed=crossed_deadlines(plan,state["snapshot"]["as_of_ms"],now)
        if not crossed:return "idle"
        # Consent is reread in the same write transaction as the update.
        # A concurrent producer or manual checkpoint advances the same cutoff.
        journal._advance_clock(conn,key,state,history,now,"scheduled_checkpoint",crossed)
        return "advanced"


def run_once(now=None):
    now=int(time.time()*1000) if now is None else now
    with db.connect() as conn:
        journal._schema(conn)
        keys=[tuple(r) for r in conn.execute(
            "SELECT workflow_id,revision,monitor_id FROM procurement_monitor_journals ORDER BY workflow_id,revision,monitor_id")]
    if len(keys)>journal.MAX_MONITORS:
        raise journal.JournalError("Local monitoring journal capacity exceeded",409)
    counts={"advanced":0,"idle":0,"not_enabled":0,"historical":0,"failed":0}
    for key in keys:
        try:counts[_tick_one(key,now)]+=1
        except Exception:
            # One unreadable/capacity-limited journal must not prevent others
            # from advancing. Never echo evidence or exceptions to the scheduler.
            counts["failed"]+=1
    return counts


def run_scheduled(target_id):
    if target_id!="local":return "failed:invalid_monitor_target",0.0
    counts=run_once()
    if counts["failed"]:
        return "failed:monitor_deadline_review_incomplete",0.0
    return "succeeded:monitor_deadline_review_"+str(counts["advanced"])+"_advanced",0.0
