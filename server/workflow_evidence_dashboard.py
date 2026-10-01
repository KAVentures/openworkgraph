from __future__ import annotations

"""Serve/inject the dashboard's evidence-to-external-AI workflow helper."""

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from .main import ROOT
from .secure_app import app


def workflow_evidence_script() -> Response:
    path = ROOT / "dashboard" / "workflow_evidence.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


async def _inject_workflow_evidence_script(request: Request, call_next):
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
    marker = '<script src="/workflow-evidence.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


if not getattr(app.state, "owg_workflow_evidence_dashboard_installed", False):
    app.add_api_route("/workflow-evidence.js", workflow_evidence_script, methods=["GET"])
    app.middleware("http")(_inject_workflow_evidence_script)
    app.state.owg_workflow_evidence_dashboard_installed = True


__all__ = ["workflow_evidence_script"]
