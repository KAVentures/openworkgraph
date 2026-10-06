"""Thin streamable-HTTP transport for the existing read-only Gateway MCP.

This module deliberately contains no evidence/query logic. It exposes the
existing gateway.mcp server over loopback HTTP so ChatGPT can reach it through
Secure MCP Tunnel during personal-plugin development.

Public plugin deployment is a separate step: use a stable HTTPS endpoint and
OAuth 2.1 rather than exposing this loopback development transport directly.
"""
from __future__ import annotations

import os

import uvicorn

from .mcp import mcp

app = mcp.streamable_http_app()


def main() -> None:
    host = os.getenv("OWG_CHATGPT_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("OWG_CHATGPT_MCP_PORT", "8791"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
