from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
from shared.time_utils import normalize_timestamp
from .auth import ALLOWED_INTEGRATION_SCOPES, DEVICE_SCOPES, Principal, env_token_matches, issue_token, normalize_scopes
from .db import GatewayDB
from .enrollment import active_device_exists, consume_enrollment_grant, create_enrollment_grant, init_enrollment_schema
from .lifecycle import get_retention_policy, init_lifecycle_schema, set_retention_policy
from .policy import privacy_contract_violation
from .query import workflow_trace
from .settings import GatewaySettings


class EnrollmentRequest(BaseModel):
    organization_id: str
    actor_id: str = ""
    device_id: str


class EnrollmentGrantRequest(BaseModel):
    organization_id: str
    actor_id: str = ""
    expires_minutes: int = 30


class IntegrationTokenRequest(BaseModel):
    organization_id: str
    actor_id: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["evidence:read"])
    label: str = ""


class PolicyRequest(BaseModel):
    policy: dict[str, Any] = Field(default_factory=dict)


class RetentionPolicyRequest(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=36500)


class EvidenceBatch(BaseModel):
    events: list[dict[str, Any]]


class SearchRequest(BaseModel):
    query: str
    since: str | None = None
    until: str | None = None
    actor_id: str | None = None
    device_id: str | None = None
    session_id: str | None = None
    limit: int = 100


def _bearer(value: str | None) -> str:
    raw = str(value or "")
    if not raw.lower().startswith("bearer "):
        return ""
    return raw[7:].strip()


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _required_event_fields(event: dict[str, Any]) -> str | None:
    for key in ("event_id", "observed_at", "event_type"):
        value = str(event.get(key) or "").strip()
        if not value:
            return f"event.{key} is required"
        if len(value) > 512:
            return f"event.{key} exceeds 512 characters"
    return None


def create_app(*, settings: GatewaySettings | None = None, db: GatewayDB | None = None) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    db = db or GatewayDB(settings.database_url)
    app = FastAPI(
        title="OpenWorkGraph Gateway",
        version="0.54.0",
        description="Self-hosted organization evidence gateway. Raw privacy-hardened evidence is canonical; inferred tasks are not treated as ground truth.",
    )
    app.state.settings = settings
    app.state.db = db

    @app.on_event("startup")
    def startup() -> None:
        db.init()
        init_enrollment_schema(db)
        init_lifecycle_schema(db)

    def principal(authorization: str | None = Header(default=None)) -> Principal:
        token = _bearer(authorization)
        value = db.authenticate(token)
        if value is None:
            raise HTTPException(status_code=401, detail="valid OpenWorkGraph Gateway token required")
        return value

    def require(p: Principal, scope: str) -> None:
        try:
            p.require(scope)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def require_admin(authorization: str | None) -> None:
        if not env_token_matches(_bearer(authorization), settings.admin_token):
            raise HTTPException(status_code=401, detail="Gateway admin token required")

    def require_enrollment_issuer(authorization: str | None) -> None:
        token = _bearer(authorization)
        if not (
            env_token_matches(token, settings.admin_token)
            or env_token_matches(token, settings.enrollment_token)
        ):
            raise HTTPException(status_code=401, detail="Gateway enrollment issuer token required")

    def effective_actor(p: Principal, requested: str | None) -> str | None:
        restricted = str(p.actor_id or "").strip()
        requested_value = str(requested or "").strip()
        if restricted:
            if requested_value and requested_value != restricted:
                raise HTTPException(status_code=403, detail="integration token is restricted to another actor")
            return restricted
        return requested_value or None

    def trace_or_400(**kwargs: Any) -> dict[str, Any]:
        try:
            return workflow_trace(db, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "server": "OpenWorkGraph Gateway",
            "version": "0.54.0",
            "storage": "postgresql" if db.is_postgres else "sqlite-development",
            "self_hosted": True,
        }

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "canonical_data_layer": "privacy_hardened_raw_rich_evidence",
            "evidence_contract": dict(RAW_RICH_EVIDENCE_CONTRACT),
            "interfaces": ["REST", "MCP-adapter"],
            "cloud_account_required": False,
            "inferred_tasks_authoritative": False,
            "max_event_bytes": settings.max_event_bytes,
            "max_batch_bytes": settings.max_batch_bytes,
            "preferred_enrollment": "single_use_organization_bound_grants",
            "legacy_shared_enrollment_supported": True,
            "evidence_lifecycle": {
                "organization_retention": "opt_in_default_disabled",
                "canonical_trace_retention_enforced": True,
                "current_context_retention_enforced": True,
                "physical_cleanup": "explicit_or_scheduled",
                "manual_purge_cli": True,
            },
        }

    @app.post("/v1/admin/enrollment-codes")
    def create_enrollment_code(
        request: EnrollmentGrantRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_enrollment_issuer(authorization)
        organization_id = request.organization_id.strip()
        actor_id = request.actor_id.strip()
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")
        if max(len(organization_id), len(actor_id)) > 512:
            raise HTTPException(status_code=400, detail="organization_id/actor_id must be at most 512 characters")
        try:
            grant = create_enrollment_grant(
                db,
                organization_id=organization_id,
                actor_id=actor_id,
                expires_minutes=request.expires_minutes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=organization_id,
            principal_id="gateway-enrollment-issuer",
            action="device.enrollment_code.created",
            details={"grant_id": grant["grant_id"], "actor_id": actor_id, "expires_at": grant["expires_at"]},
        )
        return grant

    @app.post("/v1/devices/enroll")
    def enroll_device(request: EnrollmentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        presented = _bearer(authorization)
        grant = consume_enrollment_grant(db, presented)
        legacy_shared_secret = False
        if grant is None:
            # v0.53 compatibility: existing self-hosted installs may still use the
            # long-lived enrollment secret directly. Keep this working for now,
            # but never allow it to silently replace an already-enrolled device.
            if not env_token_matches(presented, settings.enrollment_token):
                raise HTTPException(status_code=401, detail="valid unused OpenWorkGraph enrollment code required")
            organization_id = request.organization_id.strip()
            actor_id = request.actor_id.strip()
            grant_id = "legacy-shared-enrollment"
            legacy_shared_secret = True
        else:
            organization_id = str(grant["organization_id"]).strip()
            actor_id = str(grant.get("actor_id") or "").strip()
            grant_id = str(grant["grant_id"])
            requested_org = request.organization_id.strip()
            requested_actor = request.actor_id.strip()
            if requested_org and requested_org != organization_id:
                raise HTTPException(status_code=403, detail="enrollment code is bound to another organization")
            if actor_id and requested_actor and requested_actor != actor_id:
                raise HTTPException(status_code=403, detail="enrollment code is bound to another actor")

        device_id = request.device_id.strip()
        if not organization_id or not device_id:
            raise HTTPException(status_code=400, detail="organization_id and device_id are required")
        if max(len(organization_id), len(device_id), len(actor_id)) > 512:
            raise HTTPException(status_code=400, detail="organization/actor/device identifiers must be at most 512 characters")
        if active_device_exists(db, organization_id=organization_id, device_id=device_id):
            raise HTTPException(status_code=409, detail="device_id is already enrolled; revoke or rotate it explicitly")
        token = issue_token("owg_device")
        token_id = f"device_{uuid.uuid4().hex}"
        db.put_token(
            token_id=token_id,
            token=token,
            token_type="device",
            organization_id=organization_id,
            actor_id=actor_id,
            device_id=device_id,
            scopes=set(DEVICE_SCOPES),
            replace_device=False,
        )
        db.audit(
            organization_id=organization_id,
            principal_id=token_id,
            action="device.enrolled",
            details={
                "device_id": device_id,
                "grant_id": grant_id,
                "legacy_shared_secret": legacy_shared_secret,
            },
        )
        return {
            "token": token,
            "token_id": token_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "device_id": device_id,
            "scopes": sorted(DEVICE_SCOPES),
            "enrollment_mode": "legacy_shared_secret" if legacy_shared_secret else "single_use_code",
            "note": "The device token is shown once. Store it in the endpoint credential store/file.",
        }

    @app.post("/v1/device/revoke")
    def revoke_current_device(p: Principal = Depends(principal)) -> dict[str, Any]:
        if p.token_type != "device":
            raise HTTPException(status_code=403, detail="only a device token can revoke itself")
        revoked = db.revoke_token(p.token_id)
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="device.revoked",
            details={"device_id": p.device_id, "revoked": revoked},
        )
        return {"revoked": revoked, "device_id": p.device_id}

    @app.post("/v1/admin/integration-tokens")
    def create_integration_token(request: IntegrationTokenRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        organization_id = request.organization_id.strip()
        actor_id = request.actor_id.strip()
        if not organization_id:
            raise HTTPException(status_code=400, detail="organization_id is required")
        if max(len(organization_id), len(actor_id)) > 512:
            raise HTTPException(status_code=400, detail="organization_id/actor_id must be at most 512 characters")
        scopes = normalize_scopes(request.scopes)
        unknown = scopes - ALLOWED_INTEGRATION_SCOPES
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown scopes: {sorted(unknown)}")
        if not scopes:
            raise HTTPException(status_code=400, detail="at least one scope is required")
        token = issue_token("owg_service")
        token_id = f"service_{uuid.uuid4().hex}"
        db.put_token(
            token_id=token_id,
            token=token,
            token_type="integration",
            organization_id=organization_id,
            actor_id=actor_id,
            scopes=scopes,
        )
        db.audit(
            organization_id=organization_id,
            principal_id="gateway-admin",
            action="integration.token.created",
            details={"token_id": token_id, "scopes": sorted(scopes), "actor_id": actor_id, "label": request.label[:120]},
        )
        return {
            "token": token,
            "token_id": token_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "scopes": sorted(scopes),
            "note": "The service token is shown once. Store it in the integration secret store.",
        }

    @app.delete("/v1/admin/tokens/{token_id}")
    def revoke_token(token_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        revoked = db.revoke_token(token_id)
        db.audit(
            organization_id="_admin",
            principal_id="gateway-admin",
            action="token.revoked",
            details={"token_id": token_id, "revoked": revoked},
        )
        return {"revoked": revoked, "token_id": token_id}

    @app.put("/v1/admin/policy/{organization_id}")
    def update_policy(organization_id: str, request: PolicyRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        policy = db.set_policy(organization_id, request.policy)
        db.audit(organization_id=organization_id, principal_id="gateway-admin", action="policy.updated", details={"policy": policy})
        return {"organization_id": organization_id, "policy": policy}

    @app.get("/v1/admin/retention/{organization_id}")
    def retention_policy(organization_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return get_retention_policy(db, organization_id)

    @app.put("/v1/admin/retention/{organization_id}")
    def update_retention_policy(
        organization_id: str,
        request: RetentionPolicyRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(authorization)
        if not organization_id.strip() or len(organization_id.strip()) > 512:
            raise HTTPException(status_code=400, detail="organization_id must be 1-512 characters")
        previous = get_retention_policy(db, organization_id)
        try:
            policy = set_retention_policy(db, organization_id, request.retention_days)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.audit(
            organization_id=organization_id,
            principal_id="gateway-admin",
            action="lifecycle.retention.updated",
            details={
                "previous_retention_days": previous.get("retention_days"),
                "retention_days": policy.get("retention_days"),
            },
        )
        return {
            **policy,
            "note": "Retention is opt-in and non-destructive to configure. Use the lifecycle CLI to preview/apply physical cleanup.",
        }

    @app.get("/v1/admin/audit/{organization_id}")
    def audit_log(organization_id: str, limit: int = 100, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        return {"items": db.audit_rows(organization_id, limit)}

    @app.get("/v1/device-policy")
    def device_policy(p: Principal = Depends(principal)) -> dict[str, Any]:
        require(p, "policy:read")
        return {
            "organization_id": p.organization_id,
            "policy": db.get_policy(p.organization_id),
            "policy_is_ceiling": True,
            "note": "Endpoint local policy can only further restrict what is synchronized.",
        }

    @app.post("/v1/evidence/batch")
    def ingest(batch: EvidenceBatch, p: Principal = Depends(principal)) -> dict[str, Any]:
        require(p, "evidence:write")
        if p.token_type != "device":
            raise HTTPException(status_code=403, detail="only enrolled device tokens may ingest evidence")
        if not batch.events:
            return {"inserted": 0, "acknowledged_event_ids": []}
        if len(batch.events) > settings.max_batch:
            raise HTTPException(status_code=413, detail=f"batch exceeds max {settings.max_batch}")

        safe_events: list[dict[str, Any]] = []
        total_bytes = 0
        for event in batch.events:
            required_error = _required_event_fields(event)
            if required_error:
                raise HTTPException(status_code=400, detail=required_error)
            try:
                observed_at = normalize_timestamp(str(event.get("observed_at") or ""))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"event.observed_at: {exc}") from exc
            event_bytes = _json_size(event)
            if event_bytes > settings.max_event_bytes:
                raise HTTPException(status_code=413, detail=f"event exceeds max {settings.max_event_bytes} bytes")
            total_bytes += event_bytes
            if total_bytes > settings.max_batch_bytes:
                raise HTTPException(status_code=413, detail=f"batch exceeds max {settings.max_batch_bytes} bytes")
            violation = privacy_contract_violation(event)
            if violation:
                raise HTTPException(status_code=400, detail=violation)
            item = dict(event)
            item["observed_at"] = observed_at
            item.pop("screenshot_path", None)
            item.pop("screenshot_bytes", None)
            safe_events.append(item)

        inserted, acknowledged = db.insert_events(p, safe_events)
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="evidence.batch.ingested",
            details={"received": len(safe_events), "inserted": inserted, "bytes": total_bytes},
        )
        return {"inserted": inserted, "acknowledged_event_ids": acknowledged}

    @app.get("/v1/workflow-trace")
    def trace(
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        query: str | None = None,
        actor_id: str | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
        event_type: str | None = None,
        p: Principal = Depends(principal),
    ) -> dict[str, Any]:
        require(p, "evidence:read")
        actor_id = effective_actor(p, actor_id)
        result = trace_or_400(
            organization_id=p.organization_id,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
            query=query,
            actor_id=actor_id,
            device_id=device_id,
            session_id=session_id,
            event_type=event_type,
        )
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="evidence.trace.read",
            details={"returned": result["returned"], "actor_id": actor_id or ""},
        )
        return result

    @app.post("/v1/search")
    def search(request: SearchRequest, p: Principal = Depends(principal)) -> dict[str, Any]:
        require(p, "evidence:read")
        actor_id = effective_actor(p, request.actor_id)
        result = trace_or_400(
            organization_id=p.organization_id,
            since=request.since,
            until=request.until,
            limit=request.limit,
            query=request.query,
            actor_id=actor_id,
            device_id=request.device_id,
            session_id=request.session_id,
        )
        result["query"] = request.query
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="evidence.search",
            details={"returned": result["returned"], "actor_id": actor_id or ""},
        )
        return result

    @app.get("/v1/context/current")
    def current_context(
        actor_id: str | None = None,
        device_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        p: Principal = Depends(principal),
    ) -> dict[str, Any]:
        require(p, "context:read")
        actor_id = effective_actor(p, actor_id)
        recent = db.recent_rows(organization_id=p.organization_id, actor_id=actor_id, device_id=device_id, limit=limit)
        cutoff = get_retention_policy(db, p.organization_id).get("cutoff")
        if cutoff:
            retained: list[dict[str, Any]] = []
            for row in recent:
                try:
                    if normalize_timestamp(str(row.get("observed_at") or "")) >= cutoff:
                        retained.append(row)
                except ValueError:
                    continue
            recent = retained
        rows = [rich_evidence_row(row, include_identity=True) for row in recent]
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="context.current.read",
            details={"returned": len(rows), "actor_id": actor_id or ""},
        )
        return {
            "rows": rows,
            "returned": len(rows),
            "data_layer": "privacy_hardened_raw_rich_evidence",
            "evidence_contract": dict(RAW_RICH_EVIDENCE_CONTRACT),
            "interpretation": "Recent observed evidence only. The Gateway does not assert a task name, workflow family, productivity score, or inferred intent.",
        }

    @app.get("/v1/transfers")
    def transfers(
        since: str | None = None,
        until: str | None = None,
        actor_id: str | None = None,
        limit: int = Query(default=300, ge=1, le=1000),
        p: Principal = Depends(principal),
    ) -> dict[str, Any]:
        require(p, "transfers:read")
        actor_id = effective_actor(p, actor_id)
        grouped: dict[str, list[dict[str, Any]]] = {}
        cursor: str | None = None
        scanned = 0
        while True:
            page = trace_or_400(
                organization_id=p.organization_id,
                since=since,
                until=until,
                actor_id=actor_id,
                cursor=cursor,
                query="clipboard_transfer_id",
                limit=500,
            )
            for row in page["rows"]:
                scanned += 1
                transfer_id = str(
                    row.get("clipboard_transfer_id")
                    or (row.get("metadata") or {}).get("clipboard_transfer_id")
                    or ""
                )
                if transfer_id:
                    grouped.setdefault(transfer_id, []).append(row)
            cursor = page.get("next_cursor")
            if not cursor:
                break

        items = [
            {"clipboard_transfer_id": key, "events": value, "event_count": len(value)}
            for key, value in list(grouped.items())[:limit]
        ]
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="transfers.read",
            details={"transfer_evidence_rows_scanned": scanned, "transfers_returned": len(items), "actor_id": actor_id or ""},
        )
        return {
            "items": items,
            "returned": len(items),
            "data_layer": "privacy_hardened_raw_rich_evidence",
            "clipboard_contents_captured": False,
        }

    return app


app = create_app()
