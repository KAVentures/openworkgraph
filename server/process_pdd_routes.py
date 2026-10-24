"""Local route that builds a PDD package with reviewed, redacted screenshots; nothing is stored."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import process_pdd_package as package
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "process_pdd.js"


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON field')
        value[key] = item
    return value


def _finite_json(value):
    raise ValueError('non-finite JSON number')


def _saved_packet(reference):
    # Use the existing owner-scoped catalog, including its historical revisions.
    from .workflow_evidence_routes import _catalog
    try:
        packet = _catalog.export(reference['workflow_id'], reference['revision'])
    except LookupError:
        raise HTTPException(404, 'Saved process revision not found') from None
    if packet['content_sha256'] != reference['content_sha256']:
        raise HTTPException(409, 'Saved process reference does not match this revision')
    return packet


@router.get("/process-pdd.js")
def process_pdd_script() -> FileResponse:
    """Static dashboard client; it only calls the authenticated route below."""
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.post("/v1/process-design/pdd-package")
async def process_pdd_package(request: Request) -> Response:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required to build process documents")
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "request must be JSON")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > package.MAX_REQUEST_BYTES:
            raise HTTPException(413, "request is larger than the supported limit")
    try:
        payload = json.loads(bytes(body), object_pairs_hook=_unique_fields, parse_constant=_finite_json)
    except ValueError:
        raise HTTPException(422, "request is not valid JSON") from None
    try:
        reference = package.process_reference(payload)
        packet = await run_in_threadpool(_saved_packet, reference) if reference is not None else None
        archive = await run_in_threadpool(package.build_pdd_package, payload, packet)
        if reference is not None:
            # Deletion or changed content during image processing invalidates the export.
            await run_in_threadpool(_saved_packet, reference)
    except package.PddPackageError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(archive, media_type="application/zip", headers={
        "Cache-Control": "no-store",
        "Content-Disposition": 'attachment; filename="openworkgraph-pdd.zip"',
    })
