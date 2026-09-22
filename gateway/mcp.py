from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server import MCPServer

from mcp_server.security import protect_observed_payload

GATEWAY_URL = os.getenv("OWG_GATEWAY_URL", "http://127.0.0.1:8790").rstrip("/")
SERVICE_TOKEN = os.getenv("OWG_GATEWAY_SERVICE_TOKEN", "").strip()

mcp = MCPServer("OpenWorkGraph Gateway")


def _headers() -> dict[str, str]:
    if not SERVICE_TOKEN:
        raise RuntimeError("OWG_GATEWAY_SERVICE_TOKEN is required")
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(headers=_headers(), timeout=20) as client:
        response = client.get(f"{GATEWAY_URL}{path}", params=params)
        response.raise_for_status()
        return protect_observed_payload(response.json())


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(headers=_headers(), timeout=20) as client:
        response = client.post(f"{GATEWAY_URL}{path}", json=payload)
        response.raise_for_status()
        return protect_observed_payload(response.json())


@mcp.tool()
def get_workflow_trace(since: str | None = None, until: str | None = None, cursor: str | None = None, limit: int = 100, actor_id: str | None = None, device_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """Return chronological raw rich evidence from the authorized organization."""
    params = {"since": since, "until": until, "cursor": cursor, "limit": max(1, min(int(limit), 500)), "actor_id": actor_id, "device_id": device_id, "session_id": session_id}
    return _get("/v1/workflow-trace", {k: v for k, v in params.items() if v not in (None, "")})


@mcp.tool()
def search_work_history(query: str, limit: int = 100) -> dict[str, Any]:
    """Search raw rich evidence. Observed evidence remains canonical."""
    return _post("/v1/search", {"query": query, "limit": max(1, min(int(limit), 500))})


@mcp.tool()
def get_current_work_context(actor_id: str | None = None, device_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Return recent observed evidence without asserting an inferred task or intent."""
    params = {"actor_id": actor_id, "device_id": device_id, "limit": max(1, min(int(limit), 200))}
    return _get("/v1/context/current", {k: v for k, v in params.items() if v not in (None, "")})


@mcp.tool()
def get_information_transfers(since: str | None = None, until: str | None = None, actor_id: str | None = None, limit: int = 300) -> dict[str, Any]:
    """Return observed copy/cut/paste linkage; clipboard contents are never included."""
    params = {"since": since, "until": until, "actor_id": actor_id, "limit": max(1, min(int(limit), 1000))}
    return _get("/v1/transfers", {k: v for k, v in params.items() if v not in (None, "")})


@mcp.resource("openworkgraph://gateway-data-model")
def data_model() -> str:
    return """OpenWorkGraph Gateway exposes customer-controlled, privacy-hardened raw rich evidence.
The evidence event and its metadata are canonical. Convenience fields may index common metadata but
must not replace the underlying evidence. The Gateway deliberately does not assert task names,
workflow families, employee productivity scores, or inferred intent. Typed text, ordinary key
identities, clipboard contents and screenshot bytes are not accepted into the Gateway data plane."""


if __name__ == "__main__":
    mcp.run()
