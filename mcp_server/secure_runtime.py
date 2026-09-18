from __future__ import annotations

import os
from typing import Any

import httpx

from server.local_auth import ensure_api_token
from . import main as core

API_URL = os.getenv("WORKFLOW_OBSERVER_API", "http://127.0.0.1:8787").rstrip("/")


def secure_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {ensure_api_token()}"}
    with httpx.Client(timeout=15, headers=headers) as client:
        response = client.get(f"{API_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


# Existing MCP tools resolve _get from their module globals at call time. Swap
# only the transport to the local API; tool definitions/security filtering remain
# exactly the same as mcp_server.main.
core._get = secure_get
mcp = core.mcp
