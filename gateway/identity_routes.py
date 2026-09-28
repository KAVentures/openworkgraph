from __future__ import annotations

"""Named administrators, employee roster, company sign-in and the /me page.

Installed into the enterprise Gateway after the organization admin console.

Admin requests
--------------
Every existing admin endpoint still checks the Gateway admin credential. The
``AdminIdentityMiddleware`` lets named administrators use them: it validates an
``owg_admin_session_...`` bearer, enforces the role (viewers are read-only;
managing administrators needs an owner), records who is acting for the audit
log, and only then presents the request to the existing check. The bootstrap
token keeps working for scripts unless ``OWG_GATEWAY_ADMIN_TOKEN_API=disabled``.
"""

import dataclasses
import json
import os
import secrets
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import anyio
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from shared.lifespan import extend_lifespan
from . import admin_accounts as accounts
from . import employees as roster
from . import me_access
from .app import EnrollmentRequest
from .auth import env_token_matches, token_hash
from .db import GatewayDB
from .enrollment_links import peek_enrollment_link
from .settings import GatewaySettings
from .sso import SSOClient, SSOError, SSOSettings, init_sso_schema

_HERE = Path(__file__).resolve().parent
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _bearer(value: str | None) -> str:
    raw = str(value or "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "status_code", 400), detail=str(exc))


# --- middleware ---------------------------------------------------------------------------

class AdminIdentityMiddleware:
    """Pure ASGI middleware so the admin identity is visible to sync endpoints."""

    def __init__(self, app: Any, *, db: GatewayDB, settings: GatewaySettings) -> None:
        self.app = app
        self.db = db
        self.settings = settings

    async def _reject(self, send: Callable, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not path.startswith("/v1/admin/") or path.startswith("/v1/admin/auth/"):
            await self.app(scope, receive, send)
            return
        headers = [(k, v) for k, v in scope.get("headers") or []]
        raw_auth = next((v.decode("latin-1") for k, v in headers if k.lower() == b"authorization"), "")
        presented = _bearer(raw_auth)
        method = str(scope.get("method") or "GET").upper()

        identity: accounts.AdminIdentity | None = None
        if presented.startswith(accounts.SESSION_PREFIX):
            identity = await anyio.to_thread.run_sync(accounts.validate_session, self.db, presented)
            if identity is None:
                await self._reject(send, 401, "your admin session has ended; sign in again")
                return
        elif presented and env_token_matches(presented, self.settings.admin_token):
            if not accounts.bootstrap_token_api_enabled():
                await self._reject(send, 401, "the bootstrap admin token is disabled; sign in with an administrator account")
                return
            identity = accounts.BOOTSTRAP_IDENTITY
        else:
            await self.app(scope, receive, send)  # existing checks answer 401
            return

        if method not in _READ_METHODS and not identity.can_write():
            await self._reject(send, 403, "your role is read-only")
            return
        if path.startswith("/v1/admin/accounts") and not identity.is_owner():
            await self._reject(send, 403, "only owners can manage administrators")
            return

        if identity is not accounts.BOOTSTRAP_IDENTITY:
            # Present the request to the existing admin checks.
            internal = f"Bearer {self.settings.admin_token}".encode("latin-1")
            headers = [(k, v) for k, v in headers if k.lower() != b"authorization"] + [(b"authorization", internal)]
            scope = {**scope, "headers": headers}
        token = accounts.CURRENT_ADMIN.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            accounts.CURRENT_ADMIN.reset(token)


# --- request models -------------------------------------------------------------------------

class BootstrapRequest(BaseModel):
    bootstrap_token: str
    email: str
    display_name: str = ""


class SetupRequest(BaseModel):
    setup_token: str
    password: str = ""
    totp_code: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str


class AdminCreateRequest(BaseModel):
    email: str
    display_name: str = ""
    role: str = "admin"


class AdminUpdateRequest(BaseModel):
    role: str | None = None
    disabled: bool | None = None


class EmployeeRequest(BaseModel):
    email: str
    display_name: str = ""
    teams: list[str] | None = None


class ImportRequest(BaseModel):
    csv: str


class InviteRequest(BaseModel):
    organization_name: str = ""
    gateway_url: str = ""
    expires_days: int = 7
    max_devices: int = 2
    require_sso: bool | None = None


class LinkRequest(BaseModel):
    employee_id: str
    reattribute_history: bool = False


class IdentitySettingsRequest(BaseModel):
    require_verified_identity: bool


class CodeRequest(BaseModel):
    code: str


class SSOStartRequest(BaseModel):
    purpose: str
    organization_id: str = ""
    invite_token: str = ""


# --- installer ------------------------------------------------------------------------------------

def _page(name: str) -> Response:
    nonce = secrets.token_urlsafe(18)
    html = (_HERE / name).read_text(encoding="utf-8").replace("__NONCE__", nonce)
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return Response(content=html, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": csp, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    })


def _take(app: FastAPI, path: str, method: str) -> Callable[..., Any] | None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or set()):
            app.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def install_identity(
    app: FastAPI,
    *,
    db: GatewayDB,
    settings: GatewaySettings,
    sso_settings: SSOSettings | None = None,
    http_factory: Callable[[], Any] | None = None,
) -> None:
    if getattr(app.state, "owg_identity_installed", False):
        return
    sso_settings = sso_settings or SSOSettings.from_env()
    sso = SSOClient(sso_settings, db, **({"http_factory": http_factory} if http_factory else {}))
    app.state.sso_client = sso

    def init_schemas() -> None:
        accounts.init_admin_schema(db)
        roster.init_employee_schema(db)
        me_access.init_me_schema(db)
        init_sso_schema(db)

    # Like the other Gateway modules: tables are created at server startup, never at import.
    extend_lifespan(app, startup=init_schemas)

    def require_admin(authorization: str | None) -> accounts.AdminIdentity:
        if not env_token_matches(_bearer(authorization), settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway administrator sign-in required")
        return accounts.current_admin() or accounts.BOOTSTRAP_IDENTITY

    def base_url(request: Request) -> str:
        return (sso_settings.public_url or os.getenv("OWG_GATEWAY_PUBLIC_URL", "") or str(request.base_url)).rstrip("/")

    def audit(org: str, action: str, details: dict[str, Any]) -> None:
        db.audit(organization_id=org or "*", principal_id="gateway-admin", action=action, details=details)

    # ---------------- admin sign-in -------------------------------------------------------------

    @app.get("/v1/admin/auth/state")
    def auth_state() -> dict[str, Any]:
        return {
            "needs_bootstrap": accounts.admin_count(db) == 0,
            "sso_enabled": sso_settings.enabled,
            "bootstrap_token_api_enabled": accounts.bootstrap_token_api_enabled(),
        }

    @app.post("/v1/admin/auth/bootstrap")
    def auth_bootstrap(body: BootstrapRequest, request: Request) -> dict[str, Any]:
        if not env_token_matches(body.bootstrap_token.strip(), settings.admin_token):
            raise HTTPException(status_code=401, detail="the bootstrap token is not correct")
        try:
            created = accounts.bootstrap_first_owner(db, email=body.email, display_name=body.display_name)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc
        audit("*", "admin.account.bootstrapped", {"email": created["email"], "role": "owner"})
        return {**created, "setup_url": f"{base_url(request)}/admin#setup={created['setup_token']}"}

    @app.post("/v1/admin/auth/setup/start")
    def setup_start(body: SetupRequest) -> dict[str, Any]:
        try:
            return accounts.start_setup(db, body.setup_token)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc

    @app.post("/v1/admin/auth/setup/complete")
    def setup_complete(body: SetupRequest) -> dict[str, Any]:
        try:
            admin, session = accounts.complete_setup(db, body.setup_token, password=body.password, totp_code=body.totp_code)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc
        db.audit(organization_id="*", principal_id=f"admin:{admin['email']}", action="admin.account.activated", details={"email": admin["email"]})
        return {"session_token": session, "admin": admin}

    @app.post("/v1/admin/auth/login")
    def auth_login(body: LoginRequest, request: Request) -> dict[str, Any]:
        source = request.client.host if request.client else ""
        try:
            identity, session = accounts.login(db, email=body.email, password=body.password, totp_code=body.totp_code, source=source)
        except accounts.AdminAuthError as exc:
            db.audit(organization_id="*", principal_id="anonymous", action="admin.login.failed", details={"email": str(body.email)[:120].lower()})
            raise _error(exc) from exc
        db.audit(organization_id="*", principal_id=identity.audit_id, action="admin.login", details={"method": "password_totp"})
        return {"session_token": session, "admin": dataclasses.asdict(identity)}

    @app.post("/v1/admin/auth/logout")
    def auth_logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        if token.startswith(accounts.SESSION_PREFIX):
            accounts.revoke_session(db, token)
        return {"signed_out": True}

    @app.get("/v1/admin/auth/me")
    def auth_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        if token.startswith(accounts.SESSION_PREFIX):
            identity = accounts.validate_session(db, token)
        elif token and env_token_matches(token, settings.admin_token) and accounts.bootstrap_token_api_enabled():
            identity = accounts.BOOTSTRAP_IDENTITY
        else:
            identity = None
        if identity is None:
            raise HTTPException(status_code=401, detail="not signed in")
        return {**dataclasses.asdict(identity), "can_write": identity.can_write(), "is_owner": identity.is_owner()}

    # ---------------- administrator accounts (owners) ---------------------------------------------

    @app.get("/v1/admin/accounts")
    def admins_list(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return {"items": accounts.list_admins(db), "roles": list(accounts.ROLES)}

    @app.post("/v1/admin/accounts")
    def admins_create(body: AdminCreateRequest, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        actor = require_admin(authorization)
        try:
            created = accounts.create_admin(db, email=body.email, display_name=body.display_name, role=body.role, created_by=actor.audit_id)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc
        audit("*", "admin.account.created", {"email": created["email"], "role": created["role"]})
        return {**created, "setup_url": f"{base_url(request)}/admin#setup={created['setup_token']}"}

    @app.patch("/v1/admin/accounts/{admin_id}")
    def admins_update(admin_id: str, body: AdminUpdateRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        actor = require_admin(authorization)
        if admin_id == actor.admin_id and (body.disabled or (body.role and body.role != "owner")):
            raise HTTPException(status_code=409, detail="you cannot disable or demote yourself; ask another owner")
        try:
            updated = accounts.update_admin(db, admin_id, role=body.role, disabled=body.disabled)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc
        audit("*", "admin.account.updated", {"email": updated["email"], "role": updated["role"], "status": updated["status"]})
        return updated

    @app.post("/v1/admin/accounts/{admin_id}/reset")
    def admins_reset(admin_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        actor = require_admin(authorization)
        if admin_id == actor.admin_id:
            raise HTTPException(status_code=409, detail="ask another owner to reset your sign-in")
        try:
            reset = accounts.reset_admin(db, admin_id)
        except accounts.AdminAuthError as exc:
            raise _error(exc) from exc
        audit("*", "admin.account.reset", {"email": reset["email"]})
        return {**reset, "setup_url": f"{base_url(request)}/admin#setup={reset['setup_token']}"}

    # ---------------- roster --------------------------------------------------------------------------

    @app.get("/v1/admin/people/{organization_id}")
    def people(organization_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            return {**roster.people_and_devices(db, organization_id), "sso_enabled": sso_settings.enabled}
        except roster.EmployeeError as exc:
            raise _error(exc) from exc

    @app.post("/v1/admin/employees/{organization_id}")
    def employee_upsert(organization_id: str, body: EmployeeRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            employee, created = roster.upsert_employee(db, organization_id, email=body.email, display_name=body.display_name, teams=body.teams)
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        audit(organization_id, "employee.created" if created else "employee.updated", {"employee_actor_id": employee["actor_id"], "teams": body.teams})
        return {**employee, "created": created}

    @app.post("/v1/admin/employees/{organization_id}/import")
    def employee_import(organization_id: str, body: ImportRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            result = roster.import_csv(db, organization_id, body.csv)
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        # One row per person, so each employee's /me log shows how they got on the roster.
        for change in result.pop("changed"):
            audit(organization_id, "employee.created" if change["created"] else "employee.updated",
                  {"employee_actor_id": change["actor_id"], "via": "csv_import"})
        audit(organization_id, "employee.imported", {k: result[k] for k in ("created", "updated", "error_count")})
        return result

    @app.post("/v1/admin/employees/{organization_id}/{employee_id}/offboard")
    def employee_offboard(organization_id: str, employee_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            result = roster.offboard_employee(db, organization_id, employee_id)
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        audit(organization_id, "employee.offboarded", {"employee_actor_id": result["actor_id"], "revoked_devices": result["revoked_devices"]})
        return result

    @app.post("/v1/admin/employees/{organization_id}/{employee_id}/invites")
    def employee_invite(organization_id: str, employee_id: str, body: InviteRequest, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        actor = require_admin(authorization)
        require_sso = sso_settings.enabled if body.require_sso is None else bool(body.require_sso)
        if require_sso and not sso_settings.enabled:
            raise HTTPException(status_code=400, detail="company sign-in (SSO) is not configured on this Gateway")
        try:
            invite = roster.create_personal_invite(
                db, organization_id, employee_id,
                organization_name=body.organization_name, expires_days=body.expires_days,
                max_devices=body.max_devices, require_sso=require_sso, created_by=actor.audit_id,
            )
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        audit(organization_id, "employee.invite.created", {
            "employee_actor_id": invite["employee"]["actor_id"], "invite_id": invite["invite_id"],
            "require_sso": invite["require_sso"], "expires_at": invite["expires_at"],
        })
        from shared.join_code import encode_join_code
        # The address employees use: what the admin's browser uses, else the configured
        # public URL. Behind a TLS-terminating proxy the request itself looks like http.
        gateway = (body.gateway_url.strip() or sso_settings.public_url or os.getenv("OWG_GATEWAY_PUBLIC_URL", "") or base_url(request)).rstrip("/")
        try:
            invite["join_code"] = encode_join_code(
                gateway_url=gateway, organization_id=organization_id, token=invite["token"],
                organization_name=body.organization_name or organization_id,
            )
        except ValueError as exc:
            roster.revoke_personal_invite(db, organization_id, invite["invite_id"])
            raise HTTPException(
                status_code=400,
                detail=f"{exc}. Open the admin console at the https address employees use, or set OWG_GATEWAY_PUBLIC_URL.",
            ) from exc
        invite["verify_url"] = f"{gateway}/join/verify#code={quote(invite['join_code'])}" if invite["require_sso"] else None
        return invite

    @app.get("/v1/admin/personal-invites/{organization_id}")
    def invites_list(organization_id: str, employee_id: str = "", authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            return {"items": roster.list_personal_invites(db, organization_id, employee_id=employee_id)}
        except roster.EmployeeError as exc:
            raise _error(exc) from exc

    @app.delete("/v1/admin/personal-invites/{organization_id}/{invite_id}")
    def invites_revoke(organization_id: str, invite_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        if not roster.revoke_personal_invite(db, organization_id, invite_id):
            raise HTTPException(status_code=404, detail="invitation not found or already revoked")
        audit(organization_id, "employee.invite.revoked", {"invite_id": invite_id})
        return {"invite_id": invite_id, "revoked": True}

    @app.post("/v1/admin/devices/{organization_id}/{device_id}/link")
    def device_link(organization_id: str, device_id: str, body: LinkRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        actor = require_admin(authorization)
        try:
            result = roster.link_device(db, organization_id, device_id, body.employee_id,
                                        reattribute_history=body.reattribute_history, linked_by=actor.audit_id)
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        audit(organization_id, "device.linked_to_employee", {
            "device_id": device_id, "employee_actor_id": result["employee"]["actor_id"],
            "previous_actor_ids": result["previous_actor_ids"], "reattributed_events": result["reattributed_events"],
        })
        return result

    @app.get("/v1/admin/identity-settings/{organization_id}")
    def identity_get(organization_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return {
            **roster.org_settings(db, organization_id),
            "sso": {"enabled": sso_settings.enabled, "issuer": sso_settings.issuer or None,
                    "redirect_uri": sso_settings.redirect_uri if sso_settings.enabled else None,
                    "allowed_domains": list(sso_settings.allowed_domains)},
            "bootstrap_token_api_enabled": accounts.bootstrap_token_api_enabled(),
            "public_url": sso_settings.public_url or os.getenv("OWG_GATEWAY_PUBLIC_URL", "") or None,
        }

    @app.put("/v1/admin/identity-settings/{organization_id}")
    def identity_put(organization_id: str, body: IdentitySettingsRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        try:
            result = roster.set_org_settings(db, organization_id, require_verified_identity=body.require_verified_identity)
        except roster.EmployeeError as exc:
            raise _error(exc) from exc
        audit(organization_id, "identity.settings.updated", {"require_verified_identity": body.require_verified_identity})
        return result

    # ---------------- enrollment with identity -----------------------------------------------------

    previous_enroll = _take(app, "/v1/devices/enroll", "POST")
    previous_preview = _take(app, "/v1/devices/join-preview", "GET")
    if previous_enroll is None or previous_preview is None:
        raise RuntimeError("install the organization admin console before identity")

    @app.get("/v1/devices/join-preview")
    def join_preview(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        if token.startswith(roster.PERSON_TOKEN_PREFIX + "_"):
            invite = roster.peek_personal_invite(db, token)
            if invite is None:
                raise HTTPException(status_code=401, detail="this invitation is invalid, expired, revoked or already used; ask your IT admin for a new one")
            policy = db.get_policy(invite["organization_id"])
            return {
                "organization_id": invite["organization_id"],
                "organization_name": invite["organization_name"],
                "expires_at": invite["expires_at"],
                "seats_left": invite["devices_left"],
                "identity": {
                    "locked": True, "email": invite["email"], "display_name": invite["display_name"],
                    "actor_id": invite["actor_id"], "require_sso": invite["require_sso"],
                    "sso_verified": invite["sso_verified"],
                    "verify_path": "/join/verify" if invite["require_sso"] else None,
                },
                "sharing": _sharing(policy),
                "never_shared": _NEVER_SHARED,
                "you_can": _YOU_CAN,
            }
        preview = previous_preview(authorization)
        verified_only = roster.org_settings(db, preview["organization_id"])["require_verified_identity"]
        if verified_only:
            raise HTTPException(status_code=403, detail="your organization requires a personal invitation; ask your IT admin for one")
        preview["identity"] = {"locked": False, "require_sso": False}
        return preview

    def _enrollment_organization(token: str) -> str:
        """The organization an enrollment code is bound to, without consuming it."""
        if token.startswith("owg_enroll_link_"):
            link = peek_enrollment_link(db, token)
            return str(link["organization_id"]) if link else ""
        with db.connect() as conn:
            cur = db._execute(conn, "SELECT organization_id FROM enrollment_grants WHERE token_hash = ?", (token_hash(token),))
            row = db._row(cur.fetchone(), [d[0] for d in cur.description] if cur.description else None)
        return str(row["organization_id"]) if row else ""  # the legacy shared token is not bound

    @app.post("/v1/devices/enroll")
    def enroll(request: EnrollmentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        if token.startswith(roster.PERSON_TOKEN_PREFIX + "_"):
            try:
                result = roster.enroll_with_personal_invite(
                    db, token=token, requested_organization_id=request.organization_id, device_id=request.device_id,
                )
            except roster.EmployeeError as exc:
                raise _error(exc) from exc
            db.audit(
                organization_id=result["organization_id"], principal_id=result["token_id"], action="device.enrolled",
                details={"device_id": result["device_id"], "invite_id": result["invite_id"], "identity_source": result["identity_source"],
                         "employee_actor_id": result["actor_id"], "personal_invite": True},
            )
            return {k: v for k, v in result.items() if k != "invite_id"}
        # Decide by the organization the code is bound to: a client could leave
        # organization_id empty, and bound codes then take it from the code.
        org = _enrollment_organization(token) or str(request.organization_id or "").strip()
        if org and roster.org_settings(db, org)["require_verified_identity"]:
            raise HTTPException(status_code=403, detail="your organization requires a personal invitation; ask your IT admin for one")
        result = previous_enroll(request, authorization)
        try:
            roster.record_self_reported(db, result["organization_id"], result["device_id"], linked_by="self_reported_at_enrollment")
        except Exception:
            pass
        result["identity_source"] = "self_reported"
        return result

    # ---------------- employee self-service (/me) -------------------------------------------------------

    @app.post("/v1/devices/me-link")
    def me_link(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        principal = db.authenticate(_bearer(authorization))
        if principal is None or principal.token_type != "device":
            raise HTTPException(status_code=401, detail="a connected OpenWorkGraph computer is required")
        try:
            code = me_access.create_login_code(db, organization_id=principal.organization_id, actor_id=principal.actor_id, device_id=principal.device_id)
        except me_access.MeError as exc:
            raise _error(exc) from exc
        # The computer builds the link from the Gateway address it enrolled with.
        return {"code": code, "path": f"/me#code={code}", "expires_in_seconds": me_access.CODE_SECONDS}

    @app.post("/v1/me/session")
    def me_session(body: CodeRequest) -> dict[str, Any]:
        try:
            session, who = me_access.exchange_login_code(db, body.code)
        except me_access.MeError as exc:
            raise _error(exc) from exc
        return {"session_token": session, **who}

    def me(authorization: str | None) -> dict[str, Any]:
        who = me_access.validate_session(db, _bearer(authorization))
        if who is None:
            raise HTTPException(status_code=401, detail="your session has ended; open the page again from OpenWorkGraph")
        return who

    @app.get("/v1/me/overview")
    def me_overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        who = me(authorization)
        return {**me_access.overview(db, organization_id=who["organization_id"], actor_id=who["actor_id"]), "signed_in_with": who["auth_method"]}

    @app.get("/v1/me/evidence")
    def me_evidence(
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        from .query import workflow_trace

        who = me(authorization)
        try:
            payload = workflow_trace(db, organization_id=who["organization_id"], actor_id=who["actor_id"], cursor=cursor, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=who["organization_id"], principal_id=f"employee:{who['actor_id']}", action="human_access.trace.read",
            details={"actor_id": who["actor_id"], "access_mode": "self", "returned": payload.get("returned", 0), "via": "me_page"},
        )
        return payload

    @app.get("/v1/me/access-log")
    def me_access_log(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        who = me(authorization)
        return {"items": me_access.access_log(db, organization_id=who["organization_id"], actor_id=who["actor_id"])}

    @app.post("/v1/me/logout")
    def me_logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        if token.startswith(me_access.SESSION_PREFIX):
            me_access.revoke_session(db, token)
        return {"signed_out": True}

    # ---------------- company sign-in --------------------------------------------------------------------

    @app.post("/v1/sso/start")
    def sso_start(body: SSOStartRequest) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if body.purpose == "join":
            invite = roster.peek_personal_invite(db, body.invite_token.strip())
            if invite is None:
                raise HTTPException(status_code=401, detail="this invitation is invalid, expired, revoked or already used")
            context = {"invite_id": invite["invite_id"]}
        elif body.purpose == "me" and body.organization_id:
            context = {"organization_id": body.organization_id.strip()[:512]}
        try:
            return {"redirect_url": sso.start(body.purpose, context)}
        except SSOError as exc:
            raise _error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="the company sign-in provider could not be reached") from exc

    @app.get("/sso/callback", include_in_schema=False)
    def sso_callback(code: str = "", state: str = "", error: str = "", error_description: str = "") -> Response:
        if error:
            return RedirectResponse("/?error=" + quote((error_description or error)[:200]), status_code=303)
        try:
            purpose, context, identity = sso.finish(code=code, state=state)
        except SSOError as exc:
            return RedirectResponse("/?error=" + quote(str(exc)), status_code=303)
        except Exception:
            return RedirectResponse("/?error=" + quote("company sign-in failed; try again"), status_code=303)
        try:
            if purpose == "admin":
                admin, session = accounts.login_sso(db, email=identity["email"])
                db.audit(organization_id="*", principal_id=admin.audit_id, action="admin.login", details={"method": "sso"})
                return RedirectResponse(f"/admin#session={session}", status_code=303)
            if purpose == "me":
                session, _who = me_access.sso_session(db, email=identity["email"], organization_id=str(context.get("organization_id") or ""))
                return RedirectResponse(f"/me#session={session}", status_code=303)
            if purpose == "join":
                invite = roster.mark_invite_verified_by_id(db, str(context.get("invite_id") or ""), verified_email=identity["email"], subject=identity["subject"])
                db.audit(organization_id=invite["organization_id"], principal_id=f"employee:{invite['actor_id']}", action="employee.invite.sso_verified",
                         details={"invite_id": invite["invite_id"], "employee_actor_id": invite["actor_id"]})
                return RedirectResponse("/join/verify#verified=" + quote(invite["email"]), status_code=303)
        except (accounts.AdminAuthError, me_access.MeError, roster.EmployeeError) as exc:
            target = {"admin": "/admin", "me": "/me", "join": "/join/verify"}.get(purpose, "/")
            return RedirectResponse(f"{target}#error=" + quote(str(exc)), status_code=303)
        return RedirectResponse("/", status_code=303)

    # ---------------- pages ---------------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def landing() -> Response:
        return _page("landing.html")

    @app.get("/me", include_in_schema=False)
    def me_page() -> Response:
        return _page("me_page.html")

    @app.get("/join/verify", include_in_schema=False)
    def join_verify_page() -> Response:
        return _page("join_verify.html")

    app.add_middleware(AdminIdentityMiddleware, db=db, settings=settings)
    app.state.owg_identity_installed = True


_NEVER_SHARED = [
    "typed text", "clipboard contents", "screenshots", "passwords",
    "evidence recorded before you join", "anything recorded while you pause sharing",
]
_YOU_CAN = ["pause sharing at any time", "disconnect at any time", "see exactly what was shared in your dashboard"]


def _sharing(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "shares_window_titles": bool(policy.get("share_window_titles", True)),
        "shares_metadata": bool(policy.get("share_metadata", True)),
        "shares_excluded_apps": bool(policy.get("share_excluded", False)),
        "shares_agent_activity": bool(policy.get("allow_agent_events", False)),
        "limited_to_event_types": list(policy.get("allowed_event_types") or []),
        "forces_redacted_ai_context": bool(policy.get("force_redacted_ai_context", False)),
    }


class _RosterMappingVerifier:
    """Map an SSO identity (sub or email) to the roster employee's actor id."""

    def __init__(self, inner: Any, settings: Any, db: GatewayDB) -> None:
        self.inner = inner
        self.settings = settings
        self.db = db

    def verify(self, token: str) -> dict[str, Any]:
        claims = dict(self.inner.verify(token))
        org = _claim(claims, self.settings.organization_claim)
        actor = str(_claim(claims, self.settings.actor_claim) or "").strip()
        if not org or not actor:
            return claims
        with self.db.connect() as conn:
            cur = self.db._execute(
                conn,
                "SELECT actor_id FROM gateway_employees WHERE organization_id = ? AND status = 'active' AND (sso_subject = ? OR email = ?)",
                (str(org), actor, actor.lower()),
            )
            row = cur.fetchone()
        if row:
            mapped = row[0] if not hasattr(row, "keys") else row["actor_id"]
            _set_claim(claims, self.settings.actor_claim, str(mapped))
        return claims


def _claim(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in [x for x in str(path).split(".") if x]:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _set_claim(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = [x for x in str(path).split(".") if x]
    target = payload
    for part in parts[:-1]:
        nxt = target.get(part)
        if not isinstance(nxt, dict):
            return
        target = nxt
    if parts:
        target[parts[-1]] = value


def install_roster_mapping(app: FastAPI, db: GatewayDB) -> None:
    """Map SSO identities in the human API to roster employees (call after human access)."""
    verifier = getattr(app.state, "human_oidc_verifier", None)
    human_settings = getattr(app.state, "human_access_settings", None)
    if verifier is not None and human_settings is not None and not isinstance(verifier, _RosterMappingVerifier):
        app.state.human_oidc_verifier = _RosterMappingVerifier(verifier, human_settings, db)


__all__ = ["install_identity", "install_roster_mapping", "AdminIdentityMiddleware"]
