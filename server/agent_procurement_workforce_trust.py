"""Local operator-pinned installation trust and monotonic acceptance.

Only public keys and latest accepted packet pins are retained, not packet
contents. This is a local review registry, not live federation or SSO.
All state transitions are serialized with a registry revision CAS.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from shared.agent_procurement import Strict, Alias, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_signing as signing
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_ENTRIES=100
Revision=Annotated[StrictInt,Field(ge=0,le=2**53-1)]


class Configure(Strict):
    expected_revision: Revision
    installation_alias: Alias
    correlation_namespace_sha256: Hash
    action: Literal["enroll","rotate","revoke"]
    public_key_hex: Hash | None
    confirmed: StrictBool


class Review(Strict):
    expected_revision: Revision
    expected_correlation_namespace_sha256: Hash
    maximum_packet_age_ms: Annotated[StrictInt,Field(ge=1,le=366*24*3600000)]
    packets: list[signing.SignedPacket]=Field(min_length=1,max_length=signing.exchange.MAX_SITES)
    confirmed: StrictBool


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_trust_revision (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)""")
    conn.execute("INSERT OR IGNORE INTO procurement_workforce_trust_revision VALUES (1,0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_trust (
        namespace TEXT NOT NULL, alias_fold TEXT NOT NULL, alias TEXT NOT NULL,
        public_key TEXT NOT NULL, active INTEGER NOT NULL,
        accepted_at_ms INTEGER, accepted_packet_sha256 TEXT,
        PRIMARY KEY(namespace,alias_fold), UNIQUE(namespace,public_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_retired_keys (
        namespace TEXT NOT NULL, public_key TEXT NOT NULL,
        PRIMARY KEY(namespace,public_key))""")


def _revision(conn):
    return conn.execute("SELECT revision FROM procurement_workforce_trust_revision WHERE singleton=1").fetchone()[0]


def _check(conn,expected):
    if _revision(conn)!=expected:raise journal.JournalError("Trust registry changed; reload before review",409)


def _advance(conn):
    if _revision(conn)>=2**53-1:raise journal.JournalError("Trust registry revision exhausted",409)
    conn.execute("UPDATE procurement_workforce_trust_revision SET revision=revision+1 WHERE singleton=1")


def _entries(conn):
    return [dict(zip(("correlation_namespace_sha256","alias_fold","installation_alias","public_key_hex","active",
                      "accepted_at_ms","accepted_packet_sha256"),tuple(r))) for r in conn.execute(
        """SELECT namespace,alias_fold,alias,public_key,active,accepted_at_ms,accepted_packet_sha256
           FROM procurement_workforce_trust ORDER BY namespace,alias_fold""")]


def _view(conn):
    rows=_entries(conn)
    for row in rows:
        row.pop("alias_fold")
        row["active"]=bool(row["active"])
    return {"format":"openworkgraph.workforce-trust-registry.v1","revision":_revision(conn),
        "entries":rows,"local_operator_registry":True,"organization_identity_verified":False,
        "execution_authorized":False}


def status(document):
    if document!={}:raise ValueError("empty trust status document required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        return _view(conn)


def configure(document):
    req=Configure.model_validate(document)
    if not req.confirmed:raise ValueError("explicit trust change confirmation required")
    ns=req.correlation_namespace_sha256;alias=req.installation_alias;fold=alias.casefold()
    key=req.public_key_hex
    if (req.action=="revoke")!=(key is None):
        raise ValueError("revocation requires null key; enrollment/rotation require public key")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req.expected_revision)
        old=next((x for x in _entries(conn) if x["correlation_namespace_sha256"]==ns and x["alias_fold"]==fold),None)
        if old is not None and old["installation_alias"]!=alias:
            raise ValueError("exact enrolled alias required")
        if req.action=="revoke":
            if old is None or not old["active"]:raise ValueError("active enrollment required")
            conn.execute("UPDATE procurement_workforce_trust SET active=0 WHERE namespace=? AND alias_fold=?",(ns,fold))
        else:
            if req.action=="enroll" and old is not None:
                raise ValueError("existing or revoked alias requires rotation")
            if req.action=="rotate" and old is None:
                raise ValueError("existing enrollment required for rotation")
            # Refuse known retired keys, and keys already assigned elsewhere
            # within the namespace. Never silently resurrect a revoked key.
            if conn.execute("SELECT 1 FROM procurement_workforce_retired_keys WHERE namespace=? AND public_key=?",(ns,key)).fetchone():
                raise ValueError("retired signing key cannot be enrolled again")
            if conn.execute("SELECT 1 FROM procurement_workforce_trust WHERE namespace=? AND public_key=?",(ns,key)).fetchone():
                raise ValueError("signing key already enrolled")
            if old is None:
                if len(_entries(conn))>=MAX_ENTRIES:raise journal.JournalError("Trust registry capacity reached",409)
                conn.execute("INSERT INTO procurement_workforce_trust VALUES (?,?,?,?,1,NULL,NULL)",(ns,fold,alias,key))
            else:
                if conn.execute("SELECT COUNT(*) FROM procurement_workforce_retired_keys").fetchone()[0]>=MAX_ENTRIES*10:
                    raise journal.JournalError("Retired-key capacity reached",409)
                conn.execute("INSERT INTO procurement_workforce_retired_keys VALUES (?,?)",(ns,old["public_key_hex"]))
                conn.execute("UPDATE procurement_workforce_trust SET public_key=?,active=1 WHERE namespace=? AND alias_fold=?",(key,ns,fold))
        _advance(conn)
        return _view(conn)


def _acceptance(previous_time,previous_pin,new_time,new_pin):
    if previous_time is None or new_time>previous_time:return "advance"
    if new_time==previous_time and new_pin==previous_pin:return "repeat"
    raise ValueError("older or conflicting installation snapshot")


def review(document,*,now=None):
    req=Review.model_validate(document)
    if not req.confirmed:raise ValueError("explicit retained review confirmation required")
    ns=req.expected_correlation_namespace_sha256
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req.expected_revision)
        active={x["installation_alias"]:x for x in _entries(conn)
                if x["correlation_namespace_sha256"]==ns and x["active"]}
        if not active or {p.packet.installation_alias for p in req.packets}!=set(active):
            raise ValueError("provide exactly one fresh packet for every active installation in the namespace")
        result=signing.signed_review({"expected_correlation_namespace_sha256":ns,
            "maximum_packet_age_ms":req.maximum_packet_age_ms,"confirmed":True,
            "trust":[{"installation_alias":a,"public_key_hex":v["public_key_hex"],
                      "correlation_namespace_sha256":ns} for a,v in sorted(active.items())],
            "packets":[p.model_dump() for p in req.packets]},now=now)
        advancing=[]
        for p in req.packets:
            old=active[p.packet.installation_alias]
            action=_acceptance(old["accepted_at_ms"],old["accepted_packet_sha256"],
                               p.packet.exported_at_ms,p.expected_packet_sha256)
            if action=="advance":advancing.append(p)
        for p in advancing:
            conn.execute("""UPDATE procurement_workforce_trust SET accepted_at_ms=?,accepted_packet_sha256=?
                            WHERE namespace=? AND alias_fold=?""",
                (p.packet.exported_at_ms,p.expected_packet_sha256,ns,p.packet.installation_alias.casefold()))
        if advancing:_advance(conn)
        result.pop("report_sha256")
        result["format"]="openworkgraph.workforce-registered-exchange-review.v1"
        result["registry_revision"]=_revision(conn)
        result["accepted_new_packets"]=len(advancing)
        result["exact_repeated_packets"]=len(req.packets)-len(advancing)
        result["persistent_replay_history_verified"]=False
        result["local_monotonic_packet_pins_checked"]=True
        result["result_persisted"]=bool(advancing)
        result["limitations"].append("Local registry retains only latest accepted timestamps and packet pins. Revoked keys cannot contribute to registered review. This does not prevent database rollback, a signer fabricating later timestamps, or replay in a different registry.")
        return {**result,"report_sha256":digest(result)}
