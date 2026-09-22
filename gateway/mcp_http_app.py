from __future__ import annotations

import hmac
import os

from starlette.responses import JSONResponse

from .mcp import mcp

_inner = mcp.streamable_http_app()


class GatewayMCPBearerGuard:
    """Simple self-hosted MCP edge.

    The Gateway service token used to query evidence stays server-side. MCP clients
    authenticate with a separate bearer. Production deployments can place this ASGI
    app behind the organization's OAuth/OIDC-aware reverse proxy.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        raw = str(headers.get("authorization") or "")
        supplied = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        expected = os.getenv("OWG_GATEWAY_MCP_CLIENT_TOKEN", "").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            response = JSONResponse({"detail": "OpenWorkGraph Gateway MCP authentication required"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = GatewayMCPBearerGuard(_inner)
