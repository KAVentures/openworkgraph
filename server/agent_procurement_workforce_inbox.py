"""Retained local inbox for separately arriving signed workforce packets.

No remote listener, background collection, implicit trust enrollment or
execution. Explicit dashboard admission and review; approved transport is
external. Retains minimized signed packets only.
"""
from __future__ import annotations

import json
import time
from typing import Annotated

from pydantic import Field, StrictInt, StrictBool

from shared.agent_procurement import Strict,digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_signing as signing
from . import agent_procurement_workforce_trust as trust
from . import agent_procurement_monitor_journal as journal
from . import db

MAX_PACKET_BYTES=4*1024*1024
MAX_TOTAL_BYTES=32*1024*1024
MAX_INCIDENTS=2000
Age=Annotated[StrictInt,Field(ge=1,le=366*24*3600000)]


class Scope(Strict):
    expected_revision: trust.Revision
    expected_inbox_revision: trust.Revision
    correlation_namespace_sha256: Hash
    maximum_packet_age_ms: Age
    confirmed: StrictBool


class Admit(Scope):
    signed_packet: signing.SignedPacket


class Forget(Strict):
    expected_revision: trust.Revision
    expected_inbox_revision: trust.Revision
    correlation_namespace_sha256: Hash
    confirmed: StrictBool


class Retire(Forget):
    expected_incident_history_sha256: Hash
    confirm_exported_history: StrictBool


def _schema(conn):
    trust._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_inbox_revision
        (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL)""")
    conn.execute("INSERT OR IGNORE INTO procurement_workforce_inbox_revision VALUES (1,0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_inbox (
        namespace TEXT NOT NULL,alias TEXT NOT NULL,public_key TEXT NOT NULL,
        exported_at_ms INTEGER NOT NULL,packet_sha256 TEXT NOT NULL,
        packet_json TEXT NOT NULL,admitted_at_ms INTEGER NOT NULL,
        PRIMARY KEY(namespace,alias))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS procurement_workforce_inbox_incidents (
        namespace TEXT NOT NULL,alias TEXT NOT NULL,journal_tag TEXT NOT NULL,
        first_seen_ms INTEGER NOT NULL,last_seen_ms INTEGER NOT NULL,
        first_packet_sha256 TEXT NOT NULL,last_packet_sha256 TEXT NOT NULL,
        first_exported_ms INTEGER NOT NULL,last_exported_ms INTEGER NOT NULL,
        PRIMARY KEY(namespace,alias,journal_tag))""")


def _revision(conn):
    return conn.execute("SELECT revision FROM procurement_workforce_inbox_revision WHERE singleton=1").fetchone()[0]


def _advance(conn):
    if _revision(conn)>=2**53-1:raise journal.JournalError("Inbox revision exhausted",409)
    conn.execute("UPDATE procurement_workforce_inbox_revision SET revision=revision+1 WHERE singleton=1")


def _check(conn,req):
    trust._check(conn,req.expected_revision)
    if _revision(conn)!=req.expected_inbox_revision:
        raise journal.JournalError("Workforce inbox changed; reload before action",409)
    if not req.confirmed:raise ValueError("explicit inbox confirmation required")


def _active(conn,ns):
    return {r["installation_alias"]:r for r in trust._entries(conn)
            if r["correlation_namespace_sha256"]==ns and r["active"]}


def _stored(conn,ns):
    return {r[0]:dict(zip(("alias","public_key","exported_at_ms","packet_sha256","packet_json","admitted_at_ms"),tuple(r)))
            for r in conn.execute("""SELECT alias,public_key,exported_at_ms,packet_sha256,packet_json,admitted_at_ms
            FROM procurement_workforce_inbox WHERE namespace=? ORDER BY alias""",(ns,))}


def _decode(row,ns,alias):
    packet=signing.SignedPacket.model_validate_json(row["packet_json"])
    if (packet.packet.installation_alias!=alias or packet.packet.correlation_namespace_sha256!=ns
            or packet.packet.exported_at_ms!=row["exported_at_ms"]
            or packet.expected_packet_sha256!=row["packet_sha256"]):
        raise ValueError("stored packet metadata mismatch")
    signing.verify_packet(row["public_key"],packet.packet.model_dump(),packet.expected_packet_sha256,packet.signature_hex)
    return packet


def _fresh(timestamp,now,age):
    return 0<=now-timestamp<=age


def _classify(row,member,now,age):
    if member is None:return "not_active"
    if row is None:return "missing"
    if row["public_key"]!=member["public_key_hex"]:return "key_changed"
    try:
        trust._acceptance(member["accepted_at_ms"],member["accepted_packet_sha256"],
                          row["exported_at_ms"],row["packet_sha256"])
    except ValueError:return "superseded"
    if not _fresh(row["exported_at_ms"],now,age):return "stale_or_future"
    return "available"


def _retain_incidents(conn,ns,alias,packet,now):
    """Preserve first and latest signed rollback flags; never infer clearance."""
    tags=sorted({r.journal_tag for r in packet.packet.rows if r.rollback_review})
    if not tags:return
    existing={r[0] for r in conn.execute(
        "SELECT journal_tag FROM procurement_workforce_inbox_incidents WHERE namespace=? AND alias=?",
        (ns,alias))}
    count=conn.execute("SELECT COUNT(*) FROM procurement_workforce_inbox_incidents").fetchone()[0]
    if count+len(set(tags)-existing)>MAX_INCIDENTS:
        raise journal.JournalError("Retained rollback signal capacity reached; export and explicitly review incident history",409)
    stamp=packet.packet.exported_at_ms;pin=packet.expected_packet_sha256
    for tag in tags:
        conn.execute("""INSERT INTO procurement_workforce_inbox_incidents VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(namespace,alias,journal_tag) DO UPDATE SET
            last_seen_ms=excluded.last_seen_ms,last_packet_sha256=excluded.last_packet_sha256,
            last_exported_ms=excluded.last_exported_ms
            WHERE excluded.last_exported_ms>procurement_workforce_inbox_incidents.last_exported_ms""",
            (ns,alias,tag,now,now,pin,pin,stamp,stamp))


def _incidents(conn,ns):
    fields=("installation_alias","journal_tag","first_seen_ms","last_seen_ms",
            "first_packet_sha256","last_packet_sha256","first_exported_ms","last_exported_ms")
    return [dict(zip(fields,tuple(r))) for r in conn.execute("""SELECT alias,journal_tag,first_seen_ms,last_seen_ms,
        first_packet_sha256,last_packet_sha256,first_exported_ms,last_exported_ms
        FROM procurement_workforce_inbox_incidents WHERE namespace=? ORDER BY alias,journal_tag""",(ns,))]


def _inventory(conn,ns,now,age):
    active=_active(conn,ns);stored=_stored(conn,ns);rows=[]
    for alias in sorted(set(active)|set(stored)):
        item=stored.get(alias);member=active.get(alias)
        status=_classify(item,member,now,age)
        if item is not None:
            try:_decode(item,ns,alias)
            except (ValueError,TypeError,KeyError,RecursionError):status="corrupt"
        rows.append({"installation_alias":alias,"active":member is not None,
            "status":status,"packet_sha256":item["packet_sha256"] if item else None,
            "exported_at_ms":item["exported_at_ms"] if item else None,
            "admitted_at_ms":item["admitted_at_ms"] if item else None})
    incidents=_incidents(conn,ns)
    return {"format":"openworkgraph.workforce-inbox.v1","registry_revision":trust._revision(conn),
        "inbox_revision":_revision(conn),"correlation_namespace_sha256":ns,"as_of_ms":now,
        "maximum_packet_age_ms":age,"active_installations":len(active),
        "ready_for_complete_review":bool(active) and all(r["status"]=="available" for r in rows if r["active"]),
        "entries":rows,"retained_rollback_signals":incidents,"incident_history_sha256":digest(incidents),
        "incident_history_retirement_available":not active and not stored and bool(incidents),"execution_authorized":False,"organization_identity_verified":False}


def status(document,*,now=None):
    # Status does not require CAS, but uses the same explicit namespace and age.
    class Status(Strict):
        correlation_namespace_sha256: Hash
        maximum_packet_age_ms: Age
    req=Status.model_validate(document)
    now=int(time.time()*1000) if now is None else now
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn)
        return _inventory(conn,req.correlation_namespace_sha256,now,req.maximum_packet_age_ms)


def admit(document,*,now=None):
    req=Admit.model_validate(document);ns=req.correlation_namespace_sha256
    packet=req.signed_packet;alias=packet.packet.installation_alias
    now=int(time.time()*1000) if now is None else now
    if packet.packet.correlation_namespace_sha256!=ns or not _fresh(packet.packet.exported_at_ms,now,req.maximum_packet_age_ms):
        raise ValueError("wrong namespace or stale/future packet")
    encoded=json.dumps(packet.model_dump(),sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)
    if len(encoded)>MAX_PACKET_BYTES:raise journal.JournalError("Packet exceeds inbox capacity",413)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req)
        member=_active(conn,ns).get(alias)
        if member is None:raise ValueError("active exact installation enrollment required")
        public=member["public_key_hex"]
        signing.verify_packet(public,packet.packet.model_dump(),packet.expected_packet_sha256,packet.signature_hex)
        stamp=packet.packet.exported_at_ms;pin=packet.expected_packet_sha256
        trust._acceptance(member["accepted_at_ms"],member["accepted_packet_sha256"],stamp,pin)
        old=_stored(conn,ns).get(alias)
        if old is not None:
            # Even rotation must not move the inbox timestamp backwards. An
            # exact packet under a new key is allowed, but must be reverified.
            action=trust._acceptance(old["exported_at_ms"],old["packet_sha256"],stamp,pin)
            if action=="repeat" and old["public_key"]==public:
                _decode(old,ns,alias)
                return {**_inventory(conn,ns,now,req.maximum_packet_age_ms),"idempotent_replay":True}
        size=conn.execute("SELECT COALESCE(SUM(length(packet_json)),0) FROM procurement_workforce_inbox").fetchone()[0]
        if size-(len(old["packet_json"]) if old else 0)+len(encoded)>MAX_TOTAL_BYTES:
            raise journal.JournalError("Total workforce inbox capacity reached",409)
        conn.execute("""INSERT INTO procurement_workforce_inbox
            (namespace,alias,public_key,exported_at_ms,packet_sha256,packet_json,admitted_at_ms)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(namespace,alias) DO UPDATE SET
            public_key=excluded.public_key,exported_at_ms=excluded.exported_at_ms,
            packet_sha256=excluded.packet_sha256,packet_json=excluded.packet_json,admitted_at_ms=excluded.admitted_at_ms""",
            (ns,alias,public,stamp,pin,encoded,now))
        _retain_incidents(conn,ns,alias,packet,now)
        _advance(conn)
        return {**_inventory(conn,ns,now,req.maximum_packet_age_ms),"idempotent_replay":False}


def review(document,*,now=None):
    req=Scope.model_validate(document);ns=req.correlation_namespace_sha256
    now=int(time.time()*1000) if now is None else now
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req)
        inventory=_inventory(conn,ns,now,req.maximum_packet_age_ms)
        if not inventory["ready_for_complete_review"]:
            raise journal.JournalError("Fresh verified packets required for every active installation",409)
        stored=_stored(conn,ns)
        packets=[_decode(stored[alias],ns,alias).model_dump() for alias in sorted(_active(conn,ns))]
        review_req=trust.Review.model_validate({"expected_revision":req.expected_revision,
            "expected_correlation_namespace_sha256":ns,"maximum_packet_age_ms":req.maximum_packet_age_ms,
            "packets":packets,"confirmed":True})
        result=trust._review_locked(conn,review_req,now=now)
        result.pop("report_sha256")
        result["format"]="openworkgraph.workforce-inbox-review.v1"
        result["inbox_revision"]=_revision(conn)
        result["retained_rollback_signals"]=_incidents(conn,ns)
        result["retained_rollback_signals_are_review_referrals_only"]=True
        result["limitations"].append("Review uses the latest explicitly admitted signed snapshot for each active site. No remote transport, live presence, or proof of organizational completeness.")
        return {**result,"report_sha256":digest(result)}


def forget(document):
    req=Forget.model_validate(document)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req)
        count=conn.execute("DELETE FROM procurement_workforce_inbox WHERE namespace=?",
                           (req.correlation_namespace_sha256,)).rowcount
        # Explicit removal of packet contents preserves the minimized incident ledger.
        if count:_advance(conn)
        return {"removed_packets":count,"retained_rollback_signals_preserved":True,"inbox_revision":_revision(conn),
            "registry_revision":trust._revision(conn),"accepted_registry_watermarks_preserved":True,
            "physical_erasure_verified":False,"execution_authorized":False}


def retire(document):
    req=Retire.model_validate(document);ns=req.correlation_namespace_sha256
    if not req.confirm_exported_history:raise ValueError("explicit archived-history confirmation required")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE");_schema(conn);_check(conn,req)
        if _active(conn,ns) or _stored(conn,ns):
            raise journal.JournalError("Revoke every active alias and remove retained packet contents before retiring incident history",409)
        incidents=_incidents(conn,ns)
        if digest(incidents)!=req.expected_incident_history_sha256:
            raise journal.JournalError("Incident history pin changed",409)
        count=conn.execute("DELETE FROM procurement_workforce_inbox_incidents WHERE namespace=?",(ns,)).rowcount
        if count:_advance(conn)
        return {"removed_rollback_signals":count,"inbox_revision":_revision(conn),
            "registry_revision":trust._revision(conn),"archived_history_sha256":req.expected_incident_history_sha256,
            "archive_independently_verified":False,"accepted_registry_watermarks_preserved":True,
            "physical_erasure_verified":False,"execution_authorized":False}
