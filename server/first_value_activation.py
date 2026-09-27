from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse

from .main import app


DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
SCRIPT_PATH = DASHBOARD_DIR / "first_value_activation.js"
SCRIPT_TAG = '<script src="/first-value-activation.js"></script>'


@app.get("/first-value-activation.js", include_in_schema=False)
def first_value_activation_javascript() -> FileResponse:
    """Serve the additive first-value dashboard layer.

    The script reads only existing authenticated local endpoints. It adds no
    capture sensor, data store, AI permission, retention permission, or MCP
    capability.
    """
    return FileResponse(SCRIPT_PATH, media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def inject_first_value_activation(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET" or request.url.path != "/":
        return response
    content_type = str(response.headers.get("content-type") or "")
    if "text/html" not in content_type.lower():
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    text = body.decode("utf-8", errors="replace")
    if SCRIPT_TAG not in text:
        text = text.replace("</body>", f"{SCRIPT_TAG}</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return type(response)(
        content=text.encode("utf-8"),
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
        background=response.background,
    )
