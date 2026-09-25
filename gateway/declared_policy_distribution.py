from __future__ import annotations

"""Gateway transport for signed enterprise declared-policy bundles."""

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from shared.policy_bundle import PolicyBundleError

from .auth import env_token_matches
from .db import GatewayDB
from .declared_policy_store import (
    declared_policy_history,
    init_declared_policy_store,
    latest_declared_policy_bundle,
    publish_declared_policy_bundle,
)
from .settings import GatewaySettings


class SignedDeclaredPolicyRequest(BaseModel):
    bundle: dict[str, Any] = Field(default_factory=dict)


def _bearer(value: str | None) -> str:
    raw = str(value or "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def install_declared_policy_distribution(
    app: FastAPI,
    *,
    db: GatewayDB,
    settings: GatewaySettings,
) -> None:
    """Install additive signed-policy publication and device read endpoints."""

    @app.on_event("startup")
    def init_signed_policy_distribution() -> None:
        init_declared_policy_store(db)

    def require_admin(authorization: str | None) -> None:
        if not env_token_matches(_bearer(authorization), settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway admin token required")

    def require_device_policy_reader(authorization: str | None):
        principal = db.authenticate(_bearer(authorization))
        if principal is None:
            raise HTTPException(status_code=401, detail="valid OpenWorkGraph Gateway device token required")
        if principal.token_type != "device":
            raise HTTPException(status_code=403, detail="only enrolled device tokens may read managed declared policy")
        try:
            principal.require("policy:read")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return principal

    @app.put("/v1/admin/declared-policy/{organization_id}")
    def publish_declared_policy(
        organization_id: str,
        request: SignedDeclaredPolicyRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        organization_id = str(organization_id or "").strip()
        if not organization_id or len(organization_id) > 512:
            raise HTTPException(status_code=400, detail="organization_id must be 1-512 characters")
        try:
            stored = publish_declared_policy_bundle(
                db,
                organization_id=organization_id,
                bundle=request.bundle,
            )
        except PolicyBundleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.audit(
            organization_id=organization_id,
            principal_id="gateway-admin",
            action="declared_policy.bundle.published",
            details={
                "policy_revision": stored["policy_revision"],
                "bundle_sha256": stored["bundle_sha256"],
                "manifest_sha256": stored["manifest_sha256"],
                "key_id": stored["key_id"],
                "idempotent": bool(stored.get("idempotent")),
                "signature_verified_by_gateway": False,
            },
        )
        return {
            "organization_id": organization_id,
            "policy_revision": stored["policy_revision"],
            "bundle_sha256": stored["bundle_sha256"],
            "manifest_sha256": stored["manifest_sha256"],
            "key_id": stored["key_id"],
            "published_at": stored["published_at"],
            "idempotent": bool(stored.get("idempotent")),
            "signature_verified_by_gateway": False,
            "endpoint_signature_verification_required": True,
            "private_signing_key_stored_by_gateway": False,
        }

    @app.get("/v1/admin/declared-policy/{organization_id}/history")
    def declared_policy_admin_history(
        organization_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        items = declared_policy_history(db, str(organization_id or "").strip(), limit=limit)
        return {
            "organization_id": organization_id,
            "items": [
                {
                    "policy_revision": item["policy_revision"],
                    "bundle_sha256": item["bundle_sha256"],
                    "manifest_sha256": item["manifest_sha256"],
                    "key_id": item["key_id"],
                    "published_at": item["published_at"],
                }
                for item in items
            ],
            "bundles_returned": False,
        }

    @app.get("/v1/device-declared-policy")
    def device_declared_policy(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        principal = require_device_policy_reader(authorization)
        stored = latest_declared_policy_bundle(db, principal.organization_id)
        db.audit(
            organization_id=principal.organization_id,
            principal_id=principal.token_id,
            action="declared_policy.bundle.read",
            details={
                "present": bool(stored),
                "policy_revision": int(stored["policy_revision"]) if stored else None,
                "bundle_sha256": str(stored["bundle_sha256"]) if stored else None,
            },
        )
        if stored is None:
            return {
                "organization_id": principal.organization_id,
                "present": False,
                "bundle": None,
                "gateway_signature_verification": False,
                "endpoint_signature_verification_required": True,
            }
        return {
            "organization_id": principal.organization_id,
            "present": True,
            "policy_revision": stored["policy_revision"],
            "bundle_sha256": stored["bundle_sha256"],
            "manifest_sha256": stored["manifest_sha256"],
            "key_id": stored["key_id"],
            "published_at": stored["published_at"],
            "bundle": stored["bundle"],
            "gateway_signature_verification": False,
            "endpoint_signature_verification_required": True,
            "private_signing_key_stored_by_gateway": False,
        }
