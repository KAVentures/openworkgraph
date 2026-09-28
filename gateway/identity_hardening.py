from __future__ import annotations

"""Security hardening for Gateway identity/admin flows.

Kept separate from the route layer so the invariants are easy to test:

* the bootstrap credential can recover the sole owner when API bootstrap access
  has deliberately been enabled;
* an SSO confirmation on a personal invitation authorizes exactly one computer
  enrollment, even when the invitation itself allows several computers;
* company sign-in to /me never guesses between two organizations that contain
  the same work email.
"""

import json
import uuid
from typing import Any

from . import admin_accounts as accounts
from . import employees as roster
from . import me_access

_INSTALLED = False


def _bootstrap_reset_admin(db: Any, admin_id: str) -> dict[str, Any]:
    """Reset an admin, including the sole owner, under bootstrap recovery."""
    with db.connect() as conn:
        row = accounts._one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
        if not row:
            raise accounts.AdminAuthError("administrator not found", status_code=404)
        now = accounts._ts(accounts._now_dt())
        db._execute(
            conn,
            "UPDATE gateway_admins SET password_hash = '', totp_secret = '', totp_pending_secret = '', "
            "totp_last_step = 0, active = 0, failed_count = 0, locked_until = NULL WHERE admin_id = ?",
            (admin_id,),
        )
        db._execute(
            conn,
            "UPDATE gateway_admin_sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
            (now, admin_id),
        )
        token, expires = accounts._issue_setup(db, conn, admin_id)
        row = accounts._one(db, conn, "SELECT * FROM gateway_admins WHERE admin_id = ?", (admin_id,))
    return {**accounts._public(row), "setup_token": token, "setup_expires_at": expires}


def _secure_personal_enroll(
    db: Any,
    *,
    token: str,
    requested_organization_id: str,
    device_id: str,
) -> dict[str, Any]:
    """Consume one invite slot and, when required, one SSO proof atomically."""
    device = str(device_id or "").strip()
    if not device or len(device) > 512:
        raise roster.EmployeeError("device_id is required", status_code=400)

    device_token = roster.issue_token("owg_device")
    token_id = f"device_{uuid.uuid4().hex}"
    now = roster._now()

    with db.connect() as conn:
        row = roster._invite_row(db, conn, token, lock=True)
        if not roster._usable(row):
            raise roster.EmployeeError(
                "this invitation is invalid, expired, revoked or already used; ask your IT admin for a new one",
                status_code=401,
            )
        org = row["organization_id"]
        if requested_organization_id and str(requested_organization_id).strip() != org:
            raise roster.EmployeeError("this invitation belongs to a different organization", status_code=403)

        requires_sso = bool(int(row["require_sso"]))
        if requires_sso and not row.get("sso_verified_at"):
            raise roster.EmployeeError(
                "confirm your identity with your company sign-in first, then join",
                status_code=403,
            )

        existing = roster._one(
            db,
            conn,
            "SELECT token_id FROM access_tokens WHERE organization_id = ? AND token_type = 'device' "
            "AND device_id = ? AND revoked_at IS NULL",
            (org, device),
        )
        if existing:
            raise roster.EmployeeError("this computer is already enrolled; disconnect it first", status_code=409)

        # The conditional also protects SQLite/concurrent callers that observed
        # the same verification before either writer acquired the write lock.
        cur = db._execute(
            conn,
            "UPDATE gateway_personal_invites "
            "SET use_count = use_count + 1, "
            "sso_verified_at = CASE WHEN require_sso = 1 THEN NULL ELSE sso_verified_at END "
            "WHERE invite_id = ? AND use_count < max_devices AND revoked_at IS NULL "
            "AND (require_sso = 0 OR sso_verified_at IS NOT NULL)",
            (row["invite_id"],),
        )
        if not cur.rowcount:
            raise roster.EmployeeError(
                "this invitation was just used or its identity confirmation was already consumed; confirm again",
                status_code=401,
            )

        db._execute(
            conn,
            "INSERT INTO access_tokens(token_id, token_hash, token_type, organization_id, actor_id, device_id, scopes_json, created_at, revoked_at) "
            "VALUES (?, ?, 'device', ?, ?, ?, ?, ?, NULL)",
            (
                token_id,
                roster.token_hash(device_token),
                org,
                row["actor_id"],
                device,
                json.dumps(sorted(roster.DEVICE_SCOPES)),
                now,
            ),
        )
        source = "sso_verified" if requires_sso else "personal_invite"
        db._execute(
            conn,
            "DELETE FROM gateway_device_identity WHERE organization_id = ? AND device_id = ?",
            (org, device),
        )
        db._execute(
            conn,
            "INSERT INTO gateway_device_identity(organization_id, device_id, employee_id, identity_source, linked_at, linked_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (org, device, row["employee_id"], source, now, row["invite_id"]),
        )

    return {
        "organization_id": org,
        "actor_id": row["actor_id"],
        "device_id": device,
        "token_id": token_id,
        "token": device_token,
        "scopes": sorted(roster.DEVICE_SCOPES),
        "identity_source": source,
        "employee": {"email": row["email"], "display_name": row.get("display_name") or ""},
        "invite_id": row["invite_id"],
    }


def _unambiguous_sso_session(
    db: Any,
    *,
    email: str,
    organization_id: str = "",
) -> tuple[str, dict[str, Any]]:
    matches = roster.employee_by_email(db, organization_id or None, email)
    if not matches:
        raise me_access.MeError(
            f"{email} is not in your organization's OpenWorkGraph roster; ask your IT admin",
            status_code=403,
        )
    if not organization_id and len(matches) != 1:
        raise me_access.MeError(
            "this company account belongs to more than one organization on this Gateway; "
            "open /me from your enrolled OpenWorkGraph computer so the organization is unambiguous",
            status_code=409,
        )
    employee = matches[0]
    session = me_access._create_session(
        db,
        organization_id=employee["organization_id"],
        actor_id=employee["actor_id"],
        auth_method="sso",
    )
    return session, {"organization_id": employee["organization_id"], "actor_id": employee["actor_id"]}


def install_identity_security_hardening() -> None:
    """Install the hardened implementations once per process."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_reset = accounts.reset_admin

    def reset_admin(db: Any, admin_id: str) -> dict[str, Any]:
        actor = accounts.current_admin()
        if actor is not None and actor.admin_id == "bootstrap":
            return _bootstrap_reset_admin(db, admin_id)
        return original_reset(db, admin_id)

    accounts.reset_admin = reset_admin
    roster.enroll_with_personal_invite = _secure_personal_enroll
    me_access.sso_session = _unambiguous_sso_session
    _INSTALLED = True


__all__ = ["install_identity_security_hardening"]
