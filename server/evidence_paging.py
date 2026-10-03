from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from shared.discovery_scope import read_state as read_discovery_state
from . import ai_context, enterprise_app
from .db import connect
from .evidence_file_reader import (
    MAX_FILE_BYTES,
    extract_file_text,
    sha256_file,
    truncate_text,
)
from .evidence_query import query_evidence
from .local_reference_lookup import resolve_file_reference
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
            body = b"".join(
                chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                for chunk in chunks
            )
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


def _discovery_observed(file_ref: str) -> bool:
    state = read_discovery_state()
    if not state.get("enabled") or not state.get("share_approved_at"):
        return False
    start = str(state.get("starts_at") or "")
    end = str(state.get("ends_at") or "")
    if not start or not end:
        return False
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM events
                WHERE observed_at >= ? AND observed_at < ?
                  AND metadata_json LIKE ?
                LIMIT 1
                """,
                (start, end, f"%{file_ref}%"),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _authorized_file_read(request: Request, file_ref: str) -> tuple[bool, str]:
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower() != "ai":
        return False, "AI context request required"
    detail = ai_context.effective_detail()
    if detail.get("detail_level") == ai_context.DETAIL_FULL:
        return True, "full_ai_context"
    if _discovery_observed(file_ref):
        return True, "approved_discovery"
    return False, (
        "File reads require Full AI context or an approved Discovery study that "
        "actually observed this file."
    )


@app.post("/v1/evidence-files/read")
async def read_evidence_file(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON request") from exc
    file_ref = str((body or {}).get("file_ref") or "").strip()
    if not file_ref.startswith("owg:f:"):
        raise HTTPException(
            status_code=422,
            detail="file_ref must be an observed OpenWorkGraph file reference",
        )

    allowed, basis = _authorized_file_read(request, file_ref)
    if not allowed:
        raise HTTPException(status_code=403, detail=basis)

    observed = resolve_file_reference(file_ref)
    if not observed:
        raise HTTPException(
            status_code=404,
            detail="observed file reference is no longer available locally",
        )

    path = Path(str(observed.get("path") or "")).expanduser()
    try:
        stat = path.stat()
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail="the observed file no longer exists",
        ) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="the observed path is no longer a file",
        )
    if stat.st_size > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="file is larger than the on-demand read limit",
        )

    expected = str(observed.get("sha256") or "").lower()
    actual = sha256_file(path)
    if not expected or actual != expected:
        raise HTTPException(
            status_code=409,
            detail=(
                "file changed since it was observed; OpenWorkGraph refused to "
                "read a different version"
            ),
        )

    text, representation = extract_file_text(path)
    if text is None:
        return {
            "file_ref": file_ref,
            "path": str(path),
            "sha256": actual,
            "size": int(stat.st_size),
            "authorization_basis": basis,
            "representation": representation,
            "content": None,
            "contents_stored_by_openworkgraph": False,
            "instruction": (
                "Open the file with an authorized local tool that supports this format."
            ),
        }

    content, truncated = truncate_text(text)
    return {
        "file_ref": file_ref,
        "path": str(path),
        "sha256": actual,
        "size": int(stat.st_size),
        "authorization_basis": basis,
        "representation": representation,
        "content": content,
        "truncated": truncated,
        "contents_stored_by_openworkgraph": False,
    }


__all__ = ["app", "get_paged_evidence", "read_evidence_file"]
