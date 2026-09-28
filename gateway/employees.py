from __future__ import annotations

"""Employee roster, personal invitations and device identity.

The roster ties every enrolled computer to a real work identity:

* Administrators add employees (work email, name, teams) one by one or by CSV.
* A **personal invitation** is bound to one employee. A computer that joins with
  it gets that employee's identity; nothing is typed by the employee. If the
  invitation requires SSO, the employee must first confirm with the
  organization's sign-in and the verified email must match the roster entry.
* Every device records *how* its identity was established: ``sso_verified``,
  ``personal_invite``, ``admin_linked`` or ``self_reported`` (reusable link,
  legacy code or managed file). Administrators can link a self-reported device
  to a roster employee, optionally re-attributing its past evidence.
* An organization can require verified identity: then only personal
  invitations can enroll new computers.
* Offboarding revokes the employee's device credentials and open invitations.

An employee's ``actor_id`` is their normalized work email, which is also what
the Gateway stores on their evidence and what the /me page reads.
"""

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.time_utils import normalize_timestamp
from .auth import DEVICE_SCOPES, issue_token, token_hash
from .db import GatewayDB

PERSON_TOKEN_PREFIX = "owg_enroll_person"
MAX_INVITE_DAYS = 30
MAX_DEVICES_PER_INVITE = 5
MAX_IMPORT_ROWS = 5000
IDENTITY_SOURCES = ("sso_verified", "personal_invite", "admin_linked", "self_reported")
_EMAIL_RE = re.compile(r"^[^@\s]{1,128}@[^@\s]{1,253}\.[^@\s]{2,63}$")
_TEAM_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_employees (
  employee_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  email TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  actor_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  sso_subject TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  offboarded_at TEXT,
  UNIQUE (organization_id, email),
  UNIQUE (organization_id, actor_id)
);
CREATE TABLE IF NOT EXISTS gateway_personal_invites (
  invite_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  organization_id TEXT NOT NULL,
  organization_name TEXT NOT NULL DEFAULT '',
  employee_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL,
  max_devices INTEGER NOT NULL,
  use_count INTEGER NOT NULL DEFAULT 0,
  require_sso INTEGER NOT NULL DEFAULT 0,
  sso_verified_at TEXT,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_personal_invites_emp ON gateway_personal_invites(organization_id, employee_id);
CREATE TABLE IF NOT EXISTS gateway_device_identity (
  organization_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  employee_id TEXT,
  identity_source TEXT NOT NULL,
  linked_at TEXT NOT NULL,
  linked_by TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (organization_id, device_id)
);
CREATE TABLE IF NOT EXISTS gateway_org_settings (
  organization_id TEXT PRIMARY KEY,
  require_verified_identity INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
)
"""


class EmployeeError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return normalize_timestamp(datetime.now(timezone.utc).isoformat())


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


def init_employee_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            conn.execute(statement)


def _org(value: str) -> str:
    org = str(value or "").strip()
    if not org or len(org) > 512:
        raise EmployeeError("organization_id must be 1-512 characters")
    return org


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise EmployeeError(f"not a valid work email: {str(value)[:80]!r}")
    return email


def _teams(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[;,|]", value)
    teams = sorted({str(t).strip() for t in (value or []) if str(t).strip()})
    bad = [t for t in teams if not _TEAM_RE.fullmatch(t)]
    if bad:
        raise EmployeeError(f"team names may use letters, digits, '.', '_' and '-': {bad[:3]}")
    return teams


# --- organization settings ---------------------------------------------------------------

def org_settings(db: GatewayDB, organization_id: str) -> dict[str, Any]:
    org = _org(organization_id)
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_org_settings WHERE organization_id = ?", (org,))
    return {
        "organization_id": org,
        "require_verified_identity": bool(int(row.get("require_verified_identity") or 0)) if row else False,
        "updated_at": row.get("updated_at") if row else None,
    }


def set_org_settings(db: GatewayDB, organization_id: str, *, require_verified_identity: bool) -> dict[str, Any]:
    org = _org(organization_id)
    with db.connect() as conn:
        db._execute(conn, "DELETE FROM gateway_org_settings WHERE organization_id = ?", (org,))
        db._execute(
            conn,
            "INSERT INTO gateway_org_settings(organization_id, require_verified_identity, updated_at) VALUES (?, ?, ?)",
            (org, 1 if require_verified_identity else 0, _now()),
        )
    return org_settings(db, org)


# --- roster ------------------------------------------------------------------------------------

def _employee_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "employee_id": row["employee_id"],
        "organization_id": row["organization_id"],
        "email": row["email"],
        "display_name": row.get("display_name") or "",
        "actor_id": row["actor_id"],
        "status": row.get("status") or "active",
        "sso_linked": bool(row.get("sso_subject")),
        "created_at": row.get("created_at"),
        "offboarded_at": row.get("offboarded_at"),
    }


def get_employee(db: GatewayDB, organization_id: str, employee_id: str) -> dict[str, Any]:
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_employees WHERE organization_id = ? AND employee_id = ?", (_org(organization_id), employee_id))
    if not row:
        raise EmployeeError("employee not found", status_code=404)
    return _employee_public(row)


def employee_by_email(db: GatewayDB, organization_id: str | None, email: str) -> list[dict[str, Any]]:
    address = normalize_email(email)
    with db.connect() as conn:
        if organization_id:
            rows = _all(db, conn, "SELECT * FROM gateway_employees WHERE organization_id = ? AND email = ? AND status = 'active'", (_org(organization_id), address))
        else:
            rows = _all(db, conn, "SELECT * FROM gateway_employees WHERE email = ? AND status = 'active' ORDER BY organization_id", (address,))
    return [_employee_public(r) for r in rows]


def employee_by_actor(db: GatewayDB, organization_id: str, actor_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_employees WHERE organization_id = ? AND actor_id = ?", (_org(organization_id), str(actor_id or "")))
    return _employee_public(row) if row else None


def upsert_employee(
    db: GatewayDB,
    organization_id: str,
    *,
    email: str,
    display_name: str = "",
    teams: Any = None,
) -> tuple[dict[str, Any], bool]:
    """Create or update an employee. Returns (employee, created)."""
    from .human_access import set_actor_teams

    org = _org(organization_id)
    address = normalize_email(email)
    name = " ".join(str(display_name or "").split())[:120]
    team_list = _teams(teams) if teams is not None else None
    now = _now()
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_employees WHERE organization_id = ? AND email = ?", (org, address))
        created = not row
        if created:
            employee_id = "emp_" + uuid.uuid4().hex[:20]
            db._execute(
                conn,
                "INSERT INTO gateway_employees(employee_id, organization_id, email, display_name, actor_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                (employee_id, org, address, name, address, now, now),
            )
        else:
            employee_id = row["employee_id"]
            db._execute(
                conn,
                "UPDATE gateway_employees SET display_name = CASE WHEN ? <> '' THEN ? ELSE display_name END, status = 'active', offboarded_at = NULL, updated_at = ? WHERE employee_id = ?",
                (name, name, now, employee_id),
            )
    if team_list is not None:
        set_actor_teams(db, org, address, team_list)
    return get_employee(db, org, employee_id), created


def list_employees(db: GatewayDB, organization_id: str, *, include_offboarded: bool = False) -> list[dict[str, Any]]:
    from .human_access import actor_teams

    org = _org(organization_id)
    with db.connect() as conn:
        rows = _all(
            db, conn,
            "SELECT * FROM gateway_employees WHERE organization_id = ?" + ("" if include_offboarded else " AND status = 'active'") + " ORDER BY email",
            (org,),
        )
        invites = _all(
            db, conn,
            "SELECT employee_id, COUNT(*) AS n FROM gateway_personal_invites WHERE organization_id = ? AND revoked_at IS NULL AND expires_at >= ? AND use_count < max_devices GROUP BY employee_id",
            (org, _now()),
        )
    open_invites = {r["employee_id"]: int(r["n"]) for r in invites}
    people = []
    for row in rows:
        item = _employee_public(row)
        item["teams"] = sorted(actor_teams(db, org, row["actor_id"]))
        item["open_invites"] = open_invites.get(row["employee_id"], 0)
        people.append(item)
    return people


def import_csv(db: GatewayDB, organization_id: str, text: str) -> dict[str, Any]:
    """CSV columns: email (required), name, teams (separated by ';')."""
    raw = str(text or "")
    if len(raw) > 2_000_000:
        raise EmployeeError("the CSV is too large (max 2 MB)")
    reader = csv.reader(io.StringIO(raw))
    # Keep the real file line numbers so reported errors point at the right line.
    numbered = [(reader.line_num, r) for r in reader if any(cell.strip() for cell in r)]
    if not numbered:
        raise EmployeeError("the CSV is empty")
    rows = [r for _n, r in numbered]
    header = [c.strip().lower() for c in rows[0]]
    has_header = "email" in header
    idx = {
        "email": header.index("email") if has_header else 0,
        "name": header.index("name") if has_header and "name" in header else (1 if not has_header else -1),
        "teams": header.index("teams") if has_header and "teams" in header else (2 if not has_header else -1),
    }
    body = numbered[1:] if has_header else numbered
    if len(body) > MAX_IMPORT_ROWS:
        raise EmployeeError(f"import at most {MAX_IMPORT_ROWS} employees at a time")
    created = updated = 0
    errors: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for number, row in body:
        def cell(key: str) -> str:
            i = idx[key]
            return row[i].strip() if 0 <= i < len(row) else ""
        try:
            teams = cell("teams")
            employee, was_created = upsert_employee(
                db, organization_id, email=cell("email"), display_name=cell("name"),
                teams=teams if idx["teams"] >= 0 else None,
            )
            changed.append({"actor_id": employee["actor_id"], "created": was_created})
            created += int(was_created)
            updated += int(not was_created)
        except EmployeeError as exc:
            errors.append({"line": number, "error": str(exc)})
    return {"created": created, "updated": updated, "errors": errors[:200], "error_count": len(errors), "changed": changed}


def offboard_employee(db: GatewayDB, organization_id: str, employee_id: str) -> dict[str, Any]:
    from .human_access import set_actor_teams

    org = _org(organization_id)
    employee = get_employee(db, org, employee_id)
    now = _now()
    with db.connect() as conn:
        devices = _all(
            db, conn,
            "SELECT DISTINCT device_id FROM access_tokens WHERE organization_id = ? AND token_type = 'device' AND actor_id = ? AND revoked_at IS NULL",
            (org, employee["actor_id"]),
        )
        db._execute(
            conn,
            "UPDATE access_tokens SET revoked_at = ? WHERE organization_id = ? AND token_type = 'device' AND actor_id = ? AND revoked_at IS NULL",
            (now, org, employee["actor_id"]),
        )
        db._execute(
            conn,
            "UPDATE gateway_personal_invites SET revoked_at = ? WHERE organization_id = ? AND employee_id = ? AND revoked_at IS NULL",
            (now, org, employee_id),
        )
        db._execute(
            conn,
            "UPDATE gateway_employees SET status = 'offboarded', offboarded_at = ?, updated_at = ? WHERE employee_id = ?",
            (now, now, employee_id),
        )
    set_actor_teams(db, org, employee["actor_id"], [])
    return {
        **get_employee(db, org, employee_id),
        "revoked_devices": [d["device_id"] for d in devices],
        "evidence_deleted": False,
    }


# --- personal invitations --------------------------------------------------------------------

def create_personal_invite(
    db: GatewayDB,
    organization_id: str,
    employee_id: str,
    *,
    organization_name: str = "",
    expires_days: int = 7,
    max_devices: int = 2,
    require_sso: bool = False,
    created_by: str = "",
) -> dict[str, Any]:
    org = _org(organization_id)
    employee = get_employee(db, org, employee_id)
    if employee["status"] != "active":
        raise EmployeeError("this employee is offboarded; add them again first", status_code=409)
    days = max(1, min(int(expires_days), MAX_INVITE_DAYS))
    devices = max(1, min(int(max_devices), MAX_DEVICES_PER_INVITE))
    token = issue_token(PERSON_TOKEN_PREFIX)
    invite_id = "pinv_" + token_hash(token)[:20]
    now = datetime.now(timezone.utc)
    expires = normalize_timestamp((now + timedelta(days=days)).isoformat())
    with db.connect() as conn:
        db._execute(
            conn,
            """INSERT INTO gateway_personal_invites(invite_id, token_hash, organization_id, organization_name, employee_id,
                   created_at, created_by, expires_at, max_devices, use_count, require_sso, sso_verified_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL)""",
            (invite_id, token_hash(token), org, str(organization_name or org).strip()[:120] or org, employee_id,
             normalize_timestamp(now.isoformat()), str(created_by or "")[:200], expires, devices, 1 if require_sso else 0),
        )
    return {
        "invite_id": invite_id,
        "token": token,
        "organization_id": org,
        "employee": employee,
        "expires_at": expires,
        "max_devices": devices,
        "require_sso": bool(require_sso),
    }


def list_personal_invites(db: GatewayDB, organization_id: str, *, employee_id: str = "") -> list[dict[str, Any]]:
    org = _org(organization_id)
    with db.connect() as conn:
        sql = """SELECT i.*, e.email, e.display_name FROM gateway_personal_invites i
                 JOIN gateway_employees e ON e.employee_id = i.employee_id
                 WHERE i.organization_id = ?"""
        params: tuple[Any, ...] = (org,)
        if employee_id:
            sql += " AND i.employee_id = ?"
            params = (org, employee_id)
        rows = _all(db, conn, sql + " ORDER BY i.created_at DESC LIMIT 500", params)
    now = _now()
    out = []
    for r in rows:
        state = (
            "revoked" if r.get("revoked_at") else
            "used" if int(r["use_count"]) >= int(r["max_devices"]) else
            "expired" if r["expires_at"] < now else "open"
        )
        out.append({
            "invite_id": r["invite_id"], "employee_id": r["employee_id"], "email": r["email"],
            "display_name": r.get("display_name") or "", "created_at": r["created_at"],
            "created_by": r.get("created_by") or "", "expires_at": r["expires_at"],
            "max_devices": int(r["max_devices"]), "use_count": int(r["use_count"]),
            "require_sso": bool(int(r["require_sso"])), "sso_verified": bool(r.get("sso_verified_at")),
            "state": state,
        })
    return out


def revoke_personal_invite(db: GatewayDB, organization_id: str, invite_id: str) -> bool:
    with db.connect() as conn:
        cur = db._execute(
            conn,
            "UPDATE gateway_personal_invites SET revoked_at = ? WHERE organization_id = ? AND invite_id = ? AND revoked_at IS NULL",
            (_now(), _org(organization_id), invite_id),
        )
        return bool(cur.rowcount)


def _invite_row(db: GatewayDB, conn: Any, token: str, *, lock: bool = False) -> dict[str, Any]:
    sql = """SELECT i.*, e.email, e.display_name, e.actor_id, e.status AS employee_status
             FROM gateway_personal_invites i JOIN gateway_employees e ON e.employee_id = i.employee_id
             WHERE i.token_hash = ?"""
    if lock and db.is_postgres:
        sql += " FOR UPDATE OF i"
    return _one(db, conn, sql, (token_hash(str(token or "")),))


def _usable(row: dict[str, Any]) -> bool:
    return bool(
        row and not row.get("revoked_at") and row.get("employee_status") == "active"
        and row["expires_at"] >= _now() and int(row["use_count"]) < int(row["max_devices"])
    )


def peek_personal_invite(db: GatewayDB, token: str) -> dict[str, Any] | None:
    if not str(token or "").startswith(PERSON_TOKEN_PREFIX + "_"):
        return None
    with db.connect() as conn:
        row = _invite_row(db, conn, token)
    if not _usable(row):
        return None
    return {
        "invite_id": row["invite_id"],
        "organization_id": row["organization_id"],
        "organization_name": row.get("organization_name") or row["organization_id"],
        "employee_id": row["employee_id"],
        "email": row["email"],
        "display_name": row.get("display_name") or "",
        "actor_id": row["actor_id"],
        "expires_at": row["expires_at"],
        "devices_left": int(row["max_devices"]) - int(row["use_count"]),
        "require_sso": bool(int(row["require_sso"])),
        "sso_verified": bool(row.get("sso_verified_at")),
    }


def mark_invite_sso_verified(db: GatewayDB, token: str, *, verified_email: str, subject: str) -> dict[str, Any]:
    """Record that the invited employee proved their identity with the organization's SSO."""
    email = normalize_email(verified_email)
    with db.connect() as conn:
        row = _invite_row(db, conn, token)
        if not _usable(row):
            raise EmployeeError("this invitation is no longer valid; ask your IT admin for a new one", status_code=401)
        if row["email"] != email:
            raise EmployeeError(
                f"you signed in as {email}, but this invitation is for {row['email']}. Sign in with that account, or ask your IT admin.",
                status_code=403,
            )
        now = _now()
        db._execute(conn, "UPDATE gateway_personal_invites SET sso_verified_at = ? WHERE invite_id = ?", (now, row["invite_id"]))
        db._execute(conn, "UPDATE gateway_employees SET sso_subject = ?, updated_at = ? WHERE employee_id = ?", (str(subject)[:512], now, row["employee_id"]))
    return peek_personal_invite(db, token) or {}


def mark_invite_verified_by_id(db: GatewayDB, invite_id: str, *, verified_email: str, subject: str) -> dict[str, Any]:
    """SSO callback variant: the invitation is identified by id stored in the sign-in state."""
    email = normalize_email(verified_email)
    now = _now()
    with db.connect() as conn:
        row = _one(
            db, conn,
            """SELECT i.*, e.email, e.display_name, e.actor_id, e.status AS employee_status
               FROM gateway_personal_invites i JOIN gateway_employees e ON e.employee_id = i.employee_id
               WHERE i.invite_id = ?""",
            (str(invite_id or ""),),
        )
        if not _usable(row):
            raise EmployeeError("this invitation is no longer valid; ask your IT admin for a new one", status_code=401)
        if row["email"] != email:
            raise EmployeeError(
                f"you signed in as {email}, but this invitation is for {row['email']}. Sign in with that account, or ask your IT admin.",
                status_code=403,
            )
        db._execute(conn, "UPDATE gateway_personal_invites SET sso_verified_at = ? WHERE invite_id = ?", (now, row["invite_id"]))
        db._execute(conn, "UPDATE gateway_employees SET sso_subject = ?, updated_at = ? WHERE employee_id = ?", (str(subject)[:512], now, row["employee_id"]))
    return {
        "invite_id": row["invite_id"], "organization_id": row["organization_id"],
        "email": row["email"], "actor_id": row["actor_id"],
    }


def enroll_with_personal_invite(db: GatewayDB, *, token: str, requested_organization_id: str, device_id: str) -> dict[str, Any]:
    """Consume one device slot and create the device credential in one transaction."""
    device = str(device_id or "").strip()
    if not device or len(device) > 512:
        raise EmployeeError("device_id is required", status_code=400)
    device_token = issue_token("owg_device")
    token_id = f"device_{uuid.uuid4().hex}"
    now = _now()
    with db.connect() as conn:
        row = _invite_row(db, conn, token, lock=True)
        if not _usable(row):
            raise EmployeeError("this invitation is invalid, expired, revoked or already used; ask your IT admin for a new one", status_code=401)
        org = row["organization_id"]
        if requested_organization_id and str(requested_organization_id).strip() != org:
            raise EmployeeError("this invitation belongs to a different organization", status_code=403)
        if int(row["require_sso"]) and not row.get("sso_verified_at"):
            raise EmployeeError("confirm your identity with your company sign-in first, then join", status_code=403)
        existing = _one(
            db, conn,
            "SELECT token_id FROM access_tokens WHERE organization_id = ? AND token_type = 'device' AND device_id = ? AND revoked_at IS NULL",
            (org, device),
        )
        if existing:
            raise EmployeeError("this computer is already enrolled; disconnect it first", status_code=409)
        cur = db._execute(
            conn,
            "UPDATE gateway_personal_invites SET use_count = use_count + 1 WHERE invite_id = ? AND use_count < max_devices AND revoked_at IS NULL",
            (row["invite_id"],),
        )
        if not cur.rowcount:
            raise EmployeeError("this invitation was just used up; ask your IT admin for a new one", status_code=401)
        db._execute(
            conn,
            """INSERT INTO access_tokens(token_id, token_hash, token_type, organization_id, actor_id, device_id, scopes_json, created_at, revoked_at)
               VALUES (?, ?, 'device', ?, ?, ?, ?, ?, NULL)""",
            (token_id, token_hash(device_token), org, row["actor_id"], device, json.dumps(sorted(DEVICE_SCOPES)), now),
        )
        source = "sso_verified" if row.get("sso_verified_at") else "personal_invite"
        db._execute(conn, "DELETE FROM gateway_device_identity WHERE organization_id = ? AND device_id = ?", (org, device))
        db._execute(
            conn,
            "INSERT INTO gateway_device_identity(organization_id, device_id, employee_id, identity_source, linked_at, linked_by) VALUES (?, ?, ?, ?, ?, ?)",
            (org, device, row["employee_id"], source, now, row["invite_id"]),
        )
    return {
        "organization_id": org,
        "actor_id": row["actor_id"],
        "device_id": device,
        "token_id": token_id,
        # Same key as every other enrollment response: the local connector reads "token".
        "token": device_token,
        "scopes": sorted(DEVICE_SCOPES),
        "identity_source": source,
        "employee": {"email": row["email"], "display_name": row.get("display_name") or ""},
        "invite_id": row["invite_id"],
    }


# --- device identity ---------------------------------------------------------------------------------

def record_self_reported(db: GatewayDB, organization_id: str, device_id: str, *, linked_by: str) -> None:
    org = _org(organization_id)
    with db.connect() as conn:
        db._execute(conn, "DELETE FROM gateway_device_identity WHERE organization_id = ? AND device_id = ?", (org, device_id))
        db._execute(
            conn,
            "INSERT INTO gateway_device_identity(organization_id, device_id, employee_id, identity_source, linked_at, linked_by) VALUES (?, ?, NULL, 'self_reported', ?, ?)",
            (org, device_id, _now(), str(linked_by or "")[:200]),
        )


def link_device(
    db: GatewayDB,
    organization_id: str,
    device_id: str,
    employee_id: str,
    *,
    reattribute_history: bool,
    linked_by: str,
) -> dict[str, Any]:
    org = _org(organization_id)
    employee = get_employee(db, org, employee_id)
    if employee["status"] != "active":
        raise EmployeeError("this employee is offboarded", status_code=409)
    now = _now()
    with db.connect() as conn:
        tokens = _all(
            db, conn,
            "SELECT token_id, actor_id FROM access_tokens WHERE organization_id = ? AND token_type = 'device' AND device_id = ? AND revoked_at IS NULL",
            (org, device_id),
        )
        if not tokens:
            raise EmployeeError("no active computer with this device id", status_code=404)
        previous = sorted({t["actor_id"] for t in tokens})
        db._execute(
            conn,
            "UPDATE access_tokens SET actor_id = ? WHERE organization_id = ? AND token_type = 'device' AND device_id = ? AND revoked_at IS NULL",
            (employee["actor_id"], org, device_id),
        )
        moved = 0
        if reattribute_history:
            cur = db._execute(
                conn,
                "UPDATE evidence_events SET actor_id = ? WHERE organization_id = ? AND device_id = ? AND actor_id <> ?",
                (employee["actor_id"], org, device_id, employee["actor_id"]),
            )
            moved = int(cur.rowcount or 0)
        db._execute(conn, "DELETE FROM gateway_device_identity WHERE organization_id = ? AND device_id = ?", (org, device_id))
        db._execute(
            conn,
            "INSERT INTO gateway_device_identity(organization_id, device_id, employee_id, identity_source, linked_at, linked_by) VALUES (?, ?, ?, 'admin_linked', ?, ?)",
            (org, device_id, employee_id, now, str(linked_by or "")[:200]),
        )
    return {
        "organization_id": org, "device_id": device_id, "employee": employee,
        "previous_actor_ids": previous, "reattributed_events": moved,
    }


def people_and_devices(db: GatewayDB, organization_id: str) -> dict[str, Any]:
    """Employees with their computers, plus computers not linked to anyone."""
    org = _org(organization_id)
    with db.connect() as conn:
        devices = _all(
            db, conn,
            """SELECT t.device_id, t.actor_id, t.created_at, t.token_id, d.employee_id, d.identity_source, d.linked_at
               FROM access_tokens t LEFT JOIN gateway_device_identity d
                 ON d.organization_id = t.organization_id AND d.device_id = t.device_id
               WHERE t.organization_id = ? AND t.token_type = 'device' AND t.revoked_at IS NULL
               ORDER BY t.created_at DESC""",
            (org,),
        )
        activity = _all(
            db, conn,
            "SELECT device_id, COUNT(*) AS events, MAX(ingested_at) AS last_evidence FROM evidence_events WHERE organization_id = ? GROUP BY device_id",
            (org,),
        )
    by_device = {a["device_id"]: a for a in activity}
    employees = list_employees(db, org)
    by_actor = {e["actor_id"]: e for e in employees}
    by_id = {e["employee_id"]: e for e in employees}
    for e in employees:
        e["devices"] = []
    unlinked = []
    for d in devices:
        act = by_device.get(d["device_id"]) or {}
        item = {
            "device_id": d["device_id"],
            "actor_id": d["actor_id"],
            "joined_at": d["created_at"],
            "last_evidence_at": act.get("last_evidence"),
            "events": int(act.get("events") or 0),
            "identity_source": d.get("identity_source") or "self_reported",
        }
        owner = by_id.get(d.get("employee_id") or "") or by_actor.get(d["actor_id"])
        if owner and item["identity_source"] != "self_reported":
            owner["devices"].append(item)
        elif owner:
            # Self-reported identity that happens to match a roster email: shown, but flagged.
            item["identity_note"] = "self-reported identity matches this employee's email"
            owner["devices"].append(item)
        else:
            unlinked.append(item)
    return {
        "organization_id": org,
        "employees": employees,
        "unlinked_devices": unlinked,
        "settings": org_settings(db, org),
    }


def device_identity(db: GatewayDB, organization_id: str, device_id: str) -> dict[str, Any]:
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_device_identity WHERE organization_id = ? AND device_id = ?", (organization_id, device_id))
    return {
        "identity_source": (row.get("identity_source") if row else None) or "self_reported",
        "employee_id": row.get("employee_id") if row else None,
    }
