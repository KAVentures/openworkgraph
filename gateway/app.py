from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from shared.evidence import RAW_RICH_EVIDENCE_CONTRACT, rich_evidence_row
from .auth import ALLOWED_INTEGRATION_SCOPES, DEVICE_SCOPES, Principal, env_token_matches, issue_token, normalize_scopes
from .db import GatewayDB
from .policy import privacy_contract_violation
from .query import workflow_trace
from .settings import GatewaySettings


class EnrollmentRequest(BaseModel):
    organization_id: str
    actor_id: str = ""
    device_id: str


class IntegrationTokenRequest(BaseModel):
    organization_id: str
    actor_id: str = ""
    scopes: list[str] = Field(default_factory=lambda: ["evidence:read"])
    label: str = ""


class PolicyRequest(BaseModel):
    policy: dict[str, Any] = Field(default_factory=dict)


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
        version="0.53.0",
        description="Self-hosted organization evidence gateway. Raw privacy-hardened evidence is canonical; inferred tasks are not treated as ground truth.",
    )
    app.state.settings = settings
    app.state.db = db

    @app.on_event("startup")
    def startup() -> None:
        db.init()

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

    def effective_actor(p: Principal, requested: str | None) -> str | None:
        """Apply an optional actor restriction embedded in an integration token."""
        restricted = str(p.actor_id or "").strip()
        requested_value = str(requested or "").strip()
        if restricted:
            if requested_value and requested_value != restricted:
                raise HTTPException(status_code=403, detail="integration token is restricted to another actor")
            return restricted
        return requested_value or None

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "server": "OpenWorkGraph Gateway",
            "version": "0.53.0",
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
        }

    @app.post("/v1/devices/enroll")
    def enroll_device(request: EnrollmentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not env_token_matches(_bearer(authorization), settings.enrollment_token):
            raise HTTPException(status_code=401, detail="Gateway enrollment token required")
        organization_id = request.organization_id.strip()
        device_id = request.device_id.strip()
        actor_id = request.actor_id.strip()
        if not organization_id or not device_id:
            raise HTTPException(status_code=400, detail="organization_id and device_id are required")
        if max(len(organization_id), len(device_id), len(actor_id)) > 512:
            raise HTTPException(status_code=400, detail="organization/actor/device identifiers must be at most 512 characters")
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
            replace_device=True,
        )
        db.audit(organization_id=organization_id, principal_id=token_id, action="device.enrolled", details={"device_id": device_id})
        return {
            "token": token,
            "token_id": token_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "device_id": device_id,
            "scopes": sorted(DEVICE_SCOPES),
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
        return {"revoked": db.revoke_token(token_id), "token_id": token_id}

    @app.put("/v1/admin/policy/{organization_id}")
    def update_policy(organization_id: str, request: PolicyRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(authorization)
        policy = db.set_policy(organization_id, request.policy)
        db.audit(organization_id=organization_id, principal_id="gateway-admin", action="policy.updated", details={"policy": policy})
        return {"organization_id": organization_id, "policy": policy}

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
        result = workflow_trace(
            db,
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
        result = workflow_trace(
            db,
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
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(rows) < limit:
            page = workflow_trace(
                db,
                organization_id=p.organization_id,
                since=since,
                until=until,
                actor_id=actor_id,
                cursor=cursor,
                limit=min(500, limit - len(rows)),
            )
            rows.extend(page["rows"])
            cursor = page.get("next_cursor")
            if not cursor:
                break

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            transfer_id = str(row.get("clipboard_transfer_id") or (row.get("metadata") or {}).get("clipboard_transfer_id") or "")
            if transfer_id:
                grouped.setdefault(transfer_id, []).append(row)
        items = [
            {"clipboard_transfer_id": key, "events": value, "event_count": len(value)}
            for key, value in grouped.items()
        ]
        db.audit(
            organization_id=p.organization_id,
            principal_id=p.token_id,
            action="transfers.read",
            details={"evidence_rows_scanned": len(rows), "transfers_returned": len(items), "actor_id": actor_id or ""},
        )
        return {
            "items": items,
            "returned": len(items),
            "data_layer": "privacy_hardened_raw_rich_evidence",
            "clipboard_contents_captured": False,
        }

    return app


app = create_app()
