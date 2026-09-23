from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from . import enterprise_app
from .evidence_query import query_evidence
from .main import ROOT
from .secure_app import app


@app.get("/v1/evidence")
def get_paged_evidence(
    scope: str = "current",
    limit: int = 100,
    cursor: str | None = None,
    surface: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    since = enterprise_app._current_run_start() if scope == "current" else None
    try:
        return query_evidence(
            scope=scope,
            since=since,
            limit=limit,
            cursor=cursor,
            surface=surface,
            q=q,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/evidence-paging.js")
def evidence_paging_script() -> Response:
    path = ROOT / "dashboard" / "evidence_paging.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


@app.middleware("http")
async def inject_evidence_paging(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response
    marker = '<script src="/evidence-paging.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["app", "get_paged_evidence"]
