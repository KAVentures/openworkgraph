"""Scoped, write-only local producer intake for retained monitor journals.

A bearer identifies an operator-provisioned producer, not a provider,
executed artifact, or independently verified business outcome. The producer
can only add declared observations for its assigned window and arm.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_monitor_protocol import Assignment, Batch, Receipt, GrantID, MAX_BATCH, TOKEN_RE
from shared.agent_procurement_router_monitor import _normalize, _review_verified
from shared.process_shadow_review import Hash
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_GRANTS=48
MAX_TTL_SECONDS=7*24*3600


class Grant(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    producer_alias: Alias
    window_id: Alias
    arm: Literal["incumbent","challenger"]
    ttl_seconds: Annotated[StrictInt,Field(ge=60,le=MAX_TTL_SECONDS)]
    confirmed: StrictBool


class Revoke(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    grant_id: GrantID
    confirmed: StrictBool


class ManifestRequest(Strict):
    monitor_id: Alias
    expected_head_sha256: Hash
    grant_id: GrantID
    confirmed: StrictBool



def _now():
    return int(time.time()*1000)


def _schema(conn):
    journal._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_monitor_producers (
        grant_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
        workflow_id TEXT NOT NULL, revision INTEGER NOT NULL, monitor_id TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL, producer_alias TEXT NOT NULL, window_id TEXT NOT NULL,
        arm TEXT NOT NULL, expires_ms INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)""")


def forget_in_transaction(conn,key):
    _schema(conn)
    conn.execute("DELETE FROM procurement_monitor_producers WHERE workflow_id=? AND revision=? AND monitor_id=?",key)


def _token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _credential(header):
    raw=str(header or "")
    if not raw.lower().startswith("bearer "):
        raise journal.JournalError("Active scoped monitoring producer credential required",401)
    token=raw[7:].strip()
    if not TOKEN_RE.fullmatch(token):
        raise journal.JournalError("Active scoped monitoring producer credential required",401)
    return token.split(".",1)[0][7:],_token_hash(token)


def _lookup(conn,credential,now):
    gid,hashed=credential
    row=conn.execute("""SELECT token_hash,workflow_id,revision,monitor_id,plan_sha256,producer_alias,
        window_id,arm,expires_ms,revoked FROM procurement_monitor_producers WHERE grant_id=?""",(gid,)).fetchone()
    if row is None or not hmac.compare_digest(hashed,row[0]) or row[9] or now>=row[8]:
        raise journal.JournalError("Active scoped monitoring producer credential required",401)
    return {"grant_id":gid,"key":tuple(row[1:4]),"plan_sha256":row[4],"producer_alias":row[5],
            "window_id":row[6],"arm":row[7],"expires_ms":row[8]}


def authenticate(header):
    credential=_credential(header)
    with db.connect() as conn:
        _schema(conn);_lookup(conn,credential,_now())
    return credential


def grant(workflow_id,revision,doc):
    req=Grant.model_validate(doc)
    if not req.confirmed:raise journal.JournalError("Explicit producer grant confirmation required",403)
    key=journal._key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        state,history=journal._read(conn,key)
        if state is None:raise journal.JournalError("Monitoring journal not found",404)
        journal._check_head(history,req.expected_head_sha256);journal._require_current(conn,workflow_id,revision)
        if "monitor_plan" not in state:
            raise journal.JournalError("Retain a new verified checkpoint before enabling producers",409)
        if req.producer_alias.casefold() in {b["reviewer_alias"].casefold() for b in state.get("outcome_corroborations",[])}:
            raise journal.JournalError("Producer alias already used for separate outcome corroboration",409)
        window=next((w for w in state["monitor_plan"]["windows"] if w["window_id"]==req.window_id),None)
        if window is None or not any(s["arm"]==req.arm for s in window["plan"]["slots"]):
            raise journal.JournalError("Invalid producer scope")
        if conn.execute("SELECT COUNT(*) FROM procurement_monitor_producers WHERE workflow_id=? AND revision=? AND monitor_id=?",key).fetchone()[0]>=MAX_GRANTS:
            raise journal.JournalError("Producer grant capacity reached",409)
        gid=secrets.token_hex(12);token="owgmon_"+gid+"."+secrets.token_urlsafe(48)
        expiry=_now()+req.ttl_seconds*1000
        details={"grant_id":gid,"producer_alias":req.producer_alias,"window_id":req.window_id,"arm":req.arm,"expires_ms":expiry}
        conn.execute("""INSERT INTO procurement_monitor_producers
            (grant_id,token_hash,workflow_id,revision,monitor_id,plan_sha256,producer_alias,window_id,arm,expires_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",(gid,_token_hash(token),*key,state["plan_sha256"],req.producer_alias,req.window_id,req.arm,expiry))
        result=journal._save(conn,key,state,history,"producer_grant",details)
        return {"grant":details,"producer_token":token,"head_sha256":result["head_sha256"],
                "token_shown_once":True,"result_persisted":True,"execution_authorized":False}


def revoke(workflow_id,revision,doc):
    req=Revoke.model_validate(doc)
    if not req.confirmed:raise journal.JournalError("Explicit revocation confirmation required",403)
    key=journal._key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        state,history=journal._read(conn,key)
        if state is None:raise journal.JournalError("Monitoring journal not found",404)
        journal._check_head(history,req.expected_head_sha256)
        changed=conn.execute("""UPDATE procurement_monitor_producers SET revoked=1
            WHERE grant_id=? AND workflow_id=? AND revision=? AND monitor_id=? AND revoked=0""",(req.grant_id,*key)).rowcount
        if changed!=1:raise journal.JournalError("Active producer grant not found",404)
        result=journal._save(conn,key,state,history,"producer_revoke",{"grant_id":req.grant_id})
        return {"grant_id":req.grant_id,"revoked":True,"head_sha256":result["head_sha256"],
                "result_persisted":True,"execution_authorized":False}


def manifest(workflow_id,revision,doc):
    """Explicit operator-only assignment export, not a producer read capability."""
    req=ManifestRequest.model_validate(doc)
    if not req.confirmed:raise journal.JournalError("Explicit assignment export confirmation required",403)
    key=journal._key(workflow_id,revision,req.monitor_id)
    with db.connect() as conn:
        conn.execute("BEGIN");_schema(conn)
        state,history=journal._read(conn,key)
        if state is None:raise journal.JournalError("Monitoring journal not found",404)
        journal._check_head(history,req.expected_head_sha256);journal._require_current(conn,workflow_id,revision)
        g=conn.execute("""SELECT plan_sha256,producer_alias,window_id,arm,expires_ms,revoked
            FROM procurement_monitor_producers WHERE grant_id=? AND workflow_id=? AND revision=? AND monitor_id=?""",
            (req.grant_id,*key)).fetchone()
        if g is None or g[5] or _now()>=g[4] or g[0]!=state["plan_sha256"]:
            raise journal.JournalError("Active scoped producer grant required",409)
        child=next(w["plan"] for w in state["monitor_plan"]["windows"] if w["window_id"]==g[2])
        recorded={o["slot_id"] for w in state["snapshot"]["windows"] for o in w["observations"]}
        slots=[{"slot_id":s["slot_id"],"case_token_sha256":s["case_token_sha256"],
                "expected_router_configuration_sha256":s["expected_router_configuration_sha256"],
                "already_recorded":s["slot_id"] in recorded}
               for s in child["slots"] if s["arm"]==g[3]]
        result={"format":"openworkgraph.procurement-monitor-producer-assignment.v1",
            "grant_id":req.grant_id,"producer_alias":g[1],"window_id":g[2],"arm":g[3],
            "expires_ms":g[4],"window_start_ms":child["request"]["window_start_ms"],
            "window_end_exclusive_ms":child["request"]["window_end_exclusive_ms"],
            "ingest_path":"/v1/process-catalog/procurement-producer/ingest",
            "batch_format":"openworkgraph.procurement-monitor-producer-batch.v1",
            "maximum_batch_observations":MAX_BATCH,"slots":slots,
            "execution_authorized":False,"credential_included":False}
        return Assignment.model_validate({**result,"assignment_sha256":digest(result)}).model_dump()


def _receipt(grant_id,batch_id,count,replay=False):
    # No read capability: do not disclose the journal head, other arm,
    # prior observations, policy, reports, or case list.
    return Receipt.model_validate({"format":"openworkgraph.procurement-monitor-producer-receipt.v1",
            "grant_id":grant_id,"batch_id":batch_id,"accepted_observations":count,
            "idempotent_replay":replay,"source_credential_authenticated":True,
            "business_outcomes_independently_verified":False,"execution_authorized":False}).model_dump()


def ingest(credential,doc):
    req=Batch.model_validate(doc)
    payload=sorted((o.model_dump() for o in req.observations),key=lambda o:o["slot_id"])
    payload_hash=digest(payload)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        now=_now();g=_lookup(conn,credential,now);key=g["key"]
        state,history=journal._read(conn,key)
        if state is None or state["plan_sha256"]!=g["plan_sha256"]:
            raise journal.JournalError("Producer scope unavailable",409)
        journal._require_current(conn,key[0],key[1])
        for e in history:
            d=e["details"]
            if e["kind"]=="producer_checkpoint" and d["grant_id"]==g["grant_id"] and d["batch_id"]==req.batch_id:
                if d["payload_sha256"]!=payload_hash:
                    raise journal.JournalError("Batch ID already used with different observations",409)
                return _receipt(g["grant_id"],req.batch_id,len(payload),True)
        plan=state["monitor_plan"]
        child=next(w["plan"] for w in plan["windows"] if w["window_id"]==g["window_id"])
        slots={s["slot_id"]:s for s in child["slots"] if s["arm"]==g["arm"]}
        start,end=child["request"]["window_start_ms"],child["request"]["window_end_exclusive_ms"]
        existing={o["slot_id"] for w in state["snapshot"]["windows"] for o in w["observations"]}
        for o in payload:
            if o["slot_id"] not in slots or o["case_token_sha256"]!=slots[o["slot_id"]]["case_token_sha256"]:
                raise journal.JournalError("Observation outside producer scope",403)
            if o["slot_id"] in existing:
                raise journal.JournalError("Observation already retained",409)
            if not start<=o["observed_at_ms"]<end or o["observed_at_ms"]>now:
                raise journal.JournalError("Observation outside permitted time window")
        if now<=state["snapshot"]["as_of_ms"]:
            raise journal.JournalError("Server clock has not advanced past retained cutoff",409)
        windows={w["window_id"]:list(w["observations"]) for w in state["snapshot"]["windows"]}
        windows.setdefault(g["window_id"],[]).extend(payload)
        snapshot=_normalize({**state["snapshot"],"as_of_ms":now,
            "windows":[{"window_id":k,"observations":v} for k,v in windows.items()]})
        report=_review_verified(plan,snapshot,set(state["initial_sources"]),
            {"snapshot":state["snapshot"],"expected_snapshot_sha256":state["report"]["snapshot_sha256"]})
        updated=journal._next_state(state,plan,snapshot,report,set(state["initial_sources"]))
        updated["producer_attribution"]={**updated["producer_attribution"],**{
            o["slot_id"]:{"grant_id":g["grant_id"],"producer_alias":g["producer_alias"],"batch_id":req.batch_id,
                          "observation_sha256":digest(o)} for o in payload}}
        journal._save(conn,key,updated,history,"producer_checkpoint",{
            "grant_id":g["grant_id"],"producer_alias":g["producer_alias"],"batch_id":req.batch_id,
            "payload_sha256":payload_hash,"accepted_observations":len(payload),
            "snapshot_sha256":report["snapshot_sha256"],"report_sha256":report["report_sha256"],
            "as_of_ms":now,"recorded_cases":report["recorded_cases"],
            "rollback_triggers":report["rollback_review_triggers"]})
        return _receipt(g["grant_id"],req.batch_id,len(payload))
