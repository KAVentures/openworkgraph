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
from .open_work_case_run_outcome_review import read_imported_run, compose_run_outcome_review
from .open_work_run_case_binding import clear_binding_scope
from .open_work_case_review_actions import compose_outcome_review_action_plan
from .open_work_case_review_progress import (
    issue_review_progress, validate_review_progress, compare_review_progress,
)
from .open_work_case_fresh_outcome_triage import compose_fresh_outcome_triage
from .open_work_case_outcome_portfolio import (
    portfolio_fingerprint, validate_page_token, compose_outcome_portfolio,
)
from .open_work_case_declared_outcome import (
    require_declared_payment_goal, compose_fresh_declared_payment_goal,
    compose_historical_declared_payment_goal,
)
from .open_work_case_declared_dependency_payment import (
    admit_declared_dependency_payment_links, same_admitted_payment_chain,
    compose_declared_dependency_payment_result,
)
from .open_work_case_declared_payment_preflight import (
    issue_payment_preflight, validate_payment_preflight,
)
from .open_work_case_declared_portfolio_payment import (
    admit_selected_portfolio_payment_links,
    same_selected_portfolio_payment_reviews,
    compose_selected_portfolio_payment_result,
)
from .open_work_case_declared_portfolio_preflight import (
    issue_portfolio_payment_preflight, validate_portfolio_payment_preflight,
)
from .open_work_case_declared_link_renewal import (
    validate_historical_payment_record_link, renew_declared_payment_record_link,
)
from .open_work_case_review_packet import (
    issue_review_packet, validate_review_packet, compare_review_packet,
)
from .open_work_case_operation_ledger import (
    append_operation_evidence, append_operation_evidence_batch,
    read_operation_evidence, dispute_operation_evidence,
    clear_operation_evidence,
)
from .open_work_case_outcome_reviews import (
    record_human_outcome, record_human_outcomes_batch, correct_human_outcome, read_human_outcomes,
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
        # "review packet" names the sealed packet object, not a missing review.
        if re.search(r"\b(review|retention)\b", message.replace("review packet", "packet")):
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


async def _body(request: Request, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
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
    if (not isinstance(data, dict) or
        (set(data) != required if optional is None else
         not (required <= set(data) <= required | optional))):
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
        # Revoke every actor's durable run/case associations for the old scope.
        # Failure is fail-closed: do not return an apparently valid response.
        await asyncio.to_thread(clear_binding_scope, scope)
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
    }, optional={"expected_preflight"})
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



@router.post("/v1/open-work/declarations/human-outcomes/assess-dependency-batch")
async def assess_local_dependency_human_outcomes_batch(request: Request, response: Response):
    """One explicit human assessment per selected DAG case, all-or-none locally.

    This is a reviewed historical human assertion, not a provider/customer
    completion or agent authority. It never contacts Stripe.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_human_assessment", "max_cases", "assessments",
    })
    if payload["confirm_human_assessment"] is not True:
        raise HTTPException(403, "explicit human batch assessment required")
    count = payload["max_cases"]
    supplied = payload["assessments"]
    if (type(count) is not int or not 1 <= count <= 4 or
            not isinstance(supplied, list) or not 1 <= len(supplied) <= 4):
        raise HTTPException(422, "invalid bounded human assessment batch")
    required = {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "human_status", "reason_code", "expected_event_count",
    }
    if any(not isinstance(row, dict) or set(row) != required for row in supplied):
        raise HTTPException(422, "exact per-case assessment fields required")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted, signature = admitted_case_dossier_snapshot(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
        )
        selected, omitted = select_dependency_status_targets(admitted, max_cases=count)
        if omitted or len(selected) != len(supplied):
            raise CaseDeclarationError("incomplete reviewed dependency assessment selection")
        refs = [row["work_item_ref"] for row in selected]
        if [row["work_item_ref"] for row in supplied] != refs:
            raise CaseDeclarationError("changed or reordered prerequisite assessment selection")
        rows = {row["work_item_ref"]: row for row in before["items"]}
        from functools import partial
        root_guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
        targets = []
        for item, assessment in zip(selected, supplied):
            ref = item["work_item_ref"]
            row = rows[ref]
            if (assessment["expected_revision"] != row["revision"] or
                    assessment["expected_edit_token"] != row["edit_token"] or
                    type(assessment["expected_revision"]) is not int or
                    not isinstance(assessment["expected_edit_token"], str)):
                raise CaseDeclarationError("stale dependency assessment CAS")
            child, child_signature = admitted_case_dossier_snapshot(
                before, work_item_ref=ref,
                expected_revision=row["revision"], expected_edit_token=row["edit_token"],
            )
            child_guard = partial(
                _operation_ledger_guard, scope, ref, row["revision"],
                row["edit_token"], child_signature,
            )
            targets.append((
                child, child_signature, child_guard,
                assessment["human_status"], assessment["reason_code"],
                assessment["expected_event_count"],
            ))
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key",
        )
        await asyncio.to_thread(root_guard)
        await _recheck_source(scope)
        histories = await asyncio.to_thread(
            record_human_outcomes_batch, targets, scope=scope, secret=secret,
        )
        await asyncio.to_thread(root_guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {
        "format": "openworkgraph.local-dependency-human-assessment-batch.v1",
        "work_item_ref": payload["work_item_ref"],
        "case_histories": histories,
        "assessed_case_count": len(histories),
        "prerequisite_first": True,
        "local_sqlite_batch_atomic": True,
        "historical_human_assertions_only": True,
        "provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "not_synced": True,
    }


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


async def _load_review_triage(scope: str, payload: dict[str, Any]):
    """Re-admit each case and independently verify both retained histories."""
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
    return result, admitted, signature, secret


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
    result, _admitted, _signature, _secret = await _load_review_triage(scope, payload)
    try:
        action_plan = compose_outcome_review_action_plan(result)
    except (CaseDeclarationError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {**result, "review_action_plan": action_plan}



@router.post("/v1/open-work/declarations/human-outcomes/compare-imported-run")
async def compare_local_imported_run_to_outcome(request: Request, response: Response):
    """Explicitly compare one imported self-reported run with admitted case histories.

    An operator selection is not a verified run-to-case identity, customer
    completion or agent causality. This route never calls Stripe or writes.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_run_comparison",
        "max_cases", "deployment_id", "run_id",
    })
    if payload["confirm_historical_review"] is not True or payload["confirm_run_comparison"] is not True:
        raise HTTPException(403, "explicit run and historical outcome review required")
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret,
        )
        plan = compose_outcome_review_action_plan(triage)
        result = compose_run_outcome_review(run, triage, plan)
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    # The run import and both independently retained evidence ledgers can
    # change between reads. Refuse rather than return a misleading join.
    repeated, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
    try:
        second_run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret,
        )
        if (repeated != triage or refreshed_signature != signature or
                refreshed["work_item_ref"] != admitted["work_item_ref"] or
                second_run != run):
            raise CaseDeclarationError("run or reviewed outcome evidence changed during comparison")
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/imported-run-binding")
async def manage_local_imported_run_binding(request: Request, response: Response):
    """Session-scoped operator binding; never independent run identity or success."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_run_comparison",
        "max_cases", "deployment_id", "run_id", "action",
    })
    if payload["confirm_historical_review"] is not True or payload["confirm_run_comparison"] is not True:
        raise HTTPException(403, "explicit run and historical review required")
    if payload["action"] not in ("bind", "read", "revoke"):
        raise HTTPException(422, "invalid binding action")
    from .open_work_run_case_binding import bind_run, get_binding, revoke_binding
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        run = await asyncio.to_thread(read_imported_run,
            deployment_id=payload["deployment_id"], run_id=payload["run_id"],
            scope=scope, secret=secret)
        await asyncio.to_thread(_operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    repeated, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
    try:
        second_run = await asyncio.to_thread(read_imported_run,
            deployment_id=payload["deployment_id"], run_id=payload["run_id"],
            scope=scope, secret=secret)
        if (repeated != triage or refreshed_signature != signature
                or refreshed["work_item_ref"] != admitted["work_item_ref"]
                or second_run != run):
            raise CaseDeclarationError("run or case review changed during binding")
        await asyncio.to_thread(_operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    actor = hashlib.sha256(request.headers["authorization"].encode("utf-8")).hexdigest()
    fields = dict(scope=scope, actor=actor, secret=secret,
        case_ref=payload["work_item_ref"], run_ref=run["run_ref"])
    try:
        if payload["action"] == "revoke":
            result = {"revoked": await asyncio.to_thread(revoke_binding, **fields)}
        else:
            fields.update(fingerprint=run["run_snapshot_fingerprint"],
                          revision=payload["expected_revision"])
            result = await asyncio.to_thread(
                bind_run if payload["action"] == "bind" else get_binding, **fields)
        await asyncio.to_thread(_operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {"binding": result, "run_case_identity_independently_verified": False,
            "customer_outcome_verified": False, "agent_execution_authorized": False}


@router.post("/v1/open-work/declarations/human-outcomes/revoke-case-run-bindings")
async def revoke_local_case_run_bindings(request: Request, response: Response):
    """Explicit operator-only removal of all own run associations for one case.

    Does not require a remembered imported run ID or grant any run/outcome authority.
    Other sessions, cases and source scopes are never targeted.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_case_binding_revocation", "max_cases",
    })
    if (payload["confirm_historical_review"] is not True
            or payload["confirm_case_binding_revocation"] is not True):
        raise HTTPException(403, "explicit case run-binding revocation review required")
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
        await _recheck_source(scope)
        repeated, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
        if (triage != repeated or signature != refreshed_signature
                or refreshed["work_item_ref"] != admitted["work_item_ref"]):
            raise CaseDeclarationError("case review changed during revocation")
        actor = hashlib.sha256(request.headers["authorization"].encode("utf-8")).hexdigest()
        from .open_work_run_case_binding import revoke_case_bindings
        removed = await asyncio.to_thread(
            revoke_case_bindings, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"],
        )
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {"revoked_count": removed, "association": "operator_confirmed",
            "run_case_identity_independently_verified": False,
            "customer_outcome_verified": False, "agent_execution_authorized": False}


@router.post("/v1/open-work/declarations/human-outcomes/progress-checkpoint")
async def issue_local_outcome_review_progress(request: Request, response: Response):
    """Seal historical human/provider review and unresolved action baseline."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit historical outcome review required")
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        result = issue_review_progress(
            triage, admitted, signature, scope=scope, secret=secret,
            max_cases=payload["max_cases"],
        )
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    repeated, _a, _s, _secret = await _load_review_triage(scope, payload)
    if repeated != triage:
        raise HTTPException(409, "historical review changed during checkpoint")
    try:
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/progress-compare")
async def compare_local_outcome_review_progress(request: Request, response: Response):
    """Compare two historical reviews; no provider read or outcome certification."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token", "checkpoint",
    })
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        validated = validate_review_progress(
            payload["checkpoint"], admitted, signature, scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    request_payload = {**payload, "max_cases": validated["max_cases"]}
    triage, refreshed, current_signature, current_secret = await _load_review_triage(
        scope, request_payload,
    )
    try:
        result = compare_review_progress(
            payload["checkpoint"], triage, refreshed, current_signature,
            scope=scope, secret=current_secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    # A second independent HMAC-ledger read detects intervening edits during
    # the first comparison. This is not an atomic cross-store snapshot.
    repeated, _a, _s, _secret = await _load_review_triage(scope, request_payload)
    if repeated != triage:
        raise HTTPException(409, "historical review changed during comparison")
    try:
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/review-packet")
async def issue_local_outcome_review_packet(request: Request, response: Response):
    """Explicitly seal the current minimized historical review for later comparison."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit historical outcome review required")
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        result = issue_review_packet(
            triage, admitted, signature, scope=scope, secret=secret,
            max_cases=payload["max_cases"],
        )
        await asyncio.to_thread(_operation_ledger_guard, scope,
            payload["work_item_ref"], payload["expected_revision"],
            payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/verify-review-packet")
async def verify_local_outcome_review_packet(request: Request, response: Response):
    """Re-admit exact review and both ledgers; never recheck provider or infer success."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token", "packet",
    })
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        validated = validate_review_packet(
            payload["packet"], admitted, signature, scope=scope, secret=secret,
        )
        # Reject a forged or stale packet before reading either private ledger.
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    triage, refreshed, current_signature, current_secret = await _load_review_triage(
        scope, {**payload, "max_cases": validated["max_cases"]},
    )
    try:
        validate_review_packet(
            payload["packet"], refreshed, current_signature,
            scope=scope, secret=current_secret,
        )
        result = compare_review_packet(payload["packet"], triage)
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/fresh-evidence-triage")
async def check_local_fresh_outcome_evidence_triage(request: Request, response: Response):
    """One explicitly authorized fresh Stripe operation check + historical review.

    The signed record link is validated before the source read. A provider
    receipt proves only its narrowly scoped operation, not customer success.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_source_verification",
        "record_link", "max_cases",
    })
    if payload["confirm_historical_review"] is not True or payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit history review and source verification required")
    if type(payload["max_cases"]) is not int or not 1 <= payload["max_cases"] <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    # Independently read both HMAC-protected histories for each selected case.
    # An invalid link or revoked declaration must not cause provider access.
    historical_before, before_case, before_signature, _ = await _load_review_triage(scope, payload)
    try:
        if before_signature != signature or before_case["work_item_ref"] != admitted["work_item_ref"]:
            raise CaseDeclarationError("reviewed outcome dependency graph changed")
        await asyncio.to_thread(guard)
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
        await asyncio.to_thread(guard)
        validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # Re-read the full selected history after source IO. A concurrent human
    # correction or provider dispute invalidates this combined review.
    historical_after, after_case, after_signature, _ = await _load_review_triage(scope, payload)
    try:
        if (after_signature != signature
                or after_case["work_item_ref"] != admitted["work_item_ref"]
                or historical_after != historical_before):
            raise CaseDeclarationError("historical outcome review changed during source verification")
        verified = project_case_operation_verification(admitted, resource_ref, receipt)
        result = compose_fresh_outcome_triage(historical_after, verified)
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/portfolio-triage")
async def read_local_outcome_portfolio_triage(request: Request, response: Response):
    """Review the bounded CURRENT owner-declared portfolio, not one case DAG.

    Page tokens bind the entire approved portfolio's incarnation/revisions,
    not just the returned page. Each case's full prerequisite DAG is admitted
    independently before its two HMAC-validated historical ledger reads.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "confirm_historical_review", "max_cases", "offset",
        "expected_portfolio_digest",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit portfolio outcome history review required")
    count, offset = payload["max_cases"], payload["offset"]
    if (type(count) is not int or not 1 <= count <= 10 or
            type(offset) is not int or not 0 <= offset <= 50):
        raise HTTPException(422, "bounded portfolio page required")
    try:
        before = await asyncio.to_thread(list_case_declarations, scope)
        secret = await asyncio.to_thread(
            _read_or_create_secret, ".open_work_reference_key",
        )
        digest = portfolio_fingerprint(before, scope=scope, secret=secret)
        validate_page_token(payload["expected_portfolio_digest"], digest, offset=offset)
        rows = sorted(before["items"], key=lambda row: row["work_item_ref"])
        if offset > len(rows):
            raise CaseDeclarationError("stale portfolio page offset")
        from functools import partial
        targets = []
        for row in rows[offset:offset + count]:
            ref = row["work_item_ref"]
            admitted, signature = admitted_case_dossier_snapshot(
                before, work_item_ref=ref,
                expected_revision=row["revision"],
                expected_edit_token=row["edit_token"],
            )
            guard = partial(
                _operation_ledger_guard, scope, ref,
                row["revision"], row["edit_token"], signature,
            )
            targets.append((row, admitted, signature, guard))
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    entries = []
    for row, admitted, signature, guard in targets:
        try:
            await asyncio.to_thread(guard)
            provider = await asyncio.to_thread(
                read_operation_evidence, admitted, signature,
                scope=scope, secret=secret, guard=guard,
            )
            await asyncio.to_thread(guard)
            await _recheck_source(scope)
            human = await asyncio.to_thread(
                read_human_outcomes, admitted, signature,
                scope=scope, secret=secret, guard=guard,
            )
            await asyncio.to_thread(guard)
            current = await asyncio.to_thread(list_case_declarations, scope)
            if portfolio_fingerprint(current, scope=scope, secret=secret) != digest:
                raise CaseDeclarationError("stale reviewed portfolio changed during history read")
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        await _recheck_source(scope)
        entries.append((row, provider, human))
    try:
        current = await asyncio.to_thread(list_case_declarations, scope)
        if portfolio_fingerprint(current, scope=scope, secret=secret) != digest:
            raise CaseDeclarationError("stale reviewed portfolio changed during history read")
        result = compose_outcome_portfolio(
            entries, total=len(rows), offset=offset, fingerprint=digest,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/verify-payment")
async def verify_local_declared_stripe_payment_goal(request: Request, response: Response):
    """Explicit fresh provider read + durable check for a narrowly declared goal.

    A Stripe-reported paid invoice can satisfy only the owner-declared
    "stripe_invoice_paid_reported" provider goal, not customer/bank success.
    """
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
        require_declared_payment_goal(admitted)
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        # Read and HMAC-validate the prior private evidence before any source IO.
        # This snapshot is also an exact optimistic checkpoint for the SQLite
        # append transaction, not merely an unprotected response comparison.
        before = await asyncio.to_thread(
            read_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=guard,
        )
        await asyncio.to_thread(guard)
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
        await asyncio.to_thread(guard)
        validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        verified = project_case_operation_verification(admitted, resource_ref, receipt)
        history = await asyncio.to_thread(
            append_operation_evidence, admitted, signature, scope=scope,
            secret=secret, resource_ref=resource_ref, verification=verified, guard=guard,
            expected_history=before["events"],
        )
        result = compose_fresh_declared_payment_goal(
            admitted, verified, history, prior_history=before,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/payment-history")
async def read_local_declared_stripe_payment_goal_history(request: Request, response: Response):
    """Read private durable checks; a historical paid receipt is not current."""
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
    })
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        require_declared_payment_goal(admitted)
        await _recheck_source(scope)
        history = await asyncio.to_thread(
            read_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=guard,
        )
        result = compose_historical_declared_payment_goal(admitted, history)
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/renew-record-link")
async def renew_local_declared_payment_record_link(request: Request, response: Response):
    """Renew a historical invoice link after a retained independent check.

    This uses only a current reviewed case and its private HMAC evidence. It
    neither scans Stripe's open inventory nor reads payment status; the
    separate explicit verify-payment API still performs the fresh source read.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "record_link",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit historical payment link renewal review required")
    try:
        admitted, signature, secret, guard = await _operation_ledger_admission(scope, payload)
        require_declared_payment_goal(admitted)
        # Authenticate the expired original before reading any private ledger.
        resource_ref = validate_historical_payment_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
        await _recheck_source(scope)
        history = await asyncio.to_thread(
            read_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=guard, resource_ref=resource_ref,
        )
        await asyncio.to_thread(guard)
        result = renew_declared_payment_record_link(
            admitted, signature, payload["record_link"], history,
            scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/preview-dependency-payments")
async def preview_local_declared_dependency_payment_goals(request: Request, response: Response):
    """Plan the exact approved 1–4 case Stripe verification without source IO.

    This is historical evidence only. A signed five-minute checkpoint may be
    supplied to the existing verify route to reject changed reviews/evidence
    before ANY provider read; it never grants verification or agent authority.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "record_links",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit payment evidence planning review required")
    try:
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
        selected = admit_declared_dependency_payment_links(
            snapshot, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
            record_links=payload["record_links"], scope=scope, secret=secret,
        )
        from functools import partial
        guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"],
            selected["root_signature"],
        )
        histories = {}
        for ref in selected["ordered"]:
            case = selected["cases"][ref]
            histories[ref] = await asyncio.to_thread(
                read_operation_evidence, case["admitted"], case["signature"],
                scope=scope, secret=secret, guard=guard,
                resource_ref=case["resource_ref"],
            )
            await asyncio.to_thread(guard)
            await _recheck_source(scope)
        result = issue_payment_preflight(
            selected, histories, scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/verify-dependency-payments")
async def verify_local_declared_dependency_payment_goals(request: Request, response: Response):
    """Freshly check every approved payment-goal prerequisite; persist atomically.

    All source links and historical evidence are validated before the first
    provider read. Source reads are sequential, NOT a provider-atomic snapshot.
    The local HMAC evidence appends commit together or not at all.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_source_verification", "record_links",
    }, {"expected_preflight"})
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit dependency provider verification required")
    try:
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        before = await asyncio.to_thread(list_case_declarations, scope)
        admitted = admit_declared_dependency_payment_links(
            before, work_item_ref=payload["work_item_ref"],
            expected_revision=payload["expected_revision"],
            expected_edit_token=payload["expected_edit_token"],
            record_links=payload["record_links"], scope=scope, secret=secret,
        )
        from functools import partial
        guard = partial(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"],
            admitted["root_signature"],
        )
        # Preflight ALL private HMAC chains, even for the last dependency,
        # before making ANY external provider request.
        previous = {}
        for ref in admitted["ordered"]:
            case = admitted["cases"][ref]
            previous[ref] = await asyncio.to_thread(
                read_operation_evidence, case["admitted"], case["signature"],
                scope=scope, secret=secret, guard=guard,
                resource_ref=case["resource_ref"],
            )
        await asyncio.to_thread(guard)
        if "expected_preflight" in payload:
            validate_payment_preflight(
                payload["expected_preflight"], admitted, previous,
                scope=scope, secret=secret,
            )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    verifications = {}
    for ref in admitted["ordered"]:
        case = admitted["cases"][ref]
        try:
            receipt = await asyncio.to_thread(
                source_operation_receipt, case["resource_ref"], "stripe_invoice_paid",
            )
        except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
            receipt = None
        await _recheck_source(scope)
        try:
            current = await asyncio.to_thread(list_case_declarations, scope)
            rechecked = admit_declared_dependency_payment_links(
                current, work_item_ref=payload["work_item_ref"],
                expected_revision=payload["expected_revision"],
                expected_edit_token=payload["expected_edit_token"],
                record_links=payload["record_links"], scope=scope, secret=secret,
            )
            if not same_admitted_payment_chain(admitted, rechecked):
                raise CaseDeclarationError("stale declared payment dependency review changed")
            verifications[ref] = project_case_operation_verification(
                case["admitted"], case["resource_ref"], receipt,
            )
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
    await _recheck_source(scope)
    try:
        await asyncio.to_thread(guard)
        entries = [{
            "admitted": admitted["cases"][ref]["admitted"],
            "signature": admitted["cases"][ref]["signature"],
            "resource_ref": admitted["cases"][ref]["resource_ref"],
            "verification": verifications[ref],
            "expected_history": previous[ref]["events"],
        } for ref in admitted["ordered"]]
        current_histories = await asyncio.to_thread(
            append_operation_evidence_batch, entries,
            scope=scope, secret=secret, guard=guard,
        )
        result = compose_declared_dependency_payment_result(
            root_ref=payload["work_item_ref"], admitted=admitted,
            previous=previous, current=current_histories,
            verifications=verifications,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/preview-portfolio-payments")
async def preview_local_declared_portfolio_payment_goals(request: Request, response: Response):
    """Review selected historical evidence and issue a short-lived stale-check seal.

    This explicit local planning action never contacts Stripe or appends evidence.
    It does not reserve global ledger capacity or authorize fresh verification.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {"confirm_historical_review", "selections"})
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit portfolio historical payment review required")
    try:
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        snapshot = await asyncio.to_thread(list_case_declarations, scope)
        selected = admit_selected_portfolio_payment_links(
            snapshot, selections=payload["selections"], scope=scope, secret=secret,
        )

        def guard() -> None:
            if _source_scope() != scope:
                raise CaseDeclarationError("source scope changed during portfolio preflight")
            current = list_case_declarations(scope)
            rechecked = admit_selected_portfolio_payment_links(
                current, selections=payload["selections"], scope=scope, secret=secret,
            )
            if not same_selected_portfolio_payment_reviews(selected, rechecked):
                raise CaseDeclarationError("stale selected portfolio reviews changed")

        histories = {}
        for ref in selected["ordered"]:
            case = selected["cases"][ref]
            histories[ref] = await asyncio.to_thread(
                read_operation_evidence, case["admitted"], case["signature"],
                scope=scope, secret=secret, guard=guard,
                resource_ref=case["resource_ref"],
            )
            await asyncio.to_thread(guard)
            await _recheck_source(scope)
        result = issue_portfolio_payment_preflight(
            selected, histories, scope=scope, secret=secret,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/source-goals/verify-portfolio-payments")
async def verify_local_declared_portfolio_payment_goals(request: Request, response: Response):
    """Check 2–4 independently selected reviewed invoices, then commit atomically.

    This is an explicit operator action, not a scheduled portfolio scan. Every
    selected root and full reviewed prerequisite DAG, signed invoice link and
    private ledger must pass preflight before the first Stripe provider read.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(
        request, {"confirm_source_verification", "selections"}, {"expected_preflight"},
    )
    if payload["confirm_source_verification"] is not True:
        raise HTTPException(403, "explicit portfolio source verification required")
    try:
        secret = await asyncio.to_thread(_read_or_create_secret, ".open_work_reference_key")
        before = await asyncio.to_thread(list_case_declarations, scope)
        selected = admit_selected_portfolio_payment_links(
            before, selections=payload["selections"], scope=scope, secret=secret,
        )

        def guard() -> None:
            if _source_scope() != scope:
                raise CaseDeclarationError("source scope changed during portfolio verification")
            current = list_case_declarations(scope)
            rechecked = admit_selected_portfolio_payment_links(
                current, selections=payload["selections"], scope=scope, secret=secret,
            )
            if not same_selected_portfolio_payment_reviews(selected, rechecked):
                raise CaseDeclarationError("stale selected portfolio reviews changed")

        # Validate ALL HMAC histories and exact record bindings before provider IO.
        previous = {}
        for ref in selected["ordered"]:
            case = selected["cases"][ref]
            previous[ref] = await asyncio.to_thread(
                read_operation_evidence, case["admitted"], case["signature"],
                scope=scope, secret=secret, guard=guard,
                resource_ref=case["resource_ref"],
            )
        await asyncio.to_thread(guard)
        if "expected_preflight" in payload:
            validate_portfolio_payment_preflight(
                payload["expected_preflight"], selected, previous,
                scope=scope, secret=secret,
            )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)

    verifications = {}
    for ref in selected["ordered"]:
        case = selected["cases"][ref]
        try:
            receipt = await asyncio.to_thread(
                source_operation_receipt, case["resource_ref"], "stripe_invoice_paid",
            )
        except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
            receipt = None
        await _recheck_source(scope)
        try:
            await asyncio.to_thread(guard)
            verifications[ref] = project_case_operation_verification(
                case["admitted"], case["resource_ref"], receipt,
            )
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
    await _recheck_source(scope)
    try:
        await asyncio.to_thread(guard)
        entries = [{
            "admitted": selected["cases"][ref]["admitted"],
            "signature": selected["cases"][ref]["signature"],
            "resource_ref": selected["cases"][ref]["resource_ref"],
            "verification": verifications[ref],
            "expected_history": previous[ref]["events"],
        } for ref in selected["ordered"]]
        current_histories = await asyncio.to_thread(
            append_operation_evidence_batch, entries,
            scope=scope, secret=secret, guard=guard,
        )
        result = compose_selected_portfolio_payment_result(
            selected=selected, previous=previous,
            current=current_histories, verifications=verifications,
        )
        await asyncio.to_thread(guard)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/v1/open-work/declarations/human-outcomes/case-run-bindings")
async def list_local_case_run_bindings(request: Request, response: Response):
    """Authenticated, explicit inventory of this session's current case bindings.

    Inventory does not revalidate imported run snapshots. The operator must
    use the existing exact-run comparison before interpreting any association.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "max_cases",
    })
    if payload["confirm_historical_review"] is not True:
        raise HTTPException(403, "explicit historical review required")
    from .open_work_run_case_binding import list_case_bindings
    triage, admitted, signature, secret = await _load_review_triage(scope, payload)
    await _recheck_source(scope)
    actor = hashlib.sha256(request.headers["authorization"].encode("utf-8")).hexdigest()
    try:
        inventory = await asyncio.to_thread(
            list_case_bindings, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"], revision=payload["expected_revision"],
        )
        repeated, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
        if (triage != repeated or refreshed_signature != signature or
                refreshed["work_item_ref"] != admitted["work_item_ref"]):
            raise CaseDeclarationError("stale case review changed during inventory")
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature,
        )
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return inventory


@router.post("/v1/open-work/declarations/human-outcomes/verify-bound-run-source")
async def verify_local_bound_run_source(request: Request, response: Response):
    """Compare a current operator-bound imported run with fresh provider evidence.

    A source-confirmed invoice operation is NOT an agent-caused action or a
    customer outcome. This is a read-only, explicitly requested comparison.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_run_comparison",
        "confirm_source_verification", "max_cases", "deployment_id",
        "run_id", "record_link",
    }, optional={"persist_provider_check"})
    persist = payload.get("persist_provider_check", False)
    if type(persist) is not bool:
        raise HTTPException(422, "persist_provider_check must be boolean")
    if (payload["confirm_historical_review"] is not True
            or payload["confirm_run_comparison"] is not True
            or payload["confirm_source_verification"] is not True):
        raise HTTPException(403, "explicit run, history and source review required")
    if type(payload["max_cases"]) is not int or not 1 <= payload["max_cases"] <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    from .open_work_run_case_binding import get_binding

    # Admit the entire declared graph, both retained histories, signed record
    # link, imported run and actor-owned binding BEFORE provider network IO.
    historical, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret)
        run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret)
        actor = hashlib.sha256(request.headers["authorization"].encode("utf-8")).hexdigest()
        binding = await asyncio.to_thread(
            get_binding, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"], run_ref=run["run_ref"],
            fingerprint=run["run_snapshot_fingerprint"],
            revision=payload["expected_revision"])
        if binding is None:
            raise CaseDeclarationError("explicit current run/case binding review required")
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    # A durable check is an explicit second action, never the default behavior.
    # Snapshot the private HMAC ledger BEFORE network IO for optimistic CAS.
    prior_evidence = None
    if persist:
        try:
            prior_evidence = await asyncio.to_thread(
                read_operation_evidence, admitted, signature, scope=scope,
                secret=secret, guard=lambda: _operation_ledger_guard(
                    scope, payload["work_item_ref"], payload["expected_revision"],
                    payload["expected_edit_token"], signature))
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
    await _recheck_source(scope)
    # Provider failure yields unknown, never an inferred failure or success.
    try:
        receipt = await asyncio.to_thread(
            source_operation_receipt, resource_ref, "stripe_invoice_paid")
    except (ValueError, OSError, TypeError, CaseDeclarationError, sqlite3.DatabaseError):
        receipt = None
    await _recheck_source(scope)
    refreshed_history, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
    try:
        if (refreshed_history != historical or refreshed_signature != signature
                or refreshed["work_item_ref"] != admitted["work_item_ref"]):
            raise CaseDeclarationError("historical case review changed during source verification")
        validate_case_record_link(
            payload["record_link"], refreshed, refreshed_signature,
            scope=scope, secret=secret)
        second_run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret)
        second_binding = await asyncio.to_thread(
            get_binding, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"], run_ref=second_run["run_ref"],
            fingerprint=second_run["run_snapshot_fingerprint"],
            revision=payload["expected_revision"])
        if second_run != run or second_binding != binding:
            raise CaseDeclarationError("run binding changed during source verification")
        verified = project_case_operation_verification(refreshed, resource_ref, receipt)
        triage = compose_fresh_outcome_triage(refreshed_history, verified)
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    persisted_history = None
    if persist:
        # The case ledger records provider state, not independent run identity.
        # Recheck the run binding and case immediately before the guarded CAS.
        try:
            await asyncio.to_thread(
                _operation_ledger_guard, scope, payload["work_item_ref"],
                payload["expected_revision"], payload["expected_edit_token"], signature)
            latest_run = await asyncio.to_thread(
                read_imported_run, deployment_id=payload["deployment_id"],
                run_id=payload["run_id"], scope=scope, secret=secret)
            latest_binding = await asyncio.to_thread(
                get_binding, scope=scope, actor=actor, secret=secret,
                case_ref=payload["work_item_ref"], run_ref=latest_run["run_ref"],
                fingerprint=latest_run["run_snapshot_fingerprint"],
                revision=payload["expected_revision"])
            if latest_run != run or latest_binding != binding:
                raise CaseDeclarationError("run binding changed before evidence append")
            persisted_history = await asyncio.to_thread(
                append_operation_evidence, refreshed, refreshed_signature,
                scope=scope, secret=secret, resource_ref=resource_ref,
                verification=verified,
                guard=lambda: _operation_ledger_guard(
                    scope, payload["work_item_ref"], payload["expected_revision"],
                    payload["expected_edit_token"], signature),
                expected_history=prior_evidence["events"])
        except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
            raise _safe_error(exc) from None
        await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {
        "format": "openworkgraph.local-bound-run-source-verification.v1",
        "provider_check_persisted": persisted_history is not None,
        "case_provider_evidence_history": persisted_history,
        "provider_evidence_run_identity_independently_verified": False,
        "work_item_ref": payload["work_item_ref"],
        "run_ref": run["run_ref"],
        "association": "operator_confirmed",
        "imported_run": run,
        "fresh_source_triage": triage,
        "provider_operation_verified": bool(verified.get("provider_operation_verified", False)),
        "run_case_identity_independently_verified": False,
        "agent_caused_operation_verified": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_write_performed": False,
    }


@router.post("/v1/open-work/declarations/human-outcomes/bound-run-evidence-history")
async def review_local_bound_run_evidence_history(request: Request, response: Response):
    """Join a current operator-confirmed imported run to retained case evidence.

    The evidence ledger is case-wide. Its entries must NOT be interpreted as
    independently linked to this run, agent causality, or customer success.
    No provider read, new grant, or persistence occurs.
    """
    _dashboard_only(request)
    scope = await _require_scope()
    payload = await _body(request, {
        "work_item_ref", "expected_revision", "expected_edit_token",
        "confirm_historical_review", "confirm_run_comparison",
        "max_cases", "deployment_id", "run_id", "record_link",
    })
    if (payload["confirm_historical_review"] is not True
            or payload["confirm_run_comparison"] is not True):
        raise HTTPException(403, "explicit run and historical review required")
    if type(payload["max_cases"]) is not int or not 1 <= payload["max_cases"] <= 4:
        raise HTTPException(422, "max_cases must be between 1 and 4")
    from .open_work_run_case_binding import get_binding

    historical, admitted, signature, secret = await _load_review_triage(scope, payload)
    try:
        resource_ref = validate_case_record_link(
            payload["record_link"], admitted, signature, scope=scope, secret=secret)
        run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret)
        actor = hashlib.sha256(request.headers["authorization"].encode("utf-8")).hexdigest()
        binding = await asyncio.to_thread(
            get_binding, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"], run_ref=run["run_ref"],
            fingerprint=run["run_snapshot_fingerprint"],
            revision=payload["expected_revision"])
        if binding is None:
            raise CaseDeclarationError("explicit current run/case binding review required")
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    try:
        history = await asyncio.to_thread(
            read_operation_evidence, admitted, signature, scope=scope,
            secret=secret, guard=lambda: _operation_ledger_guard(
                scope, payload["work_item_ref"], payload["expected_revision"],
                payload["expected_edit_token"], signature))
        repeated, refreshed, refreshed_signature, _ = await _load_review_triage(scope, payload)
        second_run = await asyncio.to_thread(
            read_imported_run, deployment_id=payload["deployment_id"],
            run_id=payload["run_id"], scope=scope, secret=secret)
        second_binding = await asyncio.to_thread(
            get_binding, scope=scope, actor=actor, secret=secret,
            case_ref=payload["work_item_ref"], run_ref=second_run["run_ref"],
            fingerprint=second_run["run_snapshot_fingerprint"],
            revision=payload["expected_revision"])
        if (repeated != historical or refreshed_signature != signature
                or refreshed["work_item_ref"] != admitted["work_item_ref"]
                or second_run != run or second_binding != binding):
            raise CaseDeclarationError("stale run or case review changed during history read")
        second_resource = validate_case_record_link(
            payload["record_link"], refreshed, refreshed_signature,
            scope=scope, secret=secret)
        if second_resource != resource_ref:
            raise CaseDeclarationError("stale record link changed during history read")
        await asyncio.to_thread(
            _operation_ledger_guard, scope, payload["work_item_ref"],
            payload["expected_revision"], payload["expected_edit_token"], signature)
    except (CaseDeclarationError, OSError, sqlite3.DatabaseError, TypeError, KeyError) as exc:
        raise _safe_error(exc) from None
    await _recheck_source(scope)
    response.headers["Cache-Control"] = "no-store"
    return {
        "format": "openworkgraph.local-bound-run-evidence-history.v1",
        "work_item_ref": payload["work_item_ref"],
        "run_ref": run["run_ref"],
        "association": "operator_confirmed",
        "case_provider_evidence_history": history,
        "evidence_scope": "case_wide_not_run_attributed",
        "source_read_performed": False,
        "source_write_performed": False,
        "run_case_identity_independently_verified": False,
        "agent_caused_operation_verified": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
    }
