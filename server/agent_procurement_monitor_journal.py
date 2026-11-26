"""Durable local operator monitoring checkpoint journal.

Uses the existing local-owner SQLite boundary. Retains only normalized
declared snapshots, minimized reports and chained audit entries, not upstream
case inputs or provider credentials. No network, polling or deployment.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_router_monitor import _normalize, _review_verified, review
from shared.process_shadow_review import Hash
from . import db

MAX_MONITORS=20
MAX_ENTRIES=128
MAX_STATE_BYTES=8*1024*1024
FORMAT="openworkgraph.procurement-monitor-journal.v1"


class JournalError(ValueError):
    def __init__(self,message,status=422):
        super().__init__(message)
        self.status=status


class Append(Strict):
    expected_head_sha256: Hash | None
    confirmed: StrictBool


class Identify(Strict):
    monitor_id: Alias


class Decision(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    disposition: Literal["refer_for_continued_monitoring_review","refer_for_rollback_review","hold"]
    reviewer_alias: Alias
    confirmed: StrictBool


class Refresh(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    confirmed: StrictBool


class Forget(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    confirmed: StrictBool


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_monitor_journals (
        workflow_id TEXT NOT NULL, revision INTEGER NOT NULL, monitor_id TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL, head_sha256 TEXT NOT NULL, state_json TEXT NOT NULL,
        PRIMARY KEY(workflow_id,revision,monitor_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_monitor_journal_entries (
        workflow_id TEXT NOT NULL, revision INTEGER NOT NULL, monitor_id TEXT NOT NULL,
        sequence INTEGER NOT NULL, entry_sha256 TEXT NOT NULL, entry_json TEXT NOT NULL,
        PRIMARY KEY(workflow_id,revision,monitor_id,sequence))""")


def _key(workflow_id,revision,monitor_id):
    return (workflow_id,revision,monitor_id)


def _read(conn,key):
    row=conn.execute("""SELECT plan_sha256,head_sha256,state_json FROM procurement_monitor_journals
        WHERE workflow_id=? AND revision=? AND monitor_id=?""",key).fetchone()
    if row is None:return None,[]
    entries=conn.execute("""SELECT sequence,entry_sha256,entry_json FROM procurement_monitor_journal_entries
        WHERE workflow_id=? AND revision=? AND monitor_id=? ORDER BY sequence""",key).fetchall()
    try:
        state=json.loads(row[2]);history=[];prev=None
        if not entries or len(entries)>MAX_ENTRIES:
            raise ValueError("invalid retained history")
        for i,e in enumerate(entries,1):
            value=json.loads(e[2])
            if e[0]!=i or value["sequence"]!=i or value["previous_entry_sha256"]!=prev or digest(value)!=e[1]:
                raise ValueError("invalid retained chain")
            if value["scope"]!=list(key) or value["plan_sha256"]!=row[0]:
                raise ValueError("wrong retained scope")
            prev=e[1];history.append({**value,"entry_sha256":prev})
        if prev!=row[1] or history[-1]["state_sha256"]!=digest(state) or state["plan_sha256"]!=row[0]:
            raise ValueError("invalid retained head")
        return state,history
    except (ValueError,TypeError,KeyError,RecursionError):
        raise JournalError("Retained monitoring journal failed integrity checks",409) from None


def _view(key,state,history):
    return {"format":FORMAT,"workflow_id":key[0],"revision":key[1],"monitor_id":key[2],
        "plan_sha256":state["plan_sha256"],"head_sha256":history[-1]["entry_sha256"],
        "checkpoint_count":sum(e["kind"] in ("checkpoint","producer_checkpoint","clock_checkpoint","scheduled_checkpoint") for e in history),"entry_count":len(history),
        "capacity":MAX_ENTRIES,"reserved_disable_entries":_reserved_entries(state,history),
        "ordinary_entries_remaining":max(0,MAX_ENTRIES-len(history)-_reserved_entries(state,history)),
        "latest_report":state["report"],
        "sticky_rollback_triggers":state["sticky_rollback_triggers"],
        "rollback_referral_recorded":state["rollback_referral_recorded"],
        "overdue_window_history":state["overdue_window_history"],"history":history,
        "eligible_for_continued_monitoring_review":bool(state["report"]["eligible_for_continued_monitoring_review"]
            and not state["sticky_rollback_triggers"] and not state["rollback_referral_recorded"]),
        "result_persisted":True,"local_dashboard_session_required":True,
        "source_evidence_authenticated":False,
        "credential_authenticated_observations":len(state.get("producer_attribution",{})),
        "producer_attribution":state.get("producer_attribution",{}),
        "finance_reconciliations":state.get("finance_reconciliations",[]),
        "scheduled_deadline_review_enabled":state.get("scheduled_deadline_review_enabled",False),
        "producer_grants":[{**e["details"],"revoked":any(x["kind"]=="producer_revoke" and x["details"]["grant_id"]==e["details"]["grant_id"] for x in history)} for e in history if e["kind"]=="producer_grant"],"tamper_resistant_against_database_owner":False,
        "deployment_changed":False,"execution_authorized":False}


def _reserved_entries(state,history,kind=None,details=None):
    """Count outstanding disable actions from the committed audit chain.

    Expiration alone does not release a reservation: the operator must still
    be able to explicitly revoke an expired grant. A proposed grant consumes
    both an entry now and a reservation; revocation releases exactly one.
    """
    active=set()
    for entry in history:
        if entry["kind"]=="producer_grant":
            active.add(entry["details"]["grant_id"])
        elif entry["kind"]=="producer_revoke":
            active.discard(entry["details"]["grant_id"])
    if kind=="producer_grant":
        active.add(details["grant_id"])
    elif kind=="producer_revoke":
        active.discard(details["grant_id"])
    return len(active)+int(state.get("scheduled_deadline_review_enabled",False))


def _save(conn,key,state,history,kind,details):
    if len(history)>=MAX_ENTRIES:
        raise JournalError("Monitoring journal capacity reached; export and review before creating a new plan",409)
    # Reserve capacity for every outstanding producer revocation and for
    # scheduled consent withdrawal, including the effect of this proposed entry.
    reserved=_reserved_entries(state,history,kind,details)
    if len(history)+1+reserved>MAX_ENTRIES:
        raise JournalError("Monitoring journal capacity reserved for credential revocation and consent withdrawal",409)
    encoded=_json(state)
    if len(encoded.encode("ascii"))>MAX_STATE_BYTES:
        raise JournalError("Monitoring journal state exceeds capacity",413)
    previous=history[-1]["entry_sha256"] if history else None
    entry={"scope":list(key),"plan_sha256":state["plan_sha256"],"sequence":len(history)+1,
        "previous_entry_sha256":previous,"kind":kind,"recorded_at_ms":int(time.time()*1000),
        "state_sha256":digest(state),"details":details}
    head=digest(entry)
    conn.execute("""INSERT INTO procurement_monitor_journal_entries
        (workflow_id,revision,monitor_id,sequence,entry_sha256,entry_json) VALUES (?,?,?,?,?,?)""",
        (*key,entry["sequence"],head,_json(entry)))
    if history:
        changed=conn.execute("""UPDATE procurement_monitor_journals SET head_sha256=?,state_json=?
            WHERE workflow_id=? AND revision=? AND monitor_id=? AND head_sha256=?""",
            (head,encoded,*key,previous)).rowcount
        if changed!=1:raise JournalError("Monitoring journal head changed",409)
    else:
        conn.execute("""INSERT INTO procurement_monitor_journals
            (workflow_id,revision,monitor_id,plan_sha256,head_sha256,state_json) VALUES (?,?,?,?,?,?)""",
            (*key,state["plan_sha256"],head,encoded))
    return _view(key,state,[*history,{**entry,"entry_sha256":head}])


def _head(history):
    return history[-1]["entry_sha256"] if history else None


def _check_head(history,expected):
    if _head(history)!=expected:
        raise JournalError("Monitoring journal head changed; reload history before reviewing",409)


def _unique(values):
    return [v for _,v in sorted({digest(v):v for v in values}.items())]


def _require_current(conn,workflow_id,revision):
    # Same SQLite write transaction as journal mutation: no TOCTOU gap or
    # nested catalog connection acquiring a schema/write lock.
    current=conn.execute("SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                         ("local",workflow_id)).fetchone()[0]
    if current!=revision:raise JournalError("Open latest saved process revision before recording monitoring evidence",409)


def _next_state(state,plan,snapshot,report,initial_sources):
    sticky=_unique([*(state["sticky_rollback_triggers"] if state else []),
        *report["rollback_review_triggers"],*report["rollback_triggers_at_previous_checkpoint"]])
    overdue=sorted(set((state["overdue_window_history"] if state else [])+
        [w["window_id"] for w in report["windows"] if w["state"]=="overdue"]+
        report["overdue_windows_at_previous_checkpoint"]))
    return {"plan_sha256":report["monitor_plan_sha256"],"coordinator_alias":plan["request"]["coordinator_alias"],
        "snapshot":snapshot,"report":report,"sticky_rollback_triggers":sticky,"overdue_window_history":overdue,
        "rollback_referral_recorded":state["rollback_referral_recorded"] if state else False,
        "monitor_plan":plan,"initial_sources":sorted(initial_sources),
        "producer_attribution":state.get("producer_attribution",{}) if state else {},
        "finance_reconciliations":state.get("finance_reconciliations",[]) if state else [],
        "scheduled_deadline_review_enabled":state.get("scheduled_deadline_review_enabled",False) if state else False}


def append(packet,workflow_id,revision,doc):
    if not isinstance(doc,dict) or set(doc)!={"initial","monitor_plan","snapshot","request"}:
        raise JournalError("Exact monitoring checkpoint bundle required")
    req=Append.model_validate(doc["request"])
    if not req.confirmed:raise JournalError("Explicit local retention confirmation required",403)
    plan=doc["monitor_plan"];monitor_id=Identify.model_validate({"monitor_id":plan["request"]["monitor_id"]}).monitor_id
    key=_key(workflow_id,revision,monitor_id)
    snapshot=_normalize(doc["snapshot"])
    # Do expensive evidence verification outside the SQLite write lock.
    with db.connect() as conn:
        conn.execute("BEGIN");_schema(conn);state,history=_read(conn,key)
        _check_head(history,req.expected_head_sha256)
    if state is not None and state["plan_sha256"]!=plan["monitor_plan_sha256"]:
        raise JournalError("Monitor ID already binds another plan",409)
    prior=None if state is None else {"snapshot":state["snapshot"],"expected_snapshot_sha256":state["report"]["snapshot_sha256"]}
    report=review(packet,doc["initial"],plan,snapshot,prior)
    new_state=_next_state(state,plan,snapshot,report,{o["source_evidence_sha256"] for o in doc["initial"]["captures"]["observations"]})
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);current,history=_read(conn,key)
        _check_head(history,req.expected_head_sha256)
        _require_current(conn,workflow_id,revision)
        if current is None and conn.execute("SELECT COUNT(*) FROM procurement_monitor_journals").fetchone()[0]>=MAX_MONITORS:
            raise JournalError("Local monitoring journal capacity reached",409)
        return _save(conn,key,new_state,history,"checkpoint",{
            "snapshot_sha256":report["snapshot_sha256"],"report_sha256":report["report_sha256"],
            "as_of_ms":report["as_of_ms"],"recorded_cases":report["recorded_cases"],
            "recommended_review":report["recommended_review"],"rollback_triggers":report["rollback_review_triggers"],
            "overdue_windows":[w["window_id"] for w in report["windows"] if w["state"]=="overdue"]})


def refresh(workflow_id,revision,doc):
    """Advance the cutoff without new observations using the local server clock."""
    req=Refresh.model_validate(doc)
    if not req.confirmed:raise JournalError("Explicit checkpoint confirmation required",403)
    key=_key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);state,history=_read(conn,key)
        if state is None:raise JournalError("Monitoring journal not found",404)
        _check_head(history,req.expected_head_sha256);_require_current(conn,workflow_id,revision)
        if "monitor_plan" not in state:
            raise JournalError("Retain a new verified checkpoint before refreshing",409)
        now=int(time.time()*1000)
        if now<=state["snapshot"]["as_of_ms"]:
            raise JournalError("Server clock has not advanced past retained cutoff",409)
        return _advance_clock(conn,key,state,history,now)


def _advance_clock(conn,key,state,history,now,kind="clock_checkpoint",crossed_deadlines=None):
    plan=state["monitor_plan"];sources=set(state["initial_sources"])
    snapshot=_normalize({**state["snapshot"],"as_of_ms":now})
    report=_review_verified(plan,snapshot,sources,{"snapshot":state["snapshot"],
        "expected_snapshot_sha256":state["report"]["snapshot_sha256"]})
    updated=_next_state(state,plan,snapshot,report,sources)
    return _save(conn,key,updated,history,kind,{
        "snapshot_sha256":report["snapshot_sha256"],"report_sha256":report["report_sha256"],
        "as_of_ms":now,"recorded_cases":report["recorded_cases"],
        "crossed_deadlines":crossed_deadlines or [],
        "recommended_review":report["recommended_review"],"rollback_triggers":report["rollback_review_triggers"],
        "overdue_windows":[w["window_id"] for w in report["windows"] if w["state"]=="overdue"]})


def detail(workflow_id,revision,doc):
    req=Identify.model_validate(doc);key=_key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN");_schema(conn);state,history=_read(conn,key)
        if state is None:raise JournalError("Monitoring journal not found",404)
        return _view(key,state,history)


def decide(workflow_id,revision,doc):
    req=Decision.model_validate(doc)
    if not req.confirmed:raise JournalError("Explicit review confirmation required",403)
    key=_key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);state,history=_read(conn,key)
        if state is None:raise JournalError("Monitoring journal not found",404)
        _check_head(history,req.expected_head_sha256);_require_current(conn,workflow_id,revision)
        if state["coordinator_alias"].casefold()==req.reviewer_alias.casefold():
            raise JournalError("Distinct declared reviewer alias required")
        if req.disposition=="refer_for_continued_monitoring_review" and not _view(key,state,history)["eligible_for_continued_monitoring_review"]:
            raise JournalError("Retained evidence or prior rollback referral blocks continuation",409)
        if req.disposition=="refer_for_rollback_review":state["rollback_referral_recorded"]=True
        return _save(conn,key,state,history,"decision",{
            "disposition":req.disposition,"reviewer_alias":req.reviewer_alias,
            "report_sha256":state["report"]["report_sha256"],"snapshot_sha256":state["report"]["snapshot_sha256"],
            "rollback_target_configuration_sha256":state["report"]["rollback_target_configuration_sha256"],
            "reviewer_identity_verified":False,"deployment_changed":False})


def forget(workflow_id,revision,doc):
    req=Forget.model_validate(doc)
    if not req.confirmed:raise JournalError("Explicit journal removal confirmation required",403)
    key=_key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);state,history=_read(conn,key)
        if state is None:raise JournalError("Monitoring journal not found",404)
        _check_head(history,req.expected_head_sha256)
        from . import agent_procurement_monitor_telemetry as telemetry
        telemetry.forget_in_transaction(conn,key)
        conn.execute("DELETE FROM procurement_monitor_journal_entries WHERE workflow_id=? AND revision=? AND monitor_id=?",key)
        conn.execute("DELETE FROM procurement_monitor_journals WHERE workflow_id=? AND revision=? AND monitor_id=?",key)
    return {"format":FORMAT,"monitor_id":req.monitor_id,"removed":True,"result_persisted":False,
            "backup_erasure_verified":False,"deployment_changed":False,"execution_authorized":False}


def list_journals(workflow_id,revision,doc):
    if doc!={}:raise JournalError("Empty object required for journal list")
    with db.connect() as conn:
        conn.execute("BEGIN");_schema(conn)
        keys=conn.execute("SELECT monitor_id FROM procurement_monitor_journals WHERE workflow_id=? AND revision=? ORDER BY monitor_id",
                          (workflow_id,revision)).fetchall()
        rows=[]
        for k in keys:
            key=_key(workflow_id,revision,k[0]);state,history=_read(conn,key)
            view=_view(key,state,history)
            rows.append({x:view[x] for x in ("monitor_id","plan_sha256","head_sha256","entry_count","checkpoint_count",
                         "sticky_rollback_triggers","rollback_referral_recorded","eligible_for_continued_monitoring_review","scheduled_deadline_review_enabled")})
    return {"format":FORMAT,"journals":rows,"result_persisted":False,"execution_authorized":False}


def operation(packet,workflow_id,revision,action,doc):
    if action=="router-journal-list":return list_journals(workflow_id,revision,doc)
    if action=="router-journal-append":return append(packet,workflow_id,revision,doc)
    if action=="router-journal-refresh":return refresh(workflow_id,revision,doc)
    if action=="router-journal-detail":return detail(workflow_id,revision,doc)
    if action=="router-journal-decide":return decide(workflow_id,revision,doc)
    if action=="router-journal-forget":return forget(workflow_id,revision,doc)
    raise JournalError("Unknown monitoring journal operation")
