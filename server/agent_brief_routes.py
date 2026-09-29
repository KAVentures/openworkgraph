from __future__ import annotations

"""Session-start briefs: the hook route and the dashboard controls (server/agent_brief.py)."""

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from . import agent_brief
from .agent_auth import agent_brief_bearer_matches
from .agent_config_writer import ConfigConflict
from .secure_app import app

BRIEF_PATH = "/agent-brief/v1/brief"


class BriefRequest(BaseModel):
    framework: str
    session_id: str = ""
    workspace_ref: str = ""


class BriefChoice(BaseModel):
    framework: str
    enabled: bool


@app.post(BRIEF_PATH)
def post_brief(request: Request, body: BriefRequest) -> dict[str, Any]:
    # Brief-only token: it cannot read anything else, and the ingest token cannot read this.
    if not agent_brief_bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="brief token required")
    return agent_brief.deliver(body.framework[:80], session_id=body.session_id[:128], workspace_ref=body.workspace_ref[:40])


@app.get("/v1/agent-brief")
def get_agent_brief_status() -> dict[str, Any]:
    return agent_brief.status()


@app.put("/v1/agent-brief")
def set_agent_brief(body: BriefChoice) -> dict[str, Any]:
    try:
        return {"status": "saved", **agent_brief.set_enabled(body.framework, body.enabled), **agent_brief.status()}
    except (ValueError, ConfigConflict) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/agent-brief/preview")
def preview_agent_brief(framework: str = "claude-code") -> dict[str, Any]:
    """Exactly what an agent would receive now (for a session outside any known project)."""
    if framework not in agent_brief.FRAMEWORKS:
        raise HTTPException(status_code=400, detail="unknown framework")
    return agent_brief.build_brief(framework)


__all__ = ["post_brief"]
