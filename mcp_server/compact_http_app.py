from __future__ import annotations

import os

from starlette.responses import JSONResponse

from server.local_auth import mcp_bearer_matches
from .compact import mcp


_inner = mcp.streamable_http_app()


class MCPBearerGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        if not mcp_bearer_matches(headers.get("authorization")):
            response = JSONResponse({"detail": "OpenWorkGraph MCP authentication required"}, status_code=401)
            await response(scope, receive, send)
            return

        if scope.get("method") == "GET" and scope.get("path") == "/openworkgraph-id":
            response = JSONResponse({
                "server": "OpenWorkGraph",
                "instance_nonce": os.getenv("WORKFLOW_OBSERVER_MCP_INSTANCE_NONCE", ""),
            })
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


app = MCPBearerGuard(_inner)
