from __future__ import annotations

"""Bind human-session retention to explicit recording Stop/Start boundaries."""

from typing import Any, Callable

from fastapi import FastAPI

from shared.history_policy import retention_for_kind
from .history_retention import cleanup_ephemeral_session
from .main import COLLECTOR_STATUS
from .secure_app import app


def _take_endpoint(target: FastAPI, path: str, method: str) -> Callable[..., Any] | None:
    wanted = method.upper()
    for route in list(target.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and wanted in methods:
            target.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def _current_human_session() -> str:
    return str(COLLECTOR_STATUS.get("session_id") or "").strip()


def install_history_capture_integration(target: FastAPI = app) -> None:
    if getattr(target.state, "owg_history_capture_integrated", False):
        return
    original_stop = _take_endpoint(target, "/v1/capture/stop", "POST")
    original_start = _take_endpoint(target, "/v1/capture/start", "POST")
    if original_stop is None or original_start is None:
        raise RuntimeError("capture routes must be registered before history integration")

    @target.post("/v1/capture/stop")
    def stop_and_apply_history() -> dict[str, Any]:
        session_id = _current_human_session()
        result = original_stop()
        if session_id and retention_for_kind("human").get("mode") == "ephemeral":
            result["history_cleanup"] = cleanup_ephemeral_session("human", session_id)
        return result

    @target.post("/v1/capture/start")
    def start_and_apply_history() -> dict[str, Any]:
        previous = _current_human_session()
        if previous and retention_for_kind("human").get("mode") == "ephemeral":
            cleanup_ephemeral_session("human", previous)
        return original_start()

    target.state.owg_history_capture_integrated = True


install_history_capture_integration()

__all__ = ["install_history_capture_integration"]
