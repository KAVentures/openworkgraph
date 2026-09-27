from __future__ import annotations

"""Project privacy-safe browser lifecycle signals into the agent evidence model.

The browser extension never supplies prompt/response/alert text here. Provider,
opaque run id and lifecycle state are the complete allowlist.
"""

import re
from typing import Any, Callable

from fastapi import FastAPI

from .agent_ingest import ingest_agent_payloads
from .main import BrowserEvent
from .secure_app import app

_PROVIDER_MAP = {
    "chatgpt": ("ChatGPT", "openai", "chatgpt_web"),
    "claude": ("Claude", "anthropic", "claude_web"),
    "microsoft_copilot": ("Microsoft Copilot", "microsoft", "copilot_web"),
    "lovable": ("Lovable", "lovable", "lovable_web"),
    "gemini": ("Gemini", "google", "gemini_web"),
}
_ACTION_MAP = {
    "agent_run_started": ("run_started", "running"),
    "agent_run_finished": ("run_finished", "success"),
    "agent_run_cancelled": ("run_finished", "cancelled"),
    "agent_error": ("error", "error"),
    "agent_approval_requested": ("human_approval_requested", "running"),
    "agent_approval_received": ("human_approval_received", "running"),
}
_STRUCTURAL = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


def _take_endpoint(target: FastAPI, path: str, method: str) -> Callable[..., Any] | None:
    wanted = method.upper()
    for route in list(target.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and wanted in methods:
            target.router.routes.remove(route)
            return getattr(route, "endpoint", None)
    return None


def _agent_payload(event: BrowserEvent) -> dict[str, Any] | None:
    mapped = _ACTION_MAP.get(str(event.action or ""))
    if mapped is None:
        return None
    meta = dict(event.metadata or {})
    provider_key = str(meta.get("agent_provider") or "").strip().lower()
    provider = _PROVIDER_MAP.get(provider_key)
    run_id = str(meta.get("agent_run_id") or "").strip()
    if provider is None or not _STRUCTURAL.fullmatch(run_id):
        return None
    operation, status = mapped
    agent_name, provider_name, framework = provider
    payload: dict[str, Any] = {
        "observed_at": event.observed_at,
        "agent_name": agent_name,
        "provider": provider_name,
        "framework": framework,
        "operation": operation,
        "status": status,
        "observation_level": "os_observed",
        "run_id": run_id,
        "session_id": run_id,
        "sensor_id": f"browser-agent:{provider_key}",
        "device_id": "browser-agent-local",
        "tool_category": "none",
    }
    return payload


def install_browser_agent_projection(target: FastAPI = app) -> None:
    if getattr(target.state, "owg_browser_agent_projection_installed", False):
        return
    original = _take_endpoint(target, "/v1/browser-events", "POST")
    if original is None:
        raise RuntimeError("browser event route must exist before agent projection is installed")

    @target.post("/v1/browser-events")
    def browser_event_with_agent_projection(event: BrowserEvent) -> dict[str, Any]:
        result = original(event)
        payload = _agent_payload(event)
        projected = 0
        if payload is not None:
            try:
                projected = int(ingest_agent_payloads([payload]).get("inserted") or 0)
            except Exception:
                # Browser observation must never break normal browser evidence.
                # Invalid structural lifecycle data is simply not promoted.
                projected = 0
        return {**dict(result or {}), "agent_projection_inserted": projected}

    target.state.owg_browser_agent_projection_installed = True


install_browser_agent_projection()

__all__ = ["install_browser_agent_projection"]
