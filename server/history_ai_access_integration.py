from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Request

from shared.history_policy import set_ai_history_access
from .secure_app import app


def _take_endpoint(target: FastAPI, path: str, method: str) -> Callable[..., Any] | None:
    wanted = method.upper()
    for route in list(target.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and wanted in methods:
            target.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def install_history_ai_access_integration(target: FastAPI = app) -> None:
    if getattr(target.state, "owg_history_ai_access_integrated", False):
        return
    original = _take_endpoint(target, "/v1/ai-access", "POST")
    if original is None:
        raise RuntimeError("AI access route must exist before history integration")

    @target.post("/v1/ai-access")
    async def update_ai_access_and_history(request: Request):
        result = await original(request)
        if isinstance(result, dict) and result.get("enabled") is False:
            set_ai_history_access(mode="off", expires_minutes=None)
            result["saved_history_access_revoked"] = True
        return result

    target.state.owg_history_ai_access_integrated = True


install_history_ai_access_integration()

__all__ = ["install_history_ai_access_integration"]
