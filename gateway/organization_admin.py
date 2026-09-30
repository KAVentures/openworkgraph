from __future__ import annotations

"""Organization-admin UI and reusable enrollment routes for enterprise Gateway.

Installed additively by the enterprise Gateway. The existing core Gateway stays
usable on its own; enterprise deployments gain the admin console and employee
join flow without changing the canonical evidence/query contracts.
"""

from pathlib import Path
import secrets
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from .app import EnrollmentRequest
from .auth import env_token_matches
from .db import GatewayDB
from .enrollment_links import (
    EnrollmentLinkError,
    create_enrollment_link,
    device_activity,
    enroll_device_with_link,
    init_enrollment_links_schema,
    list_enrollment_links,
    peek_enrollment_link,
    revoke_enrollment_link,
)
from .settings import GatewaySettings


class EnrollmentLinkRequest(BaseModel):
    organization_id: str
    organization_name: str = ""
    max_uses: int = 50
    expires_days: int = 14
    label: str = ""


def _bearer(value: str | None) -> str:
    raw = str(value or "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def _take_endpoint(app: FastAPI, path: str, method: str) -> Callable[..., Any] | None:
    target = method.upper()
    for route in list(app.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and target in methods:
            app.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def install_organization_admin(
    app: FastAPI,
    *,
    db: GatewayDB,
    settings: GatewaySettings,
) -> None:
    if getattr(app.state, "owg_organization_admin_installed", False):
        return

    original_enroll = _take_endpoint(app, "/v1/devices/enroll", "POST")
    if original_enroll is None:
        raise RuntimeError("OpenWorkGraph core enrollment route is missing")

    def require_admin(authorization: str | None) -> None:
        if not env_token_matches(_bearer(authorization), settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway admin token required")

    @app.on_event("startup")
    def init_links() -> None:
        init_enrollment_links_schema(db)

    @app.post("/v1/admin/enrollment-links")
    def create_link(
        request: EnrollmentLinkRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        try:
            link = create_enrollment_link(
                db,
                organization_id=request.organization_id,
                organization_name=request.organization_name,
                max_uses=request.max_uses,
                expires_days=request.expires_days,
                label=request.label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=link["organization_id"],
            principal_id="gateway-admin",
            action="device.enrollment_link.created",
            details={
                "grant_id": link["grant_id"],
                "max_uses": link["max_uses"],
                "expires_at": link["expires_at"],
                "label": link["label"],
            },
        )
        return link

    @app.get("/v1/admin/enrollment-links/{organization_id}")
    def get_links(
        organization_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = str(organization_id or "").strip()
        if not org or len(org) > 512:
            raise HTTPException(status_code=400, detail="organization_id must be 1-512 characters")
        return {"organization_id": org, "items": list_enrollment_links(db, organization_id=org)}

    @app.delete("/v1/admin/enrollment-links/{grant_id}")
    def delete_link(
        grant_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        result = revoke_enrollment_link(db, grant_id=grant_id)
        if result is None:
            raise HTTPException(status_code=404, detail="enrollment link not found")
        db.audit(
            organization_id=result["organization_id"],
            principal_id="gateway-admin",
            action="device.enrollment_link.revoked",
            details={"grant_id": grant_id},
        )
        return result

    @app.get("/v1/admin/device-activity/{organization_id}")
    def get_device_activity(
        organization_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = str(organization_id or "").strip()
        if not org or len(org) > 512:
            raise HTTPException(status_code=400, detail="organization_id must be 1-512 characters")
        return {"organization_id": org, "devices": device_activity(db, organization_id=org)}

    @app.get("/v1/devices/join-preview")
    def join_preview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Show exactly what an endpoint would share without consuming a seat."""
        link = peek_enrollment_link(db, _bearer(authorization))
        if link is None:
            raise HTTPException(
                status_code=401,
                detail="this join code is invalid, expired, revoked or used up; ask your IT admin for a new one",
            )
        policy = db.get_policy(link["organization_id"])
        return {
            "organization_id": link["organization_id"],
            "organization_name": link["organization_name"],
            "expires_at": link["expires_at"],
            "seats_left": link["seats_left"],
            "sharing": {
                "shares_window_titles": bool(policy.get("share_window_titles", True)),
                "shares_metadata": bool(policy.get("share_metadata", True)),
                "shares_excluded_apps": bool(policy.get("share_excluded", False)),
                "shares_agent_activity": bool(policy.get("allow_agent_events", False)),
                "shares_agent_session_messages": bool(policy.get("allow_agent_session_messages", False)),
                "limited_to_event_types": list(policy.get("allowed_event_types") or []),
                "forces_redacted_ai_context": bool(policy.get("force_redacted_ai_context", False)),
            },
            "never_shared": [
                "typed text",
                "clipboard contents",
                "screenshots",
                "passwords",
                "evidence recorded before you join",
                "anything recorded while you pause sharing",
            ],
            "you_can": [
                "pause sharing at any time",
                "disconnect at any time",
                "see exactly what was shared in your dashboard",
            ],
        }

    @app.post("/v1/devices/enroll")
    def enroll_device(
        request: EnrollmentRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        presented = _bearer(authorization)
        if not presented.startswith("owg_enroll_link_"):
            return original_enroll(request, authorization)
        try:
            result = enroll_device_with_link(
                db,
                enrollment_token=presented,
                requested_organization_id=request.organization_id,
                actor_id=request.actor_id,
                device_id=request.device_id,
            )
        except EnrollmentLinkError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        db.audit(
            organization_id=result["organization_id"],
            principal_id=result["token_id"],
            action="device.enrolled",
            details={
                "device_id": result["device_id"],
                "grant_id": result["grant_id"],
                "legacy_shared_secret": False,
                "reusable_organization_link": True,
            },
        )
        return result

    @app.get("/v1/admin/policy/{organization_id}")
    def read_policy(
        organization_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = str(organization_id or "").strip()
        if not org or len(org) > 512:
            raise HTTPException(status_code=400, detail="organization_id must be 1-512 characters")
        return {"organization_id": org, "policy": db.get_policy(org)}

    @app.get("/admin", include_in_schema=False)
    def admin_console() -> Response:
        """Public shell only; every data/action request still needs admin auth."""
        nonce = secrets.token_urlsafe(18)
        html = Path(__file__).with_name("admin_console.html").read_text(encoding="utf-8").replace("__NONCE__", nonce)
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; "
            "style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        return Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": csp,
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    app.state.owg_organization_admin_installed = True


__all__ = ["install_organization_admin"]
