from __future__ import annotations

"""HTTP wiring for AI context detail (see server.ai_context).

* Middleware: requests marked ``X-OpenWorkGraph-Context: ai`` (the MCP server)
  get their whole JSON response transformed once according to the effective
  detail level, and a ``X-OpenWorkGraph-Detail-Level`` response header. It
  fails closed: a response that cannot be parsed is not passed through raw.
* ``GET/POST /v1/ai-context``: the dashboard setting. AI-context requests may
  read it but never change it, so a connected app cannot widen its own access.
"""

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import ai_context
from .contextual_redaction import lexicon_stats
from .contextual_redaction_extensions import install as install_redaction_extensions
from .secure_app import app


# Add conservative edge-case coverage without changing the canonical evidence
# store or the core detector's existing contract.
install_redaction_extensions(ai_context)


def _is_ai_request(request: Request) -> bool:
    return str(request.headers.get(ai_context.AI_CONTEXT_HEADER) or "").strip().lower() == "ai"


async def _ai_context_middleware(request: Request, call_next):
    if not _is_ai_request(request) or not request.url.path.startswith("/v1/"):
        return await call_next(request)

    token = ai_context.enter_ai_request()
    try:
        response = await call_next(request)
    finally:
        ai_context.exit_ai_request(token)

    content_type = str(response.headers.get("content-type") or "")
    level = ai_context.effective_detail()["detail_level"]
    if "application/json" not in content_type:
        # Full is an explicit local user choice, so existing binary/text routes
        # remain usable there. Redacted must never pass an uninspected body
        # through -- including 4xx/5xx error pages that may echo sensitive text.
        if level == ai_context.DETAIL_FULL:
            response.headers[ai_context.DETAIL_HEADER] = level
            return response
        status_code = response.status_code if response.status_code >= 400 else 406
        return JSONResponse(
            {"detail": "OpenWorkGraph withheld a non-JSON AI context response because it could not be redacted safely."},
            status_code=status_code,
            headers={ai_context.DETAIL_HEADER: ai_context.DETAIL_REDACTED},
        )

    try:
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(c if isinstance(c, bytes) else str(c).encode("utf-8") for c in chunks)
        payload: Any = json.loads(body.decode("utf-8")) if body else None
        safe, level = ai_context.redact_for_ai(payload)
    except Exception:
        return JSONResponse(
            {"detail": "OpenWorkGraph could not prepare this AI context safely."},
            status_code=500,
            headers={ai_context.DETAIL_HEADER: ai_context.DETAIL_REDACTED},
        )
    headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in {"content-length", "content-type"}
    }
    headers[ai_context.DETAIL_HEADER] = level
    return JSONResponse(safe, status_code=response.status_code, headers=headers)


def get_ai_context() -> JSONResponse:
    return JSONResponse(
        {**ai_context.effective_detail(), "lexicon": lexicon_stats()},
        headers={"Cache-Control": "no-store"},
    )


async def update_ai_context(request: Request) -> Response:
    if _is_ai_request(request):
        return JSONResponse(
            {"detail": "AI context settings can only be changed in the OpenWorkGraph dashboard."},
            status_code=403,
        )
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid request"}, status_code=400)
    payload = payload if isinstance(payload, dict) else {}
    detail = payload.get("detail")
    if detail is not None:
        detail = str(detail).strip().lower()
        if detail not in ai_context.DETAILS:
            return JSONResponse({"detail": "detail must be 'redacted' or 'full'"}, status_code=400)
        if detail == ai_context.DETAIL_FULL and ai_context.organization_lock():
            return JSONResponse(
                {"detail": "Your organization requires Redacted AI context.", "locked_by_organization": True},
                status_code=409,
            )
    lists = {}
    for key in ("never_redact", "always_redact"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                value = [line for line in value.splitlines()]
            if not isinstance(value, list):
                return JSONResponse({"detail": f"{key} must be a list"}, status_code=400)
            lists[key] = [str(item) for item in value]
    result = ai_context.save_user_settings(detail=detail, **lists)
    return JSONResponse({**result, "lexicon": lexicon_stats()}, headers={"Cache-Control": "no-store"})


def _install() -> None:
    app.add_api_route("/v1/ai-context", get_ai_context, methods=["GET"])
    app.add_api_route("/v1/ai-context", update_ai_context, methods=["POST"])
    app.middleware("http")(_ai_context_middleware)


if not getattr(app.state, "owg_ai_context_installed", False):
    _install()
    app.state.owg_ai_context_installed = True
