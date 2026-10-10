from __future__ import annotations

"""Only a paired browser popup can offer a selection to local OWG AI clients."""

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .ephemeral_page_context import consume_for_ai, store_selected_text
from .secure_app import app


@app.options("/v1/browser-selection/submit")
def browser_selection_preflight(request: Request) -> Response:
    from .secure_app import _extension_cors
    return _extension_cors(Response(status_code=204), str(request.headers.get("origin") or ""))


@app.post("/v1/browser-selection/submit")
async def submit_browser_selection(request: Request) -> Response:
    from .secure_app import _extension_cors
    origin = str(request.headers.get("origin") or "")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid selection")
        result = store_selected_text(
            hostname=payload.get("hostname", ""),
            page_url=payload.get("page_url", ""),
            title=payload.get("title", ""),
            text=payload.get("text", ""),
        )
        response = JSONResponse(result)
    except (ValueError, PermissionError) as exc:
        response = JSONResponse({"detail": str(exc)}, status_code=403)
    except Exception:
        # Redaction failure must not result in a partially stored snapshot.
        response = JSONResponse({"detail": "Could not safely process selection."}, status_code=422)
    return _extension_cors(response, origin)


@app.get("/v1/browser-selection/consume")
def consume_browser_selection(request: Request) -> Response:
    # Dashboard access is *not* permission to retrieve user-approved AI content.
    # The secure local MCP runtime sends this header after bearer authentication.
    if request.headers.get("X-OpenWorkGraph-Context") != "ai":
        return JSONResponse({"detail": "AI client context required"}, status_code=403)
    try:
        return JSONResponse(consume_for_ai())
    except PermissionError:
        return JSONResponse({"detail": "AI access is off"}, status_code=403)


__all__ = ["submit_browser_selection", "consume_browser_selection"]
