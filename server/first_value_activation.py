from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .main import ROOT
from .secure_app import app


SCRIPT_TAG = '<script src="/first-value-activation.js"></script>'


@app.get("/first-value-activation.js", include_in_schema=False)
def first_value_activation_javascript() -> Response:
    """Serve the additive first-value dashboard layer.

    The script reads only existing authenticated local endpoints. It adds no
    capture sensor, data store, AI permission, retention permission, or MCP
    capability.
    """
    path = ROOT / "dashboard" / "first_value_activation.js"
    return Response(
        path.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.middleware("http")
async def inject_first_value_activation(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or "").lower():
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
    if SCRIPT_TAG not in text:
        text = text.replace("</body>", SCRIPT_TAG + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["first_value_activation_javascript"]
