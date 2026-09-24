from __future__ import annotations

# Import the existing secure application unchanged, then add only the vendor-neutral
# agent routes. Keeping the previous implementation in secure_app_base.py makes
# this change easy to audit and protects all existing local-auth/browser/MCP logic.
from . import secure_app_base as _base

for _name, _value in vars(_base).items():
    if _name not in {"__name__", "__loader__", "__package__", "__spec__"}:
        globals()[_name] = _value

from .agent_routes import router as _agent_router

if not any(getattr(route, "path", "") == "/v1/agent-events" for route in app.routes):
    app.include_router(_agent_router)

__all__ = ["app"]
