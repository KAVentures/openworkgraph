from __future__ import annotations

"""REST surface for owner-offered Open Work hand-off (see open_work_dispatch)."""
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from . import open_work_dispatch as dispatch
from .local_auth import bearer_matches, dashboard_session_valid

router = APIRouter()
_MAX_BODY = 2048


def _require_dashboard(request: Request) -> None:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session ") or not dashboard_session_valid(raw[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="explicit local dashboard session required")


def _require_agent(request: Request) -> None:
    # Do not rely solely on the enclosing app middleware: a router mount must
    # never turn an owner-offered case into an unauthenticated agent capability.
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="local agent bearer required")


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="JSON dispatch request required")
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > _MAX_BODY:
                raise HTTPException(status_code=413, detail="dispatch request too large")
            raw.extend(chunk)

        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        def reject_constant(_value):
            raise ValueError("nonstandard JSON number")

        try:
            # Preserve the existing empty-body contract; semantic validation stays in each route.
            data = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant) if raw else {}
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(status_code=422, detail="invalid JSON") from None
    finally:
        raw.clear()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="expected an object")
    return data

async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except dispatch.DispatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail="dispatch queue could not be saved") from None


# Owner (dashboard) actions.
@router.get("/v1/open-work/dispatch")
async def dispatch_status(request: Request):
    _require_dashboard(request)
    return await _run(dispatch.status, owner=True)


@router.post("/v1/open-work/dispatch/settings")
async def dispatch_settings(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return await _run(dispatch.set_enabled, data.get("enabled"), lease_minutes=data.get("lease_minutes"))


@router.post("/v1/open-work/dispatch/offers")
async def dispatch_offer(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    if data.get("confirm_offer") is not True:
        raise HTTPException(status_code=403, detail="explicit confirmation required to offer a case to agents")
    return await _run(dispatch.offer, data.get("work_item_ref"), owner_note=data.get("owner_note", ""),
                      days=data.get("days", 3))


@router.post("/v1/open-work/dispatch/offers/withdraw")
async def dispatch_withdraw(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return {"removed": await _run(dispatch.withdraw, data.get("work_item_ref"))}


@router.post("/v1/open-work/dispatch/offers/acknowledge")
async def dispatch_acknowledge(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    if type(data.get("reoffer")) is not bool:
        raise HTTPException(status_code=422, detail="reoffer must be true or false")
    return {"offer": await _run(dispatch.acknowledge, data.get("work_item_ref"), reoffer=data["reoffer"])}


# Agent actions (called by the local MCP tools with the local API token).
@router.get("/v1/open-work/dispatch/agent")
async def agent_offers(request: Request):
    _require_agent(request)
    result = await _run(dispatch.status, owner=False)
    result["offers"] = [o for o in result["offers"] if o["status"] == "offered"] if result["enabled"] else []
    return result


@router.post("/v1/open-work/dispatch/agent/claim")
async def agent_claim(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.claim, data.get("work_item_ref"), data.get("agent"))


@router.post("/v1/open-work/dispatch/agent/release")
async def agent_release(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.release, data.get("work_item_ref"), data.get("lease_token"))


@router.post("/v1/open-work/dispatch/agent/report")
async def agent_report(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.report, data.get("work_item_ref"), data.get("lease_token"), data.get("outcome"),
                      note=data.get("note", ""), operation_receipt_ref=data.get("operation_receipt_ref"))


# Independently authenticated, case-scoped external-agent capabilities.
# The legacy /agent routes remain for local MCP compatibility only.
from . import open_work_agent_grants as grants

@router.get("/v1/open-work/dispatch/grants")
async def owner_grants(request: Request):
    _require_dashboard(request)
    return {"grants": await asyncio.to_thread(grants.list_grants)}

@router.post("/v1/open-work/dispatch/grants")
async def owner_issue_grant(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    from .open_work_declaration_routes import _source_scope
    scope = await asyncio.to_thread(_source_scope)
    try:
        return await asyncio.to_thread(grants.issue, data.get("work_item_ref"),
            data.get("agent"), data.get("hours"), confirmed=data.get("confirm_grant"), source_scope=scope,
            context_approved=data.get("confirm_case_context") is True)
    except grants.GrantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

@router.post("/v1/open-work/dispatch/grants/revoke")
async def owner_revoke_grant(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    try:
        return {"revoked": await asyncio.to_thread(grants.revoke, data.get("grant_id"))}
    except grants.GrantError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

def _grant(request: Request, ref: str | None = None) -> dict:
    identity = grants.authenticate(request.headers.get("authorization"), ref)
    if identity is None:
        raise HTTPException(status_code=401, detail="active case-scoped agent grant required")
    return identity

@router.get("/v1/open-work/dispatch/granted")
async def granted_offers(request: Request):
    identity = _grant(request)
    state = await _run(dispatch.status, owner=False)
    state["offers"] = [item for item in state["offers"] if
                       item["work_item_ref"] == identity["work_item_ref"] and item["status"] == "offered"]
    return state

@router.post("/v1/open-work/dispatch/granted/claim")
async def granted_claim(request: Request):
    identity = _grant(request)
    data = await _body(request)
    _grant(request, data.get("work_item_ref"))
    return await _run(dispatch.claim, identity["work_item_ref"], identity["agent"])

@router.post("/v1/open-work/dispatch/granted/release")
async def granted_release(request: Request):
    identity = _grant(request)
    data = await _body(request)
    _grant(request, data.get("work_item_ref"))
    return await _run(dispatch.release, identity["work_item_ref"], data.get("lease_token"),
                      expected_agent=identity["agent"])

@router.post("/v1/open-work/dispatch/granted/report")
async def granted_report(request: Request):
    identity = _grant(request)
    data = await _body(request)
    _grant(request, data.get("work_item_ref"))
    return await _run(dispatch.report, identity["work_item_ref"], data.get("lease_token"),
        data.get("outcome"), note=data.get("note", ""),
        operation_receipt_ref=data.get("operation_receipt_ref"),
        expected_agent=identity["agent"])


@router.get("/v1/open-work/dispatch/granted/case-context")
async def granted_reviewed_case_context(request: Request, response: Response):
    """Bounded, local-only owner-reviewed context; never a provider or outcome receipt."""
    from .open_work_declaration_routes import _source_scope
    from .open_work_case_declarations import list_case_declarations, CaseDeclarationError
    identity = _grant(request)
    response.headers["Cache-Control"] = "no-store"
    # Legacy grants cannot acquire retrospective source access. A grant must
    # have been issued under this exact connected source scope.
    scope = await asyncio.to_thread(_source_scope)
    if (not identity.get("context_approved") or not scope or
        not identity.get("source_scope") or identity["source_scope"] != scope):
        raise HTTPException(403, "current source-bound grant required")
    try:
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
    except (CaseDeclarationError, OSError) as exc:
        raise HTTPException(503, "reviewed case context unavailable") from None
    # Recheck both independent permissions after reading: source connection,
    # owner review, offer generation and grant revocation can change in flight.
    if await asyncio.to_thread(_source_scope) != scope or not grants.authenticate(
        request.headers.get("authorization"), identity["work_item_ref"]
    ):
        raise HTTPException(409, "source or grant changed during context read")
    item = next((entry for entry in snapshot["items"] if
                 entry["work_item_ref"] == identity["work_item_ref"]), None)
    if item is None:
        raise HTTPException(404, "no currently reviewed case context")
    # A source-scoped grant is not enough if the owner edits or withdraws a
    # declaration during the read. Pin the complete reviewed snapshot, not
    # merely the presence of this case, so changed dependency edges cannot
    # be returned under an earlier approval.
    try:
        latest = await asyncio.to_thread(list_case_declarations, scope)
    except (CaseDeclarationError, OSError):
        raise HTTPException(503, "reviewed case context unavailable") from None
    if latest != snapshot or await asyncio.to_thread(_source_scope) != scope or not grants.authenticate(
        request.headers.get("authorization"), identity["work_item_ref"]
    ):
        raise HTTPException(409, "review or grant changed during context read")
    # Edit tokens are owner-only compare-and-swap capabilities; never give
    # them to an agent. No provider reads or business success assertions.
    # Dependencies are owner-declared edges, not proof of blocking or completion.
    # Resolve only against this same reviewed, retained, source-scoped snapshot;
    # never disclose another case's goals, outcomes, edit tokens or existence
    # outside an edge already explicitly disclosed by this case's owner.
    dependencies = item.get("depends_on_work_item_refs", [])
    if not isinstance(dependencies, list) or len(dependencies) > 8 or any(
        not isinstance(ref, str) or not ref.startswith("owg:w:") or len(ref) != 38
        for ref in dependencies
    ):
        raise HTTPException(503, "reviewed dependency context unavailable")
    retained_refs = {entry.get("work_item_ref") for entry in snapshot["items"]}
    dependency_review = [
        {"work_item_ref": ref,
         "review_state": "currently_reviewed" if ref in retained_refs else "unknown_or_not_retained",
         "provider_state": "unknown", "outcome_verified": False}
        for ref in dependencies
    ]
    safe = {key: value for key, value in item.items() if key not in ("edit_token", "scope_ref")}
    return {"format": "openworkgraph.granted-reviewed-case-context.v1",
            "dependency_review": dependency_review,
            "case": safe, "source_state": "unknown_until_explicit_recheck",
            "provider_receipt_verified": False, "customer_outcome_verified": False,
            "agent_execution_authorized": False, "source_read_performed": False}
