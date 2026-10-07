"""ChatGPT personal-plugin transport for OpenWorkGraph's rich local MCP.

The personal plugin should expose the same compact, authenticated evidence
surface used by local agents. Secure MCP Tunnel can reach this loopback server
without sending the user through the reduced Gateway MCP surface.

The Gateway remains the future public/remote multi-user path; this module adds
no evidence, storage, sync, or inference logic.
"""
from __future__ import annotations

import os

import uvicorn

from mcp_server.compact_http_app import app, mcp


def main() -> None:
    host = os.getenv("OWG_CHATGPT_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("OWG_CHATGPT_MCP_PORT", "8791"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
