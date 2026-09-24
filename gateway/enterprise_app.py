from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .app import create_app as create_core_app
from .auth import env_token_matches
from .hardening import (
    HardeningSettings,
    PooledGatewayDB,
    SlidingWindowRateLimiter,
    bearer_fingerprint,
)
from .settings import PRODUCT_VERSION, GatewaySettings

GATEWAY_VERSION = PRODUCT_VERSION


def _take_get_endpoint(app: FastAPI, path: str) -> Callable[..., Any] | None:
    for route in list(app.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and "GET" in methods:
            app.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def _identifier(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 512:
        raise HTTPException(status_code=400, detail=f"{field} must be 1-512 characters")
    return clean


def create_enterprise_app(
    *,
    settings: GatewaySettings | None = None,
    hardening: HardeningSettings | None = None,
    db: PooledGatewayDB | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    hardening = hardening or HardeningSettings.from_env()
    db = db or PooledGatewayDB(
        settings.database_url,
        pool_min_size=hardening.postgres_pool_min_size,
        pool_max_size=hardening.postgres_pool_max_size,
        pool_timeout_seconds=hardening.postgres_pool_timeout_seconds,
    )

    app = create_core_app(settings=settings, db=db)
    app.version = GATEWAY_VERSION
    app.state.hardening_settings = hardening
    app.state.rate_limiter = SlidingWindowRateLimiter(window_seconds=60)

    core_health = _take_get_endpoint(app, "/health")
    core_capabilities = _take_get_endpoint(app, "/v1/capabilities")

    @app.get("/health")
    def health() -> dict[str, Any]:
        payload = core_health() if core_health else {"status": "ok", "server": "OpenWorkGraph Gateway"}
        payload = dict(payload)
        payload["version"] = GATEWAY_VERSION
        payload["database_pooling"] = bool(db.pooling_enabled)
        return payload

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        payload = core_capabilities() if core_capabilities else {}
        payload = dict(payload)
        payload["operational_hardening"] = {
            "admin_credential_inventory": True,
            "admin_device_revocation": True,
            "credential_secrets_listed": False,
            "rate_limiting": "opt_in_default_disabled",
            "postgres_connection_pooling": "opt_in_default_disabled",
            "distributed_edge_rate_limit_required_for_multi_replica": True,
        }
        return payload

    @app.middleware("http")
    async def credential_rate_limit(request: Request, call_next):
        path = request.url.path
        if path in {"/health", "/v1/capabilities", "/openapi.json", "/docs", "/redoc"}:
            return await call_next(request)

        if path == "/v1/devices/enroll":
            bucket = "enrollment"
            limit = hardening.enrollment_rate_limit_per_minute
        elif path.startswith("/v1/admin/"):
            bucket = "admin"
            limit = hardening.admin_rate_limit_per_minute
        else:
            bucket = "principal"
            limit = hardening.principal_rate_limit_per_minute

        if limit > 0:
            credential = bearer_fingerprint(request.headers.get("authorization"))
            allowed, retry_after = app.state.rate_limiter.check(f"{bucket}:{credential}", limit)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "OpenWorkGraph Gateway rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    def require_admin(authorization: str | None) -> None:
        raw = str(authorization or "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        if not env_token_matches(token, settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway admin token required")

    @app.get("/v1/admin/runtime")
    def runtime_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return {
            "version": GATEWAY_VERSION,
            "storage": "postgresql" if db.is_postgres else "sqlite-development",
            "database_pooling": {
                "enabled": bool(db.pooling_enabled),
                "min_size": db.pool_min_size if db.pooling_enabled else 0,
                "max_size": db.pool_max_size if db.pooling_enabled else 0,
                "timeout_seconds": db.pool_timeout_seconds,
            },
            "rate_limits_per_minute": {
                "principal": hardening.principal_rate_limit_per_minute,
                "admin": hardening.admin_rate_limit_per_minute,
                "enrollment": hardening.enrollment_rate_limit_per_minute,
            },
            "rate_limit_scope": "single_gateway_process",
            "secrets_exposed": False,
        }

    @app.get("/v1/admin/tokens/{organization_id}")
    def list_tokens(
        organization_id: str,
        token_type: str | None = None,
        include_revoked: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = _identifier(organization_id, "organization_id")
        kind = str(token_type or "").strip() or None
        if kind not in (None, "device", "integration"):
            raise HTTPException(status_code=400, detail="token_type must be device or integration")
        items = db.list_token_metadata(
            organization_id=org,
            token_type=kind,
            include_revoked=include_revoked,
            limit=limit,
        )
        db.audit(
            organization_id=org,
            principal_id="gateway-admin",
            action="token.inventory.read",
            details={"token_type": kind or "all", "include_revoked": include_revoked, "returned": len(items)},
        )
        return {
            "organization_id": org,
            "items": items,
            "returned": len(items),
            "secrets_exposed": False,
        }

    @app.get("/v1/admin/devices/{organization_id}")
    def list_devices(
        organization_id: str,
        include_revoked: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = _identifier(organization_id, "organization_id")
        items = db.list_token_metadata(
            organization_id=org,
            token_type="device",
            include_revoked=include_revoked,
            limit=limit,
        )
        db.audit(
            organization_id=org,
            principal_id="gateway-admin",
            action="device.inventory.read",
            details={"include_revoked": include_revoked, "returned": len(items)},
        )
        return {
            "organization_id": org,
            "items": items,
            "returned": len(items),
            "secrets_exposed": False,
        }

    @app.delete("/v1/admin/devices/{organization_id}/{device_id}")
    def revoke_device(
        organization_id: str,
        device_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        org = _identifier(organization_id, "organization_id")
        device = _identifier(device_id, "device_id")
        revoked = db.revoke_device_tokens(organization_id=org, device_id=device)
        db.audit(
            organization_id=org,
            principal_id="gateway-admin",
            action="device.admin_revoked",
            details={"device_id": device, "revoked_credentials": revoked},
        )
        return {
            "organization_id": org,
            "device_id": device,
            "revoked_credentials": revoked,
            "evidence_deleted": False,
        }

    @app.on_event("shutdown")
    def close_pool() -> None:
        db.close()

    return app


app = create_enterprise_app()
