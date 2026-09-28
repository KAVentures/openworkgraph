from __future__ import annotations

"""Named Gateway administrator accounts.

* Each administrator has their own account: work email, display name, role.
* Roles: ``owner`` (everything, including managing administrators), ``admin``
  (everything except managing administrators) and ``viewer`` (read-only).
* Sign-in: password (scrypt) plus a TOTP authenticator code (RFC 6238), or the
  organization's SSO when configured. Accounts are activated through a
  single-use setup link, so passwords are never chosen or sent by someone else.
* Sessions are random bearer tokens stored only as hashes, with an idle and an
  absolute lifetime. Repeated failures lock an account and throttle the source.
* The ``OWG_GATEWAY_ADMIN_TOKEN`` bootstrap token can create the first owner
  only while no administrator exists. After that it can be switched off for API
  use with ``OWG_GATEWAY_ADMIN_TOKEN_API=disabled``.

All tables work on SQLite (development) and PostgreSQL.
"""

import base64
import contextvars
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from shared.time_utils import normalize_timestamp
from .auth import token_hash
from .db import GatewayDB

ROLES = ("owner", "admin", "viewer")
PASSWORD_MIN_LENGTH = 12
SESSION_IDLE_MINUTES = 60
SESSION_ABSOLUTE_HOURS = 12
SETUP_LINK_HOURS = 72
LOCK_AFTER_FAILURES = 5
LOCK_MINUTES = 15
THROTTLE_WINDOW_MINUTES = 15
THROTTLE_MAX_FAILURES = 20
SESSION_PREFIX = "owg_admin_session_"
SETUP_PREFIX = "owg_admin_setup_"
_EMAIL_RE = re.compile(r"^[^@\s]{1,128}@[^@\s]{1,253}\.[^@\s]{2,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_admins (
  admin_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL,
  password_hash TEXT NOT NULL DEFAULT '',
  totp_secret TEXT NOT NULL DEFAULT '',
  totp_pending_secret TEXT NOT NULL DEFAULT '',
  totp_last_step INTEGER NOT NULL DEFAULT 0,
  setup_token_hash TEXT,
  setup_expires_at TEXT,
  active INTEGER NOT NULL DEFAULT 0,
  disabled_at TEXT,
  failed_count INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT '',
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS gateway_admin_sessions (
  session_hash TEXT PRIMARY KEY,
  admin_id TEXT NOT NULL,
  auth_method TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gateway_admin_sessions_admin ON gateway_admin_sessions(admin_id);
CREATE TABLE IF NOT EXISTS gateway_auth_throttle (
  throttle_key TEXT PRIMARY KEY,
  failures INTEGER NOT NULL,
  window_start TEXT NOT NULL
)
"""


class AdminAuthError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AdminIdentity:
    admin_id: str
    email: str
    display_name: str
    role: str
    auth_method: str

    @property
    def audit_id(self) -> str:
        return "bootstrap-token" if self.admin_id == "bootstrap" else f"admin:{self.email}"

    def can_write(self) -> bool:
        return self.role in {"owner", "admin"}

    def is_owner(self) -> bool:
        return self.role == "owner"


BOOTSTRAP_IDENTITY = AdminIdentity("bootstrap", "", "Bootstrap token", "owner", "bootstrap_token")

# Set by the admin identity middleware for the duration of one request.
CURRENT_ADMIN: contextvars.ContextVar[AdminIdentity | None] = contextvars.ContextVar("owg_current_admin", default=None)


def current_admin() -> AdminIdentity | None:
    return CURRENT_ADMIN.get()


# --- helpers ----------------------------------------------------------------------------

def _now_dt() -> datetime:
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


def init_admin_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            conn.execute(statement)


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise AdminAuthError("enter a valid work email address")
    return email


def bootstrap_token_api_enabled() -> bool:
    return str(os.getenv("OWG_GATEWAY_ADMIN_TOKEN_API", "enabled")).strip().lower() not in {"disabled", "off", "0", "false"}


# --- passwords -----------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def check_password_strength(password: str, email: str = "") -> None:
    value = str(password or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise AdminAuthError(f"use at least {PASSWORD_MIN_LENGTH} characters")
    if len(value) > 256:
        raise AdminAuthError("password is too long")
    if email and email.split("@", 1)[0] and email.split("@", 1)[0].lower() in value.lower():
        raise AdminAuthError("the password must not contain your email name")
    if len(set(value)) < 6:
        raise AdminAuthError("the password is too repetitive")


# --- TOTP (RFC 6238, SHA-1, 6 digits, 30 s) -------------------------------------------------

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    return _totp_at(secret, int((at if at is not None else time.time()) // 30))


def verify_totp(secret: str, code: str, *, last_step: int = 0, at: float | None = None) -> int | None:
    """Return the matched time step (±1 step drift), refusing reuse of a spent step."""
    value = re.sub(r"\s+", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", value) or not secret:
        return None
    now_step = int((at if at is not None else time.time()) // 30)
    for step in (now_step - 1, now_step, now_step + 1):
        if step > last_step and hmac.compare_digest(_totp_at(secret, step), value):
            return step
    return None


def otpauth_uri(secret: str, email: str, issuer: str = "OpenWorkGraph Gateway") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


# --- throttling ------------------------------------------------------------------------------------

def throttle_check(db: GatewayDB, key: str) -> None:
    if not key:
        return
    now = _now_dt()
    with db.connect() as conn:
        row = _one(db, conn, "SELECT failures, window_start FROM gateway_auth_throttle WHERE throttle_key = ?", (key,))
    if row and row["window_start"] >= _ts(now - timedelta(minutes=THROTTLE_WINDOW_MINUTES)):
        if int(row["failures"]) >= THROTTLE_MAX_FAILURES:
            raise AdminAuthError("too many failed sign-in attempts; try again in a few minutes", status_code=429)


def throttle_fail(db: GatewayDB, key: str) -> None:
    if not key:
        return
    now = _now_dt()
    with db.connect() as conn:
        row = _one(db, conn, "SELECT failures, window_start FROM gateway_auth_throttle WHERE throttle_key = ?", (key,))
        if not row or row["window_start"] < _ts(now - timedelta(minutes=THROTTLE_WINDOW_MINUTES)):
            db._execute(conn, "DELETE FROM gateway_auth_throttle WHERE throttle_key = ?", (key,))
            db._execute(conn, "INSERT INTO gateway_auth_throttle(throttle_key, failures, window_start) VALUES (?, 1, ?)", (key, _ts(now)))
        else:
            db._execute(conn, "UPDATE gateway_auth_throttle SET failures = failures + 1 WHERE throttle_key = ?", (key,))


# --- accounts ----------------------------------------------------------------------------------------

def _public(row: dict[str, Any]) -> dict[str, Any]:
    status = "disabled" if row.get("disabled_at") else "active" if int(row.get("active") or 0) else "setup_pending"
    return {
        "admin_id": row["admin_id"],
        "email": row["email"],
        "display_name": row.get("display_name") or "",
        "role": row["role"],
        "status": status,
        "two_factor": bool(row.get("totp_secret")),
        "created_at": row.get("created_at"),
        "created_by": row.get("created_by") or "",
        "last_login_at": row.get("last_login_at"),
        "locked": bool(row.get("locked_until") and row["locked_until"] > _ts(_now_dt())),
    }


def admin_count(db: GatewayDB) -> int:
    with db.connect() as conn:
        row = _one(db, conn, "SELECT COUNT(*) AS n FROM gateway_admins")
    return int(row.get("n") or 0)


def list_admins(db: GatewayDB) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = _all(db, conn, "SELECT * FROM gateway_admins ORDER BY created_at ASC")
    return [_public(r) for r in rows]


def _issue_setup(db: GatewayDB, conn: Any, admin_id: str) -> tuple[str, str]:
    token = SETUP_PREFIX + secrets.token_urlsafe(32)
    expires = _ts(_now_dt() + timedelta(hours=SETUP_LINK_HOURS))
    db._execute(
        conn,
        "UPDATE gateway_admins SET setup_token_hash = ?, setup_expires_at = ?, totp_pending_secret = '' WHERE admin_id = ?",
        (token_hash(token), expires, admin_id),
    )
    return token, expires


def create_admin(db: GatewayDB, *, email: str, display_name: str, role: str, created_by: str) -> dict[str, Any]:
    address = normalize_email(email)
    if role not in ROLES:
        raise AdminAuthError(f"role must be one of {', '.join(ROLES)}")
    name = " ".join(str(display_name or "").split())[:120]
    admin_id = "adm_" + uuid.uuid4().hex[:20]
    with db.connect() as conn:
        if _one(db, conn, "SELECT admin_id FROM gateway_admins WHERE email = ?", (address,)):
            raise AdminAuthError("an administrator with this email already exists", status_code=409)
        db._execute(
            conn,
            "INSERT INTO gateway_admins(admin_id, email, display_name, role, active, created_at, created_by) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (admin_id, address, name, role, _ts(_now_dt()), str(created_by or "")[:200]),
        )
        token, expires = _issue_setup(db, conn, admin_id)
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
    return {**_public(row), "setup_token": token, "setup_expires_at": expires}


def bootstrap_first_owner(db: GatewayDB, *, email: str, display_name: str) -> dict[str, Any]:
    """Create the first owner. Only possible while no administrator exists."""
    if admin_count(db) > 0:
        raise AdminAuthError("an administrator already exists; sign in with that account", status_code=409)
    return create_admin(db, email=email, display_name=display_name, role="owner", created_by="bootstrap-token")


def _active_owner_count(db: GatewayDB, conn: Any, exclude: str = "") -> int:
    row = _one(
        db, conn,
        "SELECT COUNT(*) AS n FROM gateway_admins WHERE role = 'owner' AND active = 1 AND disabled_at IS NULL AND admin_id <> ?",
        (exclude,),
    )
    return int(row.get("n") or 0)


def update_admin(db: GatewayDB, admin_id: str, *, role: str | None = None, disabled: bool | None = None) -> dict[str, Any]:
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
        if not row:
            raise AdminAuthError("administrator not found", status_code=404)
        losing_owner = row["role"] == "owner" and (
            (role is not None and role != "owner") or disabled is True
        )
        if losing_owner and _active_owner_count(db, conn, exclude=admin_id) == 0:
            raise AdminAuthError("keep at least one active owner", status_code=409)
        if role is not None:
            if role not in ROLES:
                raise AdminAuthError(f"role must be one of {', '.join(ROLES)}")
            db._execute(conn, "UPDATE gateway_admins SET role = ? WHERE admin_id = ?", (role, admin_id))
        if disabled is True:
            db._execute(conn, "UPDATE gateway_admins SET disabled_at = ? WHERE admin_id = ?", (_ts(_now_dt()), admin_id))
            db._execute(conn, "UPDATE gateway_admin_sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL", (_ts(_now_dt()), admin_id))
        elif disabled is False:
            db._execute(conn, "UPDATE gateway_admins SET disabled_at = NULL, failed_count = 0, locked_until = NULL WHERE admin_id = ?", (admin_id,))
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
    return _public(row)


def reset_admin(db: GatewayDB, admin_id: str) -> dict[str, Any]:
    """Clear password and 2FA, end sessions and issue a new setup link."""
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
        if not row:
            raise AdminAuthError("administrator not found", status_code=404)
        if row["role"] == "owner" and _active_owner_count(db, conn, exclude=admin_id) == 0:
            raise AdminAuthError("add another owner before resetting the only owner", status_code=409)
        db._execute(
            conn,
            "UPDATE gateway_admins SET password_hash = '', totp_secret = '', totp_last_step = 0, active = 0, failed_count = 0, locked_until = NULL WHERE admin_id = ?",
            (admin_id,),
        )
        db._execute(conn, "UPDATE gateway_admin_sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL", (_ts(_now_dt()), admin_id))
        token, expires = _issue_setup(db, conn, admin_id)
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
    return {**_public(row), "setup_token": token, "setup_expires_at": expires}


def _setup_row(db: GatewayDB, conn: Any, setup_token: str) -> dict[str, Any]:
    token = str(setup_token or "").strip()
    if not token.startswith(SETUP_PREFIX):
        raise AdminAuthError("this setup link is invalid", status_code=401)
    row = _one(db, conn, "SELECT * FROM gateway_admins WHERE setup_token_hash = ?", (token_hash(token),))
    if not row or row.get("disabled_at") or (row.get("setup_expires_at") or "") < _ts(_now_dt()):
        raise AdminAuthError("this setup link is invalid or has expired; ask an owner for a new one", status_code=401)
    return row


def start_setup(db: GatewayDB, setup_token: str) -> dict[str, Any]:
    secret = new_totp_secret()
    with db.connect() as conn:
        row = _setup_row(db, conn, setup_token)
        db._execute(conn, "UPDATE gateway_admins SET totp_pending_secret = ? WHERE admin_id = ?", (secret, row["admin_id"]))
    return {
        "email": row["email"],
        "display_name": row.get("display_name") or "",
        "role": row["role"],
        "totp_secret": secret,
        "otpauth_uri": otpauth_uri(secret, row["email"]),
        "password_min_length": PASSWORD_MIN_LENGTH,
    }


def complete_setup(db: GatewayDB, setup_token: str, *, password: str, totp_code: str) -> tuple[dict[str, Any], str]:
    with db.connect() as conn:
        row = _setup_row(db, conn, setup_token)
        check_password_strength(password, row["email"])
        secret = row.get("totp_pending_secret") or ""
        step = verify_totp(secret, totp_code)
        if not secret or step is None:
            raise AdminAuthError("the authenticator code did not match; check the time on your phone and try again")
        db._execute(
            conn,
            "UPDATE gateway_admins SET password_hash = ?, totp_secret = ?, totp_pending_secret = '', totp_last_step = ?, "
            "setup_token_hash = NULL, setup_expires_at = NULL, active = 1, failed_count = 0, locked_until = NULL, last_login_at = ? "
            "WHERE admin_id = ?",
            (hash_password(password), secret, step, _ts(_now_dt()), row["admin_id"]),
        )
    identity = AdminIdentity(row["admin_id"], row["email"], row.get("display_name") or "", row["role"], "password_totp")
    return _public({**row, "active": 1, "totp_secret": secret}), create_session(db, identity)


def login(db: GatewayDB, *, email: str, password: str, totp_code: str, source: str = "") -> tuple[AdminIdentity, str]:
    throttle_check(db, f"ip:{source}")
    generic = AdminAuthError("email, password or authenticator code is not correct", status_code=401)
    try:
        address = normalize_email(email)
    except AdminAuthError:
        throttle_fail(db, f"ip:{source}")
        raise generic
    now = _ts(_now_dt())
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE email = ?", (address,))
    if not row or not int(row.get("active") or 0) or row.get("disabled_at"):
        verify_password(password, hash_password("timing-equalizer"))  # keep timing comparable
        throttle_fail(db, f"ip:{source}")
        raise generic
    if row.get("locked_until") and row["locked_until"] > now:
        raise AdminAuthError("this account is temporarily locked after failed attempts; try again later", status_code=423)
    ok_password = verify_password(password, row.get("password_hash") or "")
    step = verify_totp(row.get("totp_secret") or "", totp_code, last_step=int(row.get("totp_last_step") or 0)) if ok_password else None
    if not ok_password or step is None:
        # Record the failure first; the connection commits only on a clean exit.
        failures = int(row.get("failed_count") or 0) + 1
        locked = _ts(_now_dt() + timedelta(minutes=LOCK_MINUTES)) if failures >= LOCK_AFTER_FAILURES else None
        with db.connect() as conn:
            db._execute(
                conn,
                "UPDATE gateway_admins SET failed_count = ?, locked_until = ? WHERE admin_id = ?",
                (0 if locked else failures, locked, row["admin_id"]),
            )
        throttle_fail(db, f"ip:{source}")
        raise generic
    with db.connect() as conn:
        db._execute(
            conn,
            "UPDATE gateway_admins SET failed_count = 0, locked_until = NULL, totp_last_step = ?, last_login_at = ? WHERE admin_id = ?",
            (step, now, row["admin_id"]),
        )
    identity = AdminIdentity(row["admin_id"], row["email"], row.get("display_name") or "", row["role"], "password_totp")
    return identity, create_session(db, identity)


def login_sso(db: GatewayDB, *, email: str) -> tuple[AdminIdentity, str]:
    address = normalize_email(email)
    with db.connect() as conn:
        row = _one(db, conn, "SELECT * FROM gateway_admins WHERE email = ?", (address,))
        if not row or row.get("disabled_at"):
            raise AdminAuthError("no administrator account for this email; ask an owner to add you", status_code=403)
        # SSO proves identity; the organization's IdP enforces MFA. Activate on first SSO use.
        db._execute(
            conn,
            "UPDATE gateway_admins SET active = 1, setup_token_hash = NULL, setup_expires_at = NULL, last_login_at = ? WHERE admin_id = ?",
            (_ts(_now_dt()), row["admin_id"]),
        )
    identity = AdminIdentity(row["admin_id"], row["email"], row.get("display_name") or "", row["role"], "sso")
    return identity, create_session(db, identity)


# --- sessions ----------------------------------------------------------------------------------------

def create_session(db: GatewayDB, identity: AdminIdentity) -> str:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    now = _now_dt()
    with db.connect() as conn:
        db._execute(
            conn,
            "INSERT INTO gateway_admin_sessions(session_hash, admin_id, auth_method, created_at, last_seen_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (token_hash(token), identity.admin_id, identity.auth_method, _ts(now), _ts(now), _ts(now + timedelta(hours=SESSION_ABSOLUTE_HOURS))),
        )
    return token


def validate_session(db: GatewayDB, token: str) -> AdminIdentity | None:
    if not str(token or "").startswith(SESSION_PREFIX):
        return None
    now = _now_dt()
    with db.connect() as conn:
        row = _one(
            db, conn,
            """SELECT s.session_hash, s.auth_method, s.last_seen_at, s.expires_at, s.revoked_at,
                      a.admin_id, a.email, a.display_name, a.role, a.active, a.disabled_at
               FROM gateway_admin_sessions s JOIN gateway_admins a ON a.admin_id = s.admin_id
               WHERE s.session_hash = ?""",
            (token_hash(token),),
        )
        if not row or row.get("revoked_at") or row.get("disabled_at") or not int(row.get("active") or 0):
            return None
        if row["expires_at"] < _ts(now) or row["last_seen_at"] < _ts(now - timedelta(minutes=SESSION_IDLE_MINUTES)):
            return None
        db._execute(conn, "UPDATE gateway_admin_sessions SET last_seen_at = ? WHERE session_hash = ?", (_ts(now), row["session_hash"]))
    return AdminIdentity(row["admin_id"], row["email"], row.get("display_name") or "", row["role"], row["auth_method"])


def revoke_session(db: GatewayDB, token: str) -> None:
    with db.connect() as conn:
        db._execute(conn, "UPDATE gateway_admin_sessions SET revoked_at = ? WHERE session_hash = ? AND revoked_at IS NULL", (_ts(_now_dt()), token_hash(token)))
