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
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from .open_work_case_graph import project_declared_case_graph, project_declared_case_change_impact
from .open_work_case_dossier import admitted_case_dossier_snapshot, compose_reviewed_case_dossier
from .open_work_case_context import stripe_open_work_case_context
from .open_work_case_portfolio import admit_portfolio, compose_portfolio_status
from .open_work_case_evidence_graph import compose_case_evidence_graph
from .open_work_case_operation_verification import (
    issue_case_record_link, validate_case_record_link,
    project_case_operation_verification,
)
from .open_work_case_dependency_operations import (
    admit_dependency_operation_links, compose_dependency_operation_result,
)
from .open_work_case_operation_timeline import (
    validate_prior_operation_timeline, append_operation_timeline,
)
from .open_work_case_dependency_history import compose_dependency_operation_history
from .open_work_case_human_dependency_history import compose_dependency_human_outcomes
from .open_work_case_outcome_reconciliation import compose_outcome_evidence_triage
from .open_work_case_operation_ledger import (
    append_operation_evidence, read_operation_evidence, dispute_operation_evidence,
    clear_operation_evidence,
)
from .open_work_case_outcome_reviews import (
    record_human_outcome, correct_human_outcome, read_human_outcomes,
    clear_human_outcome_reviews,
)
from .open_work_outcomes import source_operation_receipt
from .open_work_case_status_checkpoint import (
    validate_prior_checkpoint, issue_status_checkpoint,
)
from .open_work_dependency_dossier import (
    select_dependency_status_targets, compose_dependency_status_snapshot,
)
from .local_auth import dashboard_session_valid, _read_or_create_secret
from .local_source_credentials import _metadata_path, _read_metadata
from .open_work_case_declarations import (
    CaseDeclarationError, delete_case_declaration,
    list_case_declarations, save_case_declaration, clear_case_declarations,
    create_case_declaration_batch, revise_case_declaration_batch,
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
        # Whole words: "reviewed" in a validation message is not a missing review.
        if re.search(r"\b(review|retention)\b", message):
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
        await asyncio.to_thread(clear_operation_evidence)
        await asyncio.to_thread(clear_human_outcome_reviews)
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




@router.post("/v1/open-work/declarations/impact")
async def preview_local_case_declaration_change(request: Request, response: Response):
    """Read-only exact-CAS preview; never a replacement for confirmed write."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token", "operation",
    })
    try:
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
        impact = project_declared_case_change_impact(
            snapshot,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
            operation=payload["operation"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return impact



@router.post("/v1/open-work/declarations/dossier")
async def get_local_reviewed_case_dossier(request: Request, response: Response):
    """Explicit source scan joined to an exact, still-current owner declaration.

    No new source permission: reuse the existing Stripe case-context reader,
    its separately opted-in restricted source and retained OWG evidence.
    This endpoint never writes to Stripe or authorizes agent execution.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_scan", "max_pages",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit source scan confirmation required")
    max_pages = payload["max_pages"]
    if type(max_pages) is not int or not 1 <= max_pages <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    # The initial source scope/review/CAS gate happens BEFORE provider IO.
    await _recheck_source(scope)
    try:
        source = await asyncio.to_thread(
            stripe_open_work_case_context, payload["work_item_ref"],
            explicit_scan_authorized=True, max_pages=max_pages,
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        # A failed explicit read cannot establish a completed operation.
        source = {
            "format": "openworkgraph.open-work-case-context.v1",
            "work_item_ref": payload["work_item_ref"],
            "availability": "unknown",
            "reason": "source_unavailable",
            "source_evidence": {"source_state": "unknown"},
        }
    await _recheck_source(scope)
    try:
        after = await asyncio.to_thread(list_case_declarations, scope)
        _admitted_after, current_signature = admitted_case_dossier_snapshot(
            after,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        if current_signature != signature:
            raise CaseDeclarationError("stale declaration dependency review changed")
        result = compose_reviewed_case_dossier(admitted, source)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result



async def _read_reviewed_dependency_evidence(scope: str, payload: dict[str, Any]):
    """Single consent-pinned source-read path shared by status and evidence graph.

    The caller must already have authenticated the local dashboard session,
    validated exact request fields, and obtained the current source scope.
    """
    pages = payload["max_pages"]
    count = payload["max_cases"]
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        selected, unscanned = select_dependency_status_targets(
            admitted, max_cases=count)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    contexts = []
    for item in selected:
        ref = item["work_item_ref"]
        try:
            scanned = await asyncio.to_thread(
                stripe_open_work_case_context, ref,
                explicit_scan_authorized=True, max_pages=pages,
            )
        except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
            scanned = {
                "format": "openworkgraph.open-work-case-context.v1",
                "work_item_ref": ref,
                "availability": "unknown",
                "reason": "source_unavailable",
                "source_evidence": {"source_state": "unknown"},
            }
        # Refuse malformed or contradictory source evidence immediately;
        # never perform further scans on a corrupted provider projection.
        try:
            compose_reviewed_case_dossier({"work_item_ref": ref}, scanned)
        except (CaseDeclarationError, OSError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        contexts.append(scanned)
        # Stop before the next provider read if source, review, or a declared
        # prerequisite changed while the previous read was in flight.
        await _recheck_source(scope)
        try:
            current = await asyncio.to_thread(list_case_declarations, scope)
            _current, current_signature = admitted_case_dossier_snapshot(
                current,
                work_item_ref=payload["work_item_ref"],
                expected_revision=payload["expected_revision"],
                expected_edit_token=payload["expected_edit_token"],
            )
            if current_signature != signature:
                raise CaseDeclarationError("stale dependency review changed")
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
            raise _safe_error(exc) from None
    return admitted, signature, selected, contexts, unscanned


@router.post("/v1/open-work/declarations/dependency-status")
async def get_local_reviewed_dependency_status(request: Request, response: Response):
    """Explicit bounded source recheck of reviewed prerequisites and target.

    Sequential source checks are NOT an atomic provider snapshot; never treat
    an absent open invoice as payment, completion or an obligation.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_scan", "max_pages", "max_cases",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit dependency source scan confirmation required")
    pages = payload["max_pages"]
    count = payload["max_cases"]
    if type(pages) is not int or not 1 <= pages <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    if type(count) is not int or not 1 <= count <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    admitted, _signature, selected, contexts, unscanned = (
        await _read_reviewed_dependency_evidence(scope, payload)
    )
    try:
        result = compose_dependency_status_snapshot(
            admitted, selected, contexts, unscanned_prerequisites=unscanned)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result




@router.post("/v1/open-work/declarations/evidence-graph")
async def get_local_reviewed_case_evidence_graph(request: Request, response: Response):
    """Explicit bounded typed DAG + provider-state evidence, never outcome proof."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_scan", "max_pages", "max_cases",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit case evidence source scan required")
    if type(payload["max_pages"]) is not int or not 1 <= payload["max_pages"] <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    if type(payload["max_cases"]) is not int or not 1 <= payload["max_cases"] <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    admitted, signature, selected, contexts, unscanned = (
        await _read_reviewed_dependency_evidence(scope, payload)
    )
    try:
        result = compose_case_evidence_graph(
            admitted, signature, selected, contexts,
            unscanned_prerequisites=unscanned,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    # The source may change after its last read; never turn this projection
    # into a grant or durable assertion of customer/business completion.
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/portfolio-status")
async def get_local_reviewed_case_portfolio_status(request: Request, response: Response):
    """Explicit bounded scan of independently selected reviewed case goals.

    Unlike dependency-status this checks independently selected cases, while
    still pinning every transitive declared prerequisite for each selected
    case. A source status is never a customer outcome or business obligation.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "cases", "confirm_source_scan", "max_pages",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit portfolio source scan confirmation required")
    pages = payload["max_pages"]
    if type(pages) is not int or not 1 <= pages <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signatures = admit_portfolio(before, payload["cases"])
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    contexts = []
    for item in admitted:
        ref = item["work_item_ref"]
        try:
            scanned = await asyncio.to_thread(
                stripe_open_work_case_context, ref,
                explicit_scan_authorized=True, max_pages=pages,
            )
        except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
            scanned = {
                "format": "openworkgraph.open-work-case-context.v1",
                "work_item_ref": ref,
                "availability": "unknown",
                "reason": "source_unavailable",
                "source_evidence": {"source_state": "unknown"},
            }
        try:
            # Refuse contradictory provider identity/status before any next
            # provider read, not merely at the end of the batch.
            compose_reviewed_case_dossier(item, scanned)
        except (CaseDeclarationError, OSError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        contexts.append(scanned)
        await _recheck_source(scope)
        try:
            after = await asyncio.to_thread(list_case_declarations, scope)
            _current, current_signatures = admit_portfolio(after, payload["cases"])
            if current_signatures != signatures:
                raise CaseDeclarationError("stale portfolio prerequisite review changed")
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
    try:
        result = compose_portfolio_status(admitted, contexts)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result




@router.post("/v1/open-work/declarations/status-checkpoint")
async def get_local_reviewed_case_status_checkpoint(request: Request, response: Response):
    """Explicit portable source-state checkpoint and conservative later comparison.

    The checkpoint is sealed to the local source scope and entire currently
    reviewed DAG. It is NOT an execution credential, a verified operation or
    evidence that an invoice was paid after leaving the open inventory.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_scan", "max_pages", "previous_checkpoint",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit source checkpoint scan required")
    pages = payload["max_pages"]
    if type(pages) is not int or not 1 <= pages <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        # Use the existing installation secret, with separate HMAC domains.
        # This is a local-only bearer-session route, never an MCP permission.
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key")
        prior = payload["previous_checkpoint"]
        prior_state = None
        if prior is not None:
            prior_state = validate_prior_checkpoint(
                prior, admitted, signature, scope=scope, secret=secret)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # Reject a stale/forged receipt before any provider IO.
    await _recheck_source(scope)
    try:
        scanned = await asyncio.to_thread(
            stripe_open_work_case_context, payload["work_item_ref"],
            explicit_scan_authorized=True, max_pages=pages,
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        scanned = {
            "format": "openworkgraph.open-work-case-context.v1",
            "work_item_ref": payload["work_item_ref"],
            "availability": "unknown",
            "reason": "source_unavailable",
            "source_evidence": {"source_state": "unknown"},
        }
    await _recheck_source(scope)
    try:
        after = await asyncio.to_thread(list_case_declarations, scope)
        _current, current_signature = admitted_case_dossier_snapshot(
            after,
            work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        if current_signature != signature:
            raise CaseDeclarationError("stale checkpoint dependency review changed")
        minimized = compose_reviewed_case_dossier(admitted, scanned)
        if prior is not None:
            # Receipt may have expired while the separately authorized source
            # scan was in flight. Re-admit immediately before comparison.
            prior_state = validate_prior_checkpoint(
                prior, admitted, signature, scope=scope, secret=secret)
        result = issue_status_checkpoint(
            admitted, signature, minimized, scope=scope,
            secret=secret, prior_state=prior_state)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result



@router.post("/v1/open-work/declarations/operation-link")
async def issue_local_case_operation_link(request: Request, response: Response):
    """Explicitly source-join a reviewed Stripe case to one retained invoice record."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_scan", "max_pages", "resource_ref",
    })
    if payload["confirm_source_scan"] is not True:
        raise HTTPException(403, "explicit source record-link scan required")
    pages = payload["max_pages"]
    if type(pages) is not int or not 1 <= pages <= 3:
        raise HTTPException(422, "max_pages must be between 1 and 3")
    try:
        # Reject an invalid record reference before source IO.
        from .open_work_case_operation_verification import _record_ref
        _record_ref(payload["resource_ref"])
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    try:
        scanned = await asyncio.to_thread(
            stripe_open_work_case_context, payload["work_item_ref"],
            explicit_scan_authorized=True, max_pages=pages,
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        scanned = {
            "format": "openworkgraph.open-work-case-context.v1",
            "work_item_ref": payload["work_item_ref"],
            "availability": "unknown", "reason": "source_unavailable",
            "source_evidence": {"source_state": "unknown"},
        }
    await _recheck_source(scope)
    try:
        after = await asyncio.to_thread(list_case_declarations, scope)
        _current, current_signature = admitted_case_dossier_snapshot(
            after, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        if current_signature != signature:
            raise CaseDeclarationError("stale case record-link prerequisite review changed")
        minimized = compose_reviewed_case_dossier(admitted, scanned)
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        result = issue_case_record_link(
            admitted, signature, minimized, resource_ref=payload["resource_ref"],
            scope=scope, secret=secret,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/verify-operation")
async def verify_local_case_provider_operation(request: Request, response: Response):
    """Independently recheck an exact previously joined invoice's provider state."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_verification", "record_link",
    })
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit independent provider verification required")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # Invalid/revoked/stale link must never trigger a provider read.
    await _recheck_source(scope)
    try:
        receipt = await asyncio.to_thread(
            source_operation_receipt, resource_ref, "stripe_invoice_paid",
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        receipt = None
    await _recheck_source(scope)
    try:
        after = await asyncio.to_thread(list_case_declarations, scope)
        _current, current_signature = admitted_case_dossier_snapshot(
            after, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        if current_signature != signature:
            raise CaseDeclarationError("stale case operation verification graph changed")
        validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        result = project_case_operation_verification(admitted, resource_ref, receipt)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/dependency-operations")
async def verify_local_reviewed_dependency_operations(request: Request, response: Response):
    """Explicitly check independently source-joined invoices in a reviewed DAG."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_verification", "record_links",
    })
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit dependency provider verification required")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        signature, ordered, resources = admit_dependency_operation_links(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
            record_links=payload["record_links"], scope=scope, secret=secret,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # All links, scopes and exact prerequisite versions are validated before
    # the FIRST provider read. Re-admit the whole graph after EACH provider
    # read so a revoked dependency cannot authorize a subsequent source read.
    await _recheck_source(scope)
    verifications: dict[str, dict[str, Any]] = {}
    for ref in ordered:
        if ref not in resources:
            continue
        await _recheck_source(scope)
        try:
            receipt = await asyncio.to_thread(
                source_operation_receipt, resources[ref], "stripe_invoice_paid",
            )
        except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
            receipt = None
        await _recheck_source(scope)
        try:
            current = await asyncio.to_thread(list_case_declarations, scope)
            current_signature, current_order, current_resources = admit_dependency_operation_links(
                current, work_item_ref=payload["work_item_ref"],
                expected_revision=payload["expected_revision"],
                expected_edit_token=payload["expected_edit_token"],
                record_links=payload["record_links"], scope=scope, secret=secret,
            )
            if (current_signature != signature or current_order != ordered
                or current_resources != resources):
                raise CaseDeclarationError("stale dependency operation graph changed")
            verifications[ref] = project_case_operation_verification(
                {"work_item_ref": ref}, resources[ref], receipt,
            )
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
    # Even a zero-link preview must not return a stale owner declaration.
    await _recheck_source(scope)
    try:
        current = await asyncio.to_thread(list_case_declarations, scope)
        current_signature, current_order, current_resources = admit_dependency_operation_links(
            current, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
            record_links=payload["record_links"], scope=scope, secret=secret,
        )
        if (current_signature != signature or current_order != ordered
            or current_resources != resources):
            raise CaseDeclarationError("stale dependency operation graph changed")
        result = compose_dependency_operation_result(
            work_item_ref=payload["work_item_ref"], ordered=ordered,
            resources=resources, verifications=verifications,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/batch-revision-impact")
async def preview_local_case_declaration_batch_revision(request: Request, response: Response):
    """Advisory multi-root cascade preview; the later write rechecks under lock."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {"cases"})
    cases = payload["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        raise HTTPException(422, "1 to 4 exact case versions required")
    required = {"work_item_ref", "expected_revision", "expected_edit_token"}
    if any(not isinstance(case, dict) or set(case) != required for case in cases):
        raise HTTPException(422, "exact case version fields required")
    try:
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
        selected: set[str] = set()
        affected: set[str] = set()
        for case in cases:
            impact = project_declared_case_change_impact(
                snapshot,
                work_item_ref=case["work_item_ref"],
                expected_revision=case["expected_revision"],
                expected_edit_token=case["expected_edit_token"],
                operation="revise",
            )
            if case["work_item_ref"] in selected:
                raise CaseDeclarationError("duplicate case in batch preview")
            selected.add(case["work_item_ref"])
            affected.update(impact["affected_case_refs"])
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {
        "format": "openworkgraph.local-case-batch-revision-impact.v1",
        "reapproved_case_refs": sorted(selected),
        "expected_invalidated_work_item_refs": sorted(affected - selected),
        "invalidated_declaration_count": len(affected - selected),
        "advisory_snapshot_only": True,
        "requires_separate_confirmed_cas_write": True,
        "mutation_performed": False,
        "source_read_performed": False,
        "source_inventory_complete": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


@router.post("/v1/open-work/declarations/batch-revision")
async def revise_local_case_declaration_batch(request: Request, response: Response):
    """Explicit all-or-none reapproval of a reviewed DAG, including cascade."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "confirm_batch_revision", "confirm_cascade_invalidation",
        "expected_invalidated_work_item_refs", "items",
    })
    if (payload["confirm_batch_revision"] is not True or
        payload["confirm_cascade_invalidation"] is not True):
        raise HTTPException(403, "explicit graph revision and cascade confirmation required")
    await _recheck_source(scope)
    try:
        result = await asyncio.to_thread(
            revise_case_declaration_batch, scope, payload["items"],
            payload["expected_invalidated_work_item_refs"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/batch")
async def create_local_case_declaration_batch(request: Request, response: Response):
    """Explicit all-or-none local creation of a reviewed owner-declared DAG.

    Same source-derived scope, review/retention admission and private SQLite
    ledger as individual declarations. No provider IO or execution grant.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {"confirm_batch_create", "items"})
    if payload["confirm_batch_create"] is not True:
        raise HTTPException(403, "explicit batch owner declaration confirmation required")
    await _recheck_source(scope)
    try:
        result = await asyncio.to_thread(
            create_case_declaration_batch, scope, payload["items"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


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


@router.post("/v1/open-work/declarations/operation-timeline")
async def check_local_case_operation_timeline(request: Request, response: Response):
    """Explicitly append one independent provider check to a sealed portable history."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_verification", "record_link", "prior_timeline",
    })
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit independent provider verification required")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        link = payload["record_link"]
        resource_ref = validate_case_record_link(
            link, admitted, signature, scope=scope, secret=secret,
        )
        prior = payload["prior_timeline"]
        if prior is not None:
            checks = validate_prior_operation_timeline(
                prior, admitted, signature, resource_ref=resource_ref,
                scope=scope, secret=secret, link_expires_at=link["expires_at"],
            )
            if len(checks) >= 8:
                raise CaseDeclarationError("operation timeline full")
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # A bad record link, full history or stale/tampered prior history must not
    # trigger provider IO. Revalidate source and DAG after the source read.
    await _recheck_source(scope)
    try:
        receipt = await asyncio.to_thread(
            source_operation_receipt, resource_ref, "stripe_invoice_paid",
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        receipt = None
    await _recheck_source(scope)
    try:
        after = await asyncio.to_thread(list_case_declarations, scope)
        current, current_signature = admitted_case_dossier_snapshot(
            after, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        if current_signature != signature:
            raise CaseDeclarationError("stale operation timeline dependency graph changed")
        validate_case_record_link(
            link, current, current_signature, scope=scope, secret=secret,
        )
        result = append_operation_timeline(
            current, current_signature, resource_ref,
            project_case_operation_verification(current, resource_ref, receipt),
            scope=scope, secret=secret, link_expires_at=link["expires_at"],
            prior=prior,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


def _operation_ledger_guard(scope: str, work_item_ref: str, revision: int,
                            edit_token: str, signature: tuple) -> None:
    """Pre/post-write local re-admission, not a distributed revocation fence."""
    if _source_scope() != scope:
        raise CaseDeclarationError("source scope changed before evidence commit")
    current = list_case_declarations(scope)
    _admitted, current_signature = admitted_case_dossier_snapshot(
        current, work_item_ref=work_item_ref,
        expected_revision=revision, expected_edit_token=edit_token,
    )
    if current_signature != signature:
        raise CaseDeclarationError("reviewed dependency graph changed before evidence commit")


async def _operation_ledger_admission(scope: str, payload: dict[str, Any]):
    before = await asyncio.to_thread(list_case_declarations, scope)
    admitted, signature = admitted_case_dossier_snapshot(
        before, work_item_ref=payload["work_item_ref"],
        expected_revision=payload["expected_revision"],
        expected_edit_token=payload["expected_edit_token"],
    )
    secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
    from functools import partial
    guard = partial(
        _operation_ledger_guard, scope, payload["work_item_ref"],
        payload["expected_revision"], payload["expected_edit_token"], signature,
    )
    return admitted, signature, secret, guard


@router.post("/v1/open-work/declarations/operation-ledger/check")
async def check_local_operation_evidence_ledger(request: Request, response: Response):
    """Explicit source verification, followed by durable local evidence append."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_verification", "record_link",
    })
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit independent provider verification required")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    try:
        receipt = await asyncio.to_thread(
            source_operation_receipt, resource_ref, "stripe_invoice_paid",
        )
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        receipt = None
    await _recheck_source(scope)
    try:
        guard()
        validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        verified = project_case_operation_verification(admitted, resource_ref, receipt)
        result = await asyncio.to_thread(
            append_operation_evidence, admitted, signature, scope=scope,
            secret=secret, resource_ref=resource_ref, verification=verified, guard=guard,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/operation-ledger/history")
async def read_local_operation_evidence_ledger(request: Request, response: Response):
    """Read previously retained provider evidence, without a provider call."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
    })
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        await _recheck_source(scope)
        result = await asyncio.to_thread(
            read_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=guard,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/operation-ledger/dispute")
async def dispute_local_operation_evidence_ledger(request: Request, response: Response):
    """Append a reviewer's non-destructive dispute, not a provider correction."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_dispute", "target_sequence", "reason_code",
    })
    if payload["confirm_dispute"] is not True:
        raise HTTPException(403, "explicit human dispute confirmation required")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        await _recheck_source(scope)
        result = await asyncio.to_thread(
            dispute_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=guard, target_seq=payload["target_sequence"],
            reason=payload["reason_code"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/operation-ledger/dependency-history")
async def read_local_dependency_operation_history(request: Request, response: Response):
    """Inspect a reviewed prerequisite chain's retained provider checks only.

    No provider read or fresh completion claim. Every selected case is admitted
    against its own complete owner-reviewed DAG, with the root DAG pinned across
    each separate private-ledger read.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit historical evidence review required")
    count = payload["max_cases"]
    if type(count) is not int or not 1 <= count <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        selected, omitted = select_dependency_status_targets(admitted, max_cases=count)
        rows = {row["work_item_ref"]: row for row in before["items"]}
        # The current root signature includes every prerequisite, including
        # those not selected for this bounded history response.
        from functools import partial
        root_guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key",
        )
        targets = []
        for item in selected:
            ref = item["work_item_ref"]
            row = rows[ref]
            child, child_signature = admitted_case_dossier_snapshot(
                before, work_item_ref=ref,
                expected_revision=row["revision"],
                expected_edit_token=row["edit_token"],
            )
            child_guard = partial(
                _operation_ledger_guard, scope, ref, row["revision"],
                row["edit_token"], child_signature,
            )
            targets.append((child, child_signature, child_guard))
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    histories = []
    for child, child_signature, child_guard in targets:
        try:
            await asyncio.to_thread(root_guard)
            history = await asyncio.to_thread(
                read_operation_evidence, child, child_signature,
                scope=scope, secret=secret, guard=child_guard,
            )
            await asyncio.to_thread(root_guard)
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        histories.append(history)
        await _recheck_source(scope)
    try:
        await asyncio.to_thread(root_guard)
        result = compose_dependency_operation_history(
            admitted, signature, selected, histories,
            unscanned_prerequisites=omitted,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/assess")
async def assess_local_human_case_outcome(request: Request, response: Response):
    """Record explicit human feedback; NOT independently verified completion."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_human_assessment", "human_status", "reason_code",
    })
    if payload["confirm_human_assessment"] is not True:
        raise HTTPException(403, "explicit human outcome assessment required")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        await _recheck_source(scope)
        result = await asyncio.to_thread(
            record_human_outcome, admitted, signature, scope=scope,
            secret=secret, guard=guard, human_status=payload["human_status"],
            reason_code=payload["reason_code"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/history")
async def read_local_human_case_outcomes(request: Request, response: Response):
    """Read retained human assessments without contacting any provider."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
    })
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        await _recheck_source(scope)
        result = await asyncio.to_thread(
            read_human_outcomes, admitted, signature, scope=scope,
            secret=secret, guard=guard,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/correct")
async def correct_local_human_case_outcome(request: Request, response: Response):
    """Append an explicit correction to the latest human assessment."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_human_correction", "target_sequence", "human_status",
    })
    if payload["confirm_human_correction"] is not True:
        raise HTTPException(403, "explicit human outcome correction required")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        await _recheck_source(scope)
        result = await asyncio.to_thread(
            correct_human_outcome, admitted, signature, scope=scope,
            secret=secret, guard=guard, target_seq=payload["target_sequence"],
            human_status=payload["human_status"],
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/dependency-history")
async def read_local_dependency_human_outcomes(request: Request, response: Response):
    """Inspect human feedback across a reviewed DAG, never verified completion.

    Every selected prerequisite is separately admitted. Root admission is
    rechecked before and after each local SQLite read; separate reads are
    expressly not an atomic cross-process source-grant snapshot.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit human outcome history review required")
    count = payload["max_cases"]
    if type(count) is not int or not 1 <= count <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        selected, omitted = select_dependency_status_targets(admitted, max_cases=count)
        rows = {row["work_item_ref"]: row for row in before["items"]}
        from functools import partial
        root_guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key",
        )
        targets = []
        for item in selected:
            ref = item["work_item_ref"]
            row = rows[ref]
            child, child_signature = admitted_case_dossier_snapshot(
                before, work_item_ref=ref,
                expected_revision=row["revision"],
                expected_edit_token=row["edit_token"],
            )
            child_guard = partial(
                _operation_ledger_guard, scope, ref, row["revision"],
                row["edit_token"], child_signature,
            )
            targets.append((child, child_signature, child_guard))
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    histories = []
    for child, child_signature, child_guard in targets:
        try:
            await asyncio.to_thread(root_guard)
            history = await asyncio.to_thread(
                read_human_outcomes, child, child_signature,
                scope=scope, secret=secret, guard=child_guard,
            )
            await asyncio.to_thread(root_guard)
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        histories.append(history)
        await _recheck_source(scope)
    try:
        await asyncio.to_thread(root_guard)
        result = compose_dependency_human_outcomes(
            admitted, signature, selected, histories,
            unscanned_prerequisites=omitted,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/evidence-triage")
async def read_local_outcome_evidence_triage(request: Request, response: Response):
    """Explicit review of both historical ledgers for a bounded reviewed DAG.

    A provider payment receipt and a human assessment are distinct facts.
    This endpoint neither calls a provider nor verifies customer completion.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit outcome evidence history review required")
    count = payload["max_cases"]
    if type(count) is not int or not 1 <= count <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        selected, omitted = select_dependency_status_targets(admitted, max_cases=count)
        rows = {row["work_item_ref"]: row for row in before["items"]}
        from functools import partial
        root_guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key",
        )
        targets = []
        for item in selected:
            ref = item["work_item_ref"]
            row = rows[ref]
            child, child_signature = admitted_case_dossier_snapshot(
                before, work_item_ref=ref,
                expected_revision=row["revision"], expected_edit_token=row["edit_token"],
            )
            child_guard = partial(
                _operation_ledger_guard, scope, ref, row["revision"],
                row["edit_token"], child_signature,
            )
            targets.append((child, child_signature, child_guard))
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    provider_histories = []
    human_histories = []
    for child, child_signature, child_guard in targets:
        try:
            await asyncio.to_thread(root_guard)
            provider = await asyncio.to_thread(
                read_operation_evidence, child, child_signature,
                scope=scope, secret=secret, guard=child_guard,
            )
            await asyncio.to_thread(root_guard)
            await _recheck_source(scope)
            human = await asyncio.to_thread(
                read_human_outcomes, child, child_signature,
                scope=scope, secret=secret, guard=child_guard,
            )
            await asyncio.to_thread(root_guard)
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        provider_histories.append(provider)
        human_histories.append(human)
        await _recheck_source(scope)
    try:
        await asyncio.to_thread(root_guard)
        result = compose_outcome_evidence_triage(
            admitted, signature, selected, provider_histories, human_histories,
            unscanned_prerequisites=omitted,
        )
        await asyncio.to_thread(root_guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result
