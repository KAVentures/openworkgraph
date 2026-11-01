from __future__ import annotations

"""Explicit local dashboard REST review of owner-declared Open Work facts.

No source reads, scans, cloud sync, AI bearer access or agent execution.
The source scope is derived from the configured Stripe account and an existing
installation secret; a caller can never choose another source/tenant scope.
"""
import asyncio
import hashlib
import hmac
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from .open_work_case_graph import project_declared_case_graph
from .local_auth import dashboard_session_valid, _read_or_create_secret
from .local_source_credentials import _metadata_path, _read_metadata
from .open_work_case_declarations import (
    CaseDeclarationError, delete_case_declaration,
    list_case_declarations, save_case_declaration, clear_case_declarations,
)

router = APIRouter()
_MAX_BODY = 4096


def _dashboard_only(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if (not auth.lower().startswith("owg-session ") or
        not dashboard_session_valid(auth[len("owg-session "):].strip())):
        raise HTTPException(403, "explicit local dashboard session required")


def _source_scope() -> str | None:
    # This metadata exists only after a separately authorized, verified local
    # Stripe connection. No keychain access, Stripe request or provider ID
    # enters the response. Replacement/disconnect already clears reviews.
    metadata = _read_metadata(_metadata_path())
    if metadata is None:
        return None
    account = metadata["account"]
    key = _read_or_create_secret(".open_work_reference_key").encode("utf-8")
    digest = hmac.new(
        key, ("open-work:declaration-scope:stripe:v1:" + account).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return "owg:scope:" + digest


def _safe_error(exc: Exception) -> HTTPException:
    # Never echo raw source/account identifiers, SQL, credentials or payloads.
    if isinstance(exc, CaseDeclarationError):
        message = str(exc)
        if "stale" in message or "scope" in message or "changed" in message:
            return HTTPException(409, "declaration version, scope or review changed")
        if "review" in message or "retention" in message:
            return HTTPException(403, "explicit current review and retained history required")
        return HTTPException(422, "invalid or unavailable owner declaration")
    return HTTPException(409, "local declaration store unavailable")


def _pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


async def _body(request: Request, required: set[str]) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON declaration request required")
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > _MAX_BODY:
                raise HTTPException(413, "declaration request too large")
            raw.extend(chunk)
        try:
            data = json.loads(raw, object_pairs_hook=_pairs_unique, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(422, "invalid declaration JSON") from None
    finally:
        raw.clear()
    if not isinstance(data, dict) or set(data) != required:
        raise HTTPException(422, "exact declaration fields required")
    return data


async def _require_scope() -> str:
    scope = await asyncio.to_thread(_source_scope)
    if scope is None:
        raise HTTPException(403, "explicit local Stripe source connection required")
    return scope


async def _recheck_source(scope: str) -> None:
    if await asyncio.to_thread(_source_scope) != scope:
        # Fail closed on disconnect/account replacement during a local write.
        # This is not an atomic cross-process provider-grant transaction.
        await asyncio.to_thread(clear_case_declarations)
        raise HTTPException(409, "local source connection changed")


@router.get("/v1/open-work/declarations")
async def list_local_case_declarations(request: Request, response: Response):
    _dashboard_only(request)
    response.headers["Cache-Control"] = "no-store"
    scope = await asyncio.to_thread(_source_scope)
    if scope is None:
        return {
            "format": "openworkgraph.local-case-declarations.v1",
            "available": False, "items": [],
            "source_read_performed": False,
            "source_state": "unknown_until_explicit_recheck",
            "agent_execution_authorized": False, "not_synced": True,
        }
    try:
        result = await asyncio.to_thread(list_case_declarations, scope)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    return {**result, "available": True}


@router.get("/v1/open-work/declarations/graph")
async def get_local_case_dependency_graph(request: Request, response: Response):
    """Dashboard-only graph of currently reviewed declarations, never source truth."""
    _dashboard_only(request)
    response.headers["Cache-Control"] = "no-store"
    scope = await asyncio.to_thread(_source_scope)
    if scope is None:
        # No source grant means no durable declaration graph can be read.
        return {
            **project_declared_case_graph({
                "format": "openworkgraph.local-case-declarations.v1", "items": [],
            }),
            "available": False,
        }
    try:
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
        graph = project_declared_case_graph(snapshot)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    return {**graph, "available": True}


@router.post("/v1/open-work/declarations")
async def save_local_case_declaration(request: Request, response: Response):
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "confirm_owner_declaration", "work_item_ref", "goal_code",
        "expected_outcome_code", "depends_on_work_item_refs",
        "expected_revision", "expected_edit_token",
    })
    if payload["confirm_owner_declaration"] is not True:
        raise HTTPException(403, "explicit owner declaration confirmation required")
    try:
        result = await asyncio.to_thread(
            save_case_declaration,
            payload["work_item_ref"], scope,
            goal_code=payload["goal_code"],
            expected_outcome_code=payload["expected_outcome_code"],
            depends_on_work_item_refs=payload["depends_on_work_item_refs"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/v1/open-work/declarations")
async def delete_local_case_declaration(request: Request, response: Response):
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "confirm_delete", "work_item_ref", "expected_revision", "expected_edit_token",
    })
    if payload["confirm_delete"] is not True:
        raise HTTPException(403, "explicit declaration deletion confirmation required")
    try:
        result = await asyncio.to_thread(
            delete_case_declaration,
            payload["work_item_ref"], scope,
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result
