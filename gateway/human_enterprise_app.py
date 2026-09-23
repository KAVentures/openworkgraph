from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException

from .auth import env_token_matches
from .enterprise_app import _take_get_endpoint, create_enterprise_app
from .hardening import HardeningSettings, PooledGatewayDB
from .human_access import HumanAccessSettings, install_human_access
from .settings import GatewaySettings

GATEWAY_VERSION = "0.56.1"


def _bearer(value: str | None) -> str:
    raw = str(value or "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def create_human_enterprise_app(
    *,
    settings: GatewaySettings | None = None,
    hardening: HardeningSettings | None = None,
    human_access: HumanAccessSettings | None = None,
    db: PooledGatewayDB | None = None,
    verifier: Any | None = None,
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    hardening = hardening or HardeningSettings.from_env()
    human_access = human_access or HumanAccessSettings.from_env()
    db = db or PooledGatewayDB(
        settings.database_url,
        pool_min_size=hardening.postgres_pool_min_size,
        pool_max_size=hardening.postgres_pool_max_size,
        pool_timeout_seconds=hardening.postgres_pool_timeout_seconds,
    )

    app = create_enterprise_app(settings=settings, hardening=hardening, db=db)
    app.version = GATEWAY_VERSION

    def require_admin(authorization: str | None) -> None:
        if not env_token_matches(_bearer(authorization), settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway admin token required")

    install_human_access(
        app,
        db=db,
        settings=human_access,
        require_admin=require_admin,
        verifier=verifier,
    )

    previous_health = _take_get_endpoint(app, "/health")
    previous_capabilities = _take_get_endpoint(app, "/v1/capabilities")
    previous_runtime = _take_get_endpoint(app, "/v1/admin/runtime")

    @app.get("/health")
    def health() -> dict[str, Any]:
        payload = dict(previous_health() if previous_health else {"status": "ok"})
        payload["version"] = GATEWAY_VERSION
        payload["human_oidc_enabled"] = bool(human_access.enabled)
        return payload

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        payload = dict(previous_capabilities() if previous_capabilities else {})
        payload["human_access"] = {
            "oidc": "configured" if human_access.enabled else "disabled_by_default",
            "service_token_compatibility": True,
            "self_read_default": bool(human_access.self_read_enabled),
            "group_scoped_authorization": True,
            "team_scoped_raw_evidence": True,
            "organization_raw_evidence_requires_explicit_scope": True,
            "aggregate_read": {
                "thresholded": True,
                "minimum_actors": human_access.aggregate_min_actors,
                "actor_identifiers_returned": False,
                "window": "complete_utc_iso_weeks",
                "values_rounded": True,
                "exact_cohort_size_returned": False,
            },
            "pseudonymous_trace": {
                "separate_explicit_scope": True,
                "claimed_anonymous": False,
                "cursor_pagination": True,
            },
        }
        return payload

    @app.get("/v1/admin/runtime")
    def runtime(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        payload = dict(previous_runtime(authorization) if previous_runtime else {})
        payload["version"] = GATEWAY_VERSION
        payload["human_oidc"] = {
            "enabled": bool(human_access.enabled),
            "aggregate_min_actors": human_access.aggregate_min_actors,
            "aggregate_window": "complete_utc_iso_weeks",
            "aggregate_values_rounded": True,
            "pseudonymous_scope_configured": any(
                "pseudonymous:evidence:read" in scopes
                for scopes in (human_access.group_scope_map or {}).values()
            ),
        }
        return payload

    return app


app = create_human_enterprise_app()
