"""Local signal review inbox. Acknowledgement never grants action authority.

The dashboard explicitly confirms the exact evaluated state fingerprint.
Reviews are private to this computer/log and deleted with their source log.
No scheduling, external recipients, source writes or AI-visible permissions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from . import event_log_workspace as workspace

_RULE_ID = re.compile(r"sig_[0-9a-f]{16}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


def _hash(log_id: str, rule_id: str, state: str) -> str:
    return hashlib.sha256(json.dumps(
        [log_id, rule_id, json.loads(state)],
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _row(conn, log_id: str, rule_id: str):
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise workspace.EventLogWorkspaceError("unknown signal rule")
    result = conn.execute(
        """SELECT definition,last_evaluated_at,last_state
           FROM signal_rules WHERE log_id=? AND rule_id=?""",
        [log_id, rule_id]).fetchone()
    if result is None:
        raise workspace.EventLogWorkspaceError("unknown signal rule")
    return result


def _state(log_id, rule_id, name, last_at, state, acknowledged=None):
    if state is None or last_at is None:
        return {
            "rule_id": rule_id, "name": name, "status": "not_evaluated",
            "triggered": None, "evaluated_at": None, "state_fingerprint": None,
            "acknowledged_at": None, "requires_review": False,
        }
    snapshot = json.loads(state)
    if not isinstance(snapshot, dict) or type(snapshot.get("triggered")) is not bool:
        raise workspace.EventLogWorkspaceError("signal evaluation state is unavailable")
    digest = _hash(log_id, rule_id, state)
    triggered = snapshot["triggered"]
    accepted = triggered and acknowledged is not None and acknowledged[0] == digest
    return {
        "rule_id": rule_id, "name": name,
        "status": "acknowledged" if accepted else ("unreviewed" if triggered else "cleared"),
        "triggered": triggered,
        "evaluated_at": last_at.isoformat() + "Z",
        "state_fingerprint": digest,
        "acknowledged_at": acknowledged[1].isoformat() + "Z" if accepted else None,
        "requires_review": triggered and not accepted,
    }


def list_inbox(log_id: str) -> dict:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            records = conn.execute("""SELECT s.rule_id,s.definition,s.last_evaluated_at,s.last_state,
                r.state_fingerprint,r.acknowledged_at
                FROM signal_rules s LEFT JOIN signal_reviews r
                ON s.log_id=r.log_id AND s.rule_id=r.rule_id
                WHERE s.log_id=? ORDER BY s.created_at,s.rule_id""", [log_id]).fetchall()
            items = []
            for rule_id, definition, when, state, fingerprint, acknowledged_at in records:
                raw = json.loads(definition)
                items.append(_state(log_id, rule_id, raw["name"], when, state,
                                    (fingerprint, acknowledged_at) if fingerprint else None))
        finally:
            conn.close()
    return {
        "items": items,
        "unreviewed": sum(x["requires_review"] for x in items),
        "delivery_enabled": False, "scheduled_checks_enabled": False,
        "actions_authorized": False,
        "interpretation": ("Acknowledgement records only a local human review of a specific "
                           "evaluated signal state; it neither verifies an outcome nor "
                           "approves an agent or source-system operation."),
    }


def review(log_id: str, rule_id: str, body: dict, *, acknowledge: bool) -> dict:
    if (not isinstance(body, dict) or set(body) != {"state_fingerprint"} or
        not isinstance(body.get("state_fingerprint"), str) or
        not _FINGERPRINT.fullmatch(body["state_fingerprint"])):
        raise workspace.EventLogWorkspaceError("exact current state_fingerprint required")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute("BEGIN TRANSACTION")
            try:
                definition, when, state = _row(conn, log_id, rule_id)
                raw = json.loads(definition)
                current = _state(log_id, rule_id, raw["name"], when, state)
                if (current["state_fingerprint"] != body["state_fingerprint"] or
                        not current["triggered"]):
                    raise workspace.EventLogWorkspaceError(
                        "signal is stale or not triggered; evaluate and review again")
                conn.execute("DELETE FROM signal_reviews WHERE log_id=? AND rule_id=?",
                             [log_id, rule_id])
                if acknowledge:
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    conn.execute("""INSERT INTO signal_reviews
                        (log_id,rule_id,state_fingerprint,acknowledged_at)
                        VALUES (?,?,?,?)""", [log_id, rule_id, current["state_fingerprint"], now])
                conn.execute("COMMIT")
                return _state(log_id, rule_id, raw["name"], when, state,
                    (current["state_fingerprint"], now) if acknowledge else None)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


__all__ = ["list_inbox", "review"]
