"""Saved reference nets: target process models kept with a log for repeated conformance checks.

Conformance tools keep the target model next to the data (Celonis's target process, ProM's model
collections) so that every later check, alert or comparison uses the same reference. A reference
net here is a PNML Petri net saved under a name with one log, at most ``MAX_NETS`` per log. It is
checked when saved (safe XML, a reachable final marking), listed without its contents, returned for
replay, alignments or a signal, and deleted with the log.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_reference_nets.v1"
MAX_NETS = 20
MAX_CHARS = 2 * 1024 * 1024


def save(log_id: str, name: Any, pnml: Any) -> dict[str, Any]:
    from . import event_log_alignments as alignments
    name = str(name or "").strip()
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
        raise EventLogWorkspaceError("name the net in 1 to 120 characters")
    if not isinstance(pnml, str) or not pnml or len(pnml) > MAX_CHARS:
        raise EventLogWorkspaceError("the net must be PNML text of at most 2 MB")
    net = alignments.reference_net(pnml.encode())
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            count = conn.execute("SELECT count(*) FROM reference_nets WHERE log_id = ?", [log_id]).fetchone()[0]
            if count >= MAX_NETS:
                raise EventLogWorkspaceError(f"a log keeps at most {MAX_NETS} reference nets; delete one first")
            if conn.execute("SELECT 1 FROM reference_nets WHERE log_id = ? AND name = ?", [log_id, name]).fetchone():
                raise EventLogWorkspaceError("a reference net with that name already exists")
            net_id = "net_" + secrets.token_hex(8)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute("INSERT INTO reference_nets VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [log_id, net_id, name, now, pnml, len(net["places"]), len(net["transitions"])])
        finally:
            conn.close()
    return {"net_id": net_id, "name": name, "places": len(net["places"]), "transitions": len(net["transitions"])}


def listing(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("""SELECT net_id, name, created_at, places, transitions FROM reference_nets
                WHERE log_id = ? ORDER BY created_at, net_id""", [log_id]).fetchall()
        finally:
            conn.close()
    return {"format": FORMAT, "log_id": log_id, "max_nets": MAX_NETS,
            "nets": [{"net_id": i, "name": n, "created_at": c.isoformat() + "Z", "places": p, "transitions": t}
                     for i, n, c, p, t in rows]}


def get(log_id: str, net_id: str) -> tuple[str, str]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            row = conn.execute("SELECT name, pnml FROM reference_nets WHERE log_id = ? AND net_id = ?",
                               [log_id, str(net_id)]).fetchone()
        finally:
            conn.close()
    if row is None:
        raise EventLogWorkspaceError("no reference net with that id")
    return row[0], row[1]


def delete(log_id: str, net_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            found = conn.execute("SELECT count(*) FROM reference_nets WHERE log_id = ? AND net_id = ?",
                                 [log_id, str(net_id)]).fetchone()[0]
            if not found:
                raise EventLogWorkspaceError("no reference net with that id")
            conn.execute("DELETE FROM reference_nets WHERE log_id = ? AND net_id = ?", [log_id, str(net_id)])
        finally:
            conn.close()
    return {"deleted": net_id}


__all__ = ["save", "listing", "get", "delete", "FORMAT", "MAX_NETS"]
