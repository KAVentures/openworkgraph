from __future__ import annotations

"""Authenticated local dashboard controls for ONE opt-in Stripe source account.

Provider keys are never returned to the UI or available through an AI bearer.
Only a live dashboard-session capability may authorize new source reads.
"""

import asyncio
import platform
from fastapi import APIRouter, HTTPException, Request, Response

from .local_source_credentials import (
    SourceCredentialError, configure_stripe_source,
    disconnect_stripe_source, stripe_source_status,
)
from .local_auth import dashboard_session_valid
from .jira_source_evidence import jira_issue_receipt
from .jira_process_preview import jira_audit_process_preview
from .jira_batch_process_preview import jira_selected_cases_process
from .process_mining_csv import ProcessMiningCSVError
import re

from .open_work_preview import stripe_open_work_preview
from .open_work_github_preview import github_open_work_preview
from .open_work_case_context import stripe_open_work_case_context
from .open_work_mcp_settings import settings_status, set_experimental_tools_enabled
from .open_work_reviews import (
    list_review_intentions, save_review_intention,
    remove_review_intention, clear_review_intentions,
)

router = APIRouter()


def _require_dashboard(request: Request) -> None:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session ") or not dashboard_session_valid(raw[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="explicit local dashboard session required")


def _status() -> dict:
    return {
        **stripe_source_status(),
        "native_supported": platform.system() in {"Darwin", "Windows"},
        "credentials_exposed": False,
        "local_only": True,
        "source_read_requires_exact_observed_record": True,
    }


@router.get("/v1/local-source/stripe")
def local_stripe_status(request: Request):
    _require_dashboard(request)
    return _status()


@router.post("/v1/local-source/stripe")
async def local_stripe_connect(request: Request):
    _require_dashboard(request)
    if len(await request.body()) > 2048:
        raise HTTPException(status_code=413, detail="source connection request too large")
    try:
        body = await request.json()
        if not isinstance(body, dict) or type(body.get("enabled")) is not bool or body.get("enabled") is not True:
            raise ValueError("separate explicit source-read authorization required")
        account = body.get("account")
        secret = body.get("restricted_key")
        if not isinstance(account, str) or not isinstance(secret, str):
            raise ValueError("a Stripe account and restricted read-only key are required")
        # Provider GET and OS credential dialogs must not stall the local API's
        # event loop. No secret enters access logs, query strings or response.
        await asyncio.to_thread(configure_stripe_source, account, secret)
        # A new/replaced provider connection must not inherit another
        # connection's locally saved review intentions.
        await asyncio.to_thread(clear_review_intentions)
        return _status()
    except (ValueError, SourceCredentialError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/v1/local-source/stripe")
async def local_stripe_disconnect(request: Request):
    _require_dashboard(request)
    try:
        await asyncio.to_thread(disconnect_stripe_source)
        await asyncio.to_thread(clear_review_intentions)
    except SourceCredentialError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _status()


@router.get("/v1/process-mining/jira-case")
async def local_jira_process_preview(
    request: Request, resource_ref: str, response: Response,
    confirm_source_read: bool = False,
):
    """One explicit Jira changelog read for an already observed opaque record.

    Requires both a live local dashboard click and the existing independent
    Jira OAuth source-read opt-in. A bearer/MCP alone cannot trigger this read.
    """
    _require_dashboard(request)
    if confirm_source_read is not True:
        raise HTTPException(status_code=403, detail="explicit source audit read confirmation required")
    if not re.fullmatch(r"owg:r:[0-9a-f]{32}", str(resource_ref or "")):
        raise HTTPException(status_code=422, detail="valid local opaque resource reference required")
    receipt = await asyncio.to_thread(jira_issue_receipt, resource_ref)
    response.headers["Cache-Control"] = "no-store"
    try:
        return jira_audit_process_preview(receipt)
    except (ProcessMiningCSVError, TypeError, ValueError):
        # No source-provided content, target issue ID, or audit text in errors.
        raise HTTPException(status_code=422, detail="bounded Jira audit could not be analyzed") from None


@router.post("/v1/process-mining/jira-selected-cases")
async def local_selected_jira_process_preview(request: Request, response: Response):
    """User-selected, exact observed Jira records only; no tenant scan."""
    _require_dashboard(request)
    content_type = str(request.headers.get("content-type") or "").lower().split(";", 1)[0].strip()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="JSON selected-record request required")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2048:
            raise HTTPException(status_code=413, detail="selected-record request too large")
        body.extend(chunk)
    try:
        try:
            import json
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(status_code=422, detail="invalid selected-record JSON") from None
    finally:
        body.clear()
    if (not isinstance(data, dict)
            or set(data) != {"resource_refs", "confirm_source_reads"}
            or data.get("confirm_source_reads") is not True):
        raise HTTPException(status_code=403, detail="explicit source reads must be confirmed")
    refs = data.get("resource_refs")
    if (not isinstance(refs, list) or not 2 <= len(refs) <= 8
            or any(not isinstance(ref, str) or not re.fullmatch(r"owg:r:[0-9a-f]{32}", ref) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise HTTPException(status_code=422, detail="select 2-8 distinct observed Jira references")
    receipts = []
    for ref in refs:
        # Each existing Jira receipt resolves and checks its own tenant,
        # independent OAuth read permission and exact observed issue identity.
        try:
            receipts.append(await asyncio.to_thread(jira_issue_receipt, ref))
        except (OSError, ValueError, RuntimeError, TypeError):
            receipts.append(None)
    try:
        result = jira_selected_cases_process(receipts)
    except (ProcessMiningCSVError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="source audit window could not be analyzed") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/v1/open-work/stripe-preview")
async def stripe_open_work_candidates(request: Request, confirm_scan: bool = False,
                                      max_pages: int = 1,
                                      include_local_observation: bool = False):
    """Explicit, dashboard-only read-only preview of source-reported open invoices.

    No background scan, no provider write, no hosted MCP exposure and no
    inference that all open invoices require a follow-up. Existing source
    configuration is not consent to scan its entire invoice inventory.
    """
    _require_dashboard(request)
    if confirm_scan is not True:
        raise HTTPException(status_code=403, detail="explicit inventory scan confirmation required")
    if not 1 <= max_pages <= 3:
        raise HTTPException(status_code=422, detail="max_pages must be between 1 and 3")
    return await asyncio.to_thread(
        stripe_open_work_preview, explicit_scan_authorized=True,
        max_pages=max_pages, include_local_observation=include_local_observation,
    )


@router.get("/v1/open-work/stripe-preview/case")
async def stripe_open_work_case(request: Request, work_item_ref: str,
                                confirm_scan: bool = False,
                                max_pages: int = 3):
    """Explicit dashboard-only case context, not permission for an agent to act."""
    _require_dashboard(request)
    if confirm_scan is not True:
        raise HTTPException(status_code=403, detail="explicit inventory scan confirmation required")
    if not 1 <= max_pages <= 3:
        raise HTTPException(status_code=422, detail="max_pages must be between 1 and 3")
    try:
        return await asyncio.to_thread(
            stripe_open_work_case_context, work_item_ref,
            explicit_scan_authorized=True, max_pages=max_pages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/v1/open-work/github-preview")
async def github_open_work_candidates(request: Request, repository: str,
                                       confirm_scan: bool = False,
                                       limit: int = 50):
    """Dashboard-only read of open PR candidates in one explicit repository."""
    _require_dashboard(request)
    if confirm_scan is not True:
        raise HTTPException(status_code=403, detail="explicit inventory scan confirmation required")
    try:
        return await asyncio.to_thread(
            github_open_work_preview, repository,
            explicit_scan_authorized=True, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/v1/local-open-work/mcp-tools")
def local_open_work_mcp_tool_status(request: Request):
    """Read dashboard-only optional MCP catalog setting; no AI permissions."""
    _require_dashboard(request)
    return settings_status()


@router.post("/v1/local-open-work/mcp-tools")
async def update_local_open_work_mcp_tools(request: Request):
    """Only a local dashboard-session owner may opt in, never an AI bearer."""
    _require_dashboard(request)
    if len(await request.body()) > 256:
        raise HTTPException(status_code=413, detail="Open Work settings request too large")
    try:
        data = await request.json()
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            raise ValueError("enabled must be a boolean")
        return await asyncio.to_thread(set_experimental_tools_enabled, data["enabled"])
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail="Open Work tool preference could not be saved") from None


@router.get("/v1/open-work/reviews")
async def local_review_intentions(request: Request):
    """List only human-selected local review intents; does not read Stripe."""
    _require_dashboard(request)
    if not stripe_source_status().get("configured"):
        return {
            **list_review_intentions(),
            "items": [],
            "reason": "provider_connection_not_configured",
        }
    return await asyncio.to_thread(list_review_intentions)


@router.post("/v1/open-work/reviews")
async def create_local_review_intention(request: Request):
    """A deliberate owner click, live source verification, then local write.

    A provider's 'open' flag proves only its current source status, never
    an obligation, customer debt, or permission to contact the customer.
    """
    _require_dashboard(request)
    if len(await request.body()) > 512:
        raise HTTPException(status_code=413, detail="review request too large")
    try:
        payload = await request.json()
        if (not isinstance(payload, dict) or
            payload.get("confirm_scan") is not True or
            payload.get("confirm_save") is not True):
            raise HTTPException(status_code=403, detail="explicit scan and local save confirmation required")
        ref = payload.get("work_item_ref")
        intent = payload.get("review_intent")
        if not isinstance(ref, str) or not isinstance(intent, str):
            raise ValueError("invalid review candidate")
        import re
        if not re.fullmatch(r"owg:w:[0-9a-f]{32}", ref):
            raise ValueError("invalid review candidate reference")
        if intent not in {"assess_follow_up", "verify_payment_status", "check_approved_policy"}:
            raise ValueError("invalid review intention")
        scan = await asyncio.to_thread(
            stripe_open_work_preview, explicit_scan_authorized=True,
            max_pages=3,
        )
        if scan.get("availability") != "source_reported":
            raise HTTPException(status_code=409, detail="current source status unavailable")
        if ref not in {
            item.get("work_item_ref") for item in scan.get("candidates", [])
            if isinstance(item, dict) and item.get("source_status") == "open"
        }:
            raise HTTPException(status_code=409, detail="candidate not verified open within bounded source inventory")
        return await asyncio.to_thread(
            save_review_intention, ref, intent,
            provider_candidate_currently_open=True,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid local review request") from None


@router.delete("/v1/open-work/reviews/{work_item_ref}")
async def delete_local_review_intention(request: Request, work_item_ref: str):
    """Forget an explicit locally stored review intention without source calls."""
    _require_dashboard(request)
    try:
        removed = await asyncio.to_thread(remove_review_intention, work_item_ref)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid review reference") from None
    return {"removed": removed, "source_read_performed": False}
