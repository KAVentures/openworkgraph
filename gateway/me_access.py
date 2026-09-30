from __future__ import annotations

"""Employee self-service: "what does my organization hold about me?".

An employee reaches /me in one of two ways:

* from their own OpenWorkGraph dashboard: the enrolled computer asks the
  Gateway for a one-time login code with its device credential (valid for two
  minutes, single use), then opens ``/me#code=...`` in the browser;
* with company SSO, when configured: the verified email must belong to an
  active roster employee.

A session only ever reads data for that employee's own ``actor_id``.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.time_utils import normalize_timestamp
from .auth import token_hash
from .db import GatewayDB

CODE_SECONDS = 120
SESSION_HOURS = 8
SESSION_IDLE_MINUTES = 30
CODE_PREFIX = "owg_me_code_"
SESSION_PREFIX = "owg_me_session_"

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_me_codes (
  code_hash TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS gateway_me_sessions (
  session_hash TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  auth_method TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
)
"""


class MeError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: datetime) -> str:
    return normalize_timestamp(value.isoformat())


def _one(db: GatewayDB, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    cur = db._execute(conn, sql, params)
    row = cur.fetchone()
    columns = [d[0] for d in cur.description] if cur.description else None
    return db._row(row, columns)


def _all(db: GatewayDB, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = db._execute(conn, sql, params)
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description] if cur.description else None
    return [db._row(r, columns) for r in rows]


def init_me_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            conn.execute(statement)


def create_login_code(db: GatewayDB, *, organization_id: str, actor_id: str, device_id: str) -> str:
    if not actor_id:
        raise MeError("this computer has no work identity on the Gateway; ask your IT admin to link it", status_code=409)
    code = CODE_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    with db.connect() as conn:
        db._execute(
            conn,
            "INSERT INTO gateway_me_codes(code_hash, organization_id, actor_id, device_id, created_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (token_hash(code), organization_id, actor_id, device_id, _ts(now), _ts(now + timedelta(seconds=CODE_SECONDS))),
        )
    return code


def _create_session(db: GatewayDB, *, organization_id: str, actor_id: str, auth_method: str) -> str:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    with db.connect() as conn:
        db._execute(
            conn,
            "INSERT INTO gateway_me_sessions(session_hash, organization_id, actor_id, auth_method, created_at, last_seen_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (token_hash(token), organization_id, actor_id, auth_method, _ts(now), _ts(now), _ts(now + timedelta(hours=SESSION_HOURS))),
        )
    return token


def exchange_login_code(db: GatewayDB, code: str) -> tuple[str, dict[str, Any]]:
    value = str(code or "").strip()
    now = _now()
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_me_codes WHERE code_hash = ?", (token_hash(value),)) if value.startswith(CODE_PREFIX) else {}
        if not row or row.get("used_at") or row["expires_at"] < _ts(now):
            raise MeError("this link has expired or was already used; open it again from OpenWorkGraph", status_code=401)
        db._execute(conn, "UPDATE gateway_me_codes SET used_at = ? WHERE code_hash = ?", (_ts(now), row["code_hash"]))
        # The device must still be enrolled at the moment the code is used.
        active = _one(
            db, conn,
            "SELECT token_id FROM access_tokens WHERE organization_id = ? AND device_id = ? AND token_type = 'device' AND revoked_at IS NULL AND actor_id = ?",
            (row["organization_id"], row["device_id"], row["actor_id"]),
        )
    if not active:
        raise MeError("this computer is no longer connected to the organization", status_code=401)
    session = _create_session(db, organization_id=row["organization_id"], actor_id=row["actor_id"], auth_method="device_link")
    return session, {"organization_id": row["organization_id"], "actor_id": row["actor_id"]}


def sso_session(db: GatewayDB, *, email: str, organization_id: str = "") -> tuple[str, dict[str, Any]]:
    from .employees import employee_by_email

    matches = employee_by_email(db, organization_id or None, email)
    if not matches:
        raise MeError(f"{email} is not in your organization's OpenWorkGraph roster; ask your IT admin", status_code=403)
    employee = matches[0]
    session = _create_session(db, organization_id=employee["organization_id"], actor_id=employee["actor_id"], auth_method="sso")
    return session, {"organization_id": employee["organization_id"], "actor_id": employee["actor_id"]}


def validate_session(db: GatewayDB, token: str) -> dict[str, Any] | None:
    if not str(token or "").startswith(SESSION_PREFIX):
        return None
    now = _now()
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_me_sessions WHERE session_hash = ?", (token_hash(token),))
        if not row or row.get("revoked_at") or row["expires_at"] < _ts(now) or row["last_seen_at"] < _ts(now - timedelta(minutes=SESSION_IDLE_MINUTES)):
            return None
        db._execute(conn, "UPDATE gateway_me_sessions SET last_seen_at = ? WHERE session_hash = ?", (_ts(now), row["session_hash"]))
    return {"organization_id": row["organization_id"], "actor_id": row["actor_id"], "auth_method": row["auth_method"]}


def revoke_session(db: GatewayDB, token: str) -> None:
    with db.connect() as conn:
        db._execute(conn, "UPDATE gateway_me_sessions SET revoked_at = ? WHERE session_hash = ?", (_ts(_now()), token_hash(token)))


# --- views ---------------------------------------------------------------------------------------

_READ_MODES = {
    "self": "You read your own evidence",
    "team": "A team lead with access to your team read your evidence",
    "organization": "Someone with organization-wide access read your evidence",
}
_READ_ACTIONS = {"human_access.trace.read", "human_access.search.read"}
# Reads by integration tokens; with an empty actor_id they covered every employee.
_INTEGRATION_READS = {
    "evidence.trace.read": "An integration read your evidence",
    "evidence.search": "An integration searched your evidence",
    "context.current.read": "An integration read your current context",
    "transfers.read": "An integration read handoffs involving you",
}
_ORG_WIDE_READS = {
    "evidence.trace.read": "An integration read everyone's evidence, including yours",
    "evidence.search": "An integration searched everyone's evidence, including yours",
    "context.current.read": "An integration read everyone's current context, including yours",
    "transfers.read": "An integration read handoffs across the organization",
}
_ORG_WIDE = '%"actor_id": ""%'  # json.dumps default separators, as db.audit writes it
_EVENTS = {
    "employee.created": "You were added to the employee roster",
    "employee.updated": "Your roster entry was changed",
    "employee.offboarded": "You were offboarded; your computers were disconnected",
    "employee.invite.created": "A personal invitation was created for you",
    "employee.invite.revoked": "A personal invitation for you was revoked",
    "employee.invite.sso_verified": "You confirmed a personal invitation with your company account",
    "device.enrolled": "A computer joined as you",
    "device.linked_to_employee": "A computer was linked to you by an administrator",
    "device.admin_revoked": "One of your computers was disconnected by an administrator",
    "device.revoked": "One of your computers was disconnected",
    "human_access.actor_teams.updated": "Your teams were changed",
}


def overview(db: GatewayDB, *, organization_id: str, actor_id: str) -> dict[str, Any]:
    from .employees import employee_by_actor, people_and_devices
    from .human_access import actor_teams
    from .lifecycle import get_retention_policy

    employee = employee_by_actor(db, organization_id, actor_id)
    policy = db.get_policy(organization_id)
    everyone = people_and_devices(db, organization_id)
    devices: list[dict[str, Any]] = []
    for person in everyone["employees"]:
        if person["actor_id"] == actor_id:
            devices = person["devices"]
    if not devices:
        devices = [d for d in everyone["unlinked_devices"] if d["actor_id"] == actor_id]
    now = _now()
    with db.connect() as conn:
        counts = {}
        for days in (7, 30):
            row = _one(
                db, conn,
                "SELECT COUNT(*) AS n FROM evidence_events WHERE organization_id = ? AND actor_id = ? AND observed_at >= ?",
                (organization_id, actor_id, _ts(now - timedelta(days=days))),
            )
            counts[f"last_{days}_days"] = int(row.get("n") or 0)
        total = _one(db, conn, "SELECT COUNT(*) AS n, MIN(observed_at) AS first_at FROM evidence_events WHERE organization_id = ? AND actor_id = ?", (organization_id, actor_id))
        readers = _all(
            db, conn,
            "SELECT token_id, scopes_json, created_at FROM access_tokens WHERE organization_id = ? AND token_type = 'integration' AND revoked_at IS NULL",
            (organization_id,),
        )
    org_readers = 0
    for token in readers:
        try:
            scopes = set(json.loads(token.get("scopes_json") or "[]"))
        except Exception:
            scopes = set()
        if scopes & {"evidence:read", "context:read", "transfers:read", "agent-sessions:read"}:
            org_readers += 1
    retention = get_retention_policy(db, organization_id)
    return {
        "organization_id": organization_id,
        "you": {
            "actor_id": actor_id,
            "email": employee["email"] if employee else actor_id,
            "display_name": employee["display_name"] if employee else "",
            "in_roster": bool(employee),
            "teams": sorted(actor_teams(db, organization_id, actor_id)),
        },
        "computers": devices,
        "evidence": {**counts, "total": int(total.get("n") or 0), "first_at": total.get("first_at")},
        "what_is_shared": {
            "window_and_page_titles": bool(policy.get("share_window_titles", True)),
            "structural_details": bool(policy.get("share_metadata", True)),
            "excluded_apps": bool(policy.get("share_excluded", False)),
            "ai_agent_activity_permitted": bool(policy.get("allow_agent_events", False)),
            "ai_agent_session_messages_permitted": bool(policy.get("allow_agent_session_messages", False)),
            "limited_to_event_types": list(policy.get("allowed_event_types") or []),
        },
        "never_shared": ["typed text", "clipboard contents", "screenshots", "passwords"],
        "who_can_read": {
            "you": True,
            "team_leads_of_your_teams": sorted(actor_teams(db, organization_id, actor_id)),
            "integrations_with_organization_read_access": org_readers,
            "gateway_administrators_can_read_evidence": False,
            "note": "Administrators manage computers, invitations and policy; reading evidence needs an explicit access scope, and every read is logged.",
        },
        "retention_days": retention.get("retention_days"),
    }


def _who(principal: str, details: dict[str, Any], actor_id: str) -> str:
    """A readable name for an audit principal."""
    if principal == f"employee:{actor_id}":
        return "you"
    if principal.startswith("admin:"):
        return "administrator " + principal.split(":", 1)[1]
    if principal == "bootstrap-token":
        return "Gateway bootstrap token"
    if principal == "gateway-admin":
        return "Gateway administrator"
    if principal.startswith("device_") and details.get("device_id"):
        return f"computer {details['device_id']}"
    return principal


def access_log(db: GatewayDB, *, organization_id: str, actor_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Recorded reads of this employee's evidence and admin actions about them."""
    # The LIKE prefilter keeps this an indexed-org scan of only candidate rows; the
    # exact match below decides. `_` and `%` in addresses only widen the prefilter.
    needle = "%" + json.dumps(actor_id, ensure_ascii=False) + "%"  # as db.audit writes it
    with db.connect() as conn:
        rows = _all(
            db, conn,
            "SELECT observed_at, principal_id, action, details_json FROM audit_log "
            "WHERE organization_id = ? AND (details_json LIKE ? OR (action IN (?, ?, ?, ?) AND details_json LIKE ?)) "
            "ORDER BY id DESC",
            (organization_id, needle, *_INTEGRATION_READS, _ORG_WIDE),
        )
    out = []
    for row in rows:
        try:
            details = json.loads(row.get("details_json") or "{}")
        except Exception:
            details = {}
        action = row["action"]
        about_me = details.get("actor_id") == actor_id or details.get("employee_actor_id") == actor_id
        org_wide = action in _INTEGRATION_READS and not details.get("actor_id")
        if not (about_me or org_wide):
            continue
        who = _who(str(row.get("principal_id") or ""), details, actor_id)
        if action in _READ_ACTIONS:
            mode = str(details.get("access_mode") or "")
            label = _READ_MODES.get(mode, "Your evidence was read")
            by = "you" if mode == "self" else who
        elif org_wide:
            label, by = _ORG_WIDE_READS[action], "integration " + who
        elif action in _INTEGRATION_READS:
            label, by = _INTEGRATION_READS[action], "integration " + who
        else:
            label = _EVENTS.get(action) or action.replace("_", " ").replace(".", " · ")
            by = who
        out.append({"at": row["observed_at"], "what": label, "by": by, "rows_returned": details.get("returned")})
        if len(out) >= limit:
            break
    return out
