from __future__ import annotations

"""Authenticated local dashboard controls for ONE opt-in Stripe source account.

Provider keys are never returned to the UI or available through an AI bearer.
Only a live dashboard-session capability may authorize new source reads.
"""

import asyncio
import platform
from fastapi import APIRouter, HTTPException, Request

from .local_source_credentials import (
    SourceCredentialError, configure_stripe_source,
    disconnect_stripe_source, stripe_source_status,
)
from .local_auth import dashboard_session_valid
from .open_work_preview import stripe_open_work_preview

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
        return _status()
    except (ValueError, SourceCredentialError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/v1/local-source/stripe")
async def local_stripe_disconnect(request: Request):
    _require_dashboard(request)
    try:
        await asyncio.to_thread(disconnect_stripe_source)
    except SourceCredentialError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _status()


@router.get("/v1/open-work/stripe-preview")
async def stripe_open_work_candidates(request: Request, confirm_scan: bool = False,
                                      max_pages: int = 1):
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
        max_pages=max_pages,
    )
