from __future__ import annotations

"""Dashboard routes for connector OAuth (see connector_oauth)."""
import asyncio
import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import connector_oauth as oauth
from .local_auth import dashboard_session_valid

router = APIRouter()


def _require_dashboard(request: Request) -> None:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session ") or not dashboard_session_valid(raw[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="explicit local dashboard session required")


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except oauth.OAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/v1/connector-oauth")
async def oauth_status(request: Request):
    _require_dashboard(request)
    return await _run(oauth.status)


@router.post("/v1/connector-oauth/begin")
async def oauth_begin(request: Request):
    _require_dashboard(request)
    if len(await request.body()) > 4096:
        raise HTTPException(status_code=413, detail="request too large")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="expected an object")
    port = request.url.port or 8787
    return await _run(oauth.begin, data.get("provider"), client_id=data.get("client_id"),
                      host=data.get("host"), scopes=data.get("scopes"), port=port,
                      client_secret=data.get("client_secret") or None)


@router.post("/v1/connector-oauth/disconnect")
async def oauth_disconnect(request: Request):
    _require_dashboard(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="expected an object")
    return await _run(oauth.disconnect, data.get("connection_id"))


def _page(title: str, message: str, status: int) -> HTMLResponse:
    body = (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<body style='font-family:system-ui;margin:3em'><h1>{html.escape(title)}</h1>"
            f"<p>{html.escape(message)}</p></body>")
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/v1/connector-oauth/callback")
async def oauth_callback(request: Request):
    params = request.query_params
    if params.get("error"):
        return _page("Sign-in cancelled", "The provider did not grant access. You can close this tab.", 400)
    try:
        conn = await asyncio.to_thread(oauth.complete, params.get("state", ""), params.get("code", ""))
    except oauth.OAuthError as exc:
        return _page("Sign-in failed", str(exc), 400)
    return _page("Connected", f"{conn['label']} is connected to OpenWorkGraph. No data is read until you start a "
                 "connector. You can close this tab.", 200)
