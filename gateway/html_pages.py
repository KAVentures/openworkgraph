"""Static account shells with a shared nonce and no stored credentials."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from starlette.responses import Response


def render_page(name: str, *, config: dict[str, Any] | None = None,
                connect_origin: str = "") -> Response:
    nonce = secrets.token_urlsafe(24)
    html = Path(__file__).with_name(name).read_text(encoding="utf-8").replace("__NONCE__", nonce)
    if config is not None:
        html = html.replace("__CONFIG__", json.dumps(config).replace("<", "\\u003c"))
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        + (f"style-src 'nonce-{nonce}'; " if config is not None else "style-src 'unsafe-inline'; ")
        + f"connect-src 'self' {connect_origin}; img-src data:; "
        + "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return Response(content=html, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": csp, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    })
