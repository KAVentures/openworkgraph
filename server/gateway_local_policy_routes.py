from __future__ import annotations

"""Endpoint-local organization-sharing preferences.

These routes can only narrow/broaden the endpoint's own local preference. The
organization policy is still a separate ceiling, so enabling structural agent
sharing here never overrides an organization prohibition.
"""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from connector.control import local_agent_sharing, set_local_agent_sharing
from connector.runtime import restart_sync_worker
from .enterprise_app import CONFIG_PATH, _demo_mode, app


class AgentSharingRequest(BaseModel):
    enabled: bool


@app.get("/v1/gateway-agent-sharing")
def get_gateway_agent_sharing() -> dict[str, Any]:
    return local_agent_sharing(CONFIG_PATH)


@app.post("/v1/gateway-agent-sharing")
def update_gateway_agent_sharing(request: AgentSharingRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Organization sharing settings are disabled in demo mode")
    result = set_local_agent_sharing(CONFIG_PATH, request.enabled)
    # The sync worker loads policy from config when it starts. Restarting is
    # bounded and affects only optional organization synchronization, never local
    # capture or Context MCP.
    try:
        restart_sync_worker(CONFIG_PATH)
    except Exception:
        # The preference remains saved even when there is no enrolled/reachable
        # Gateway; normal worker startup will use it later.
        pass
    return result


__all__ = ["app", "get_gateway_agent_sharing", "update_gateway_agent_sharing"]
