from __future__ import annotations

"""Compatibility-safe refinements for the compact MCP surface.

The legacy MCP server and procedural-memory REST endpoints stay unchanged. This
module only adjusts the compact entrypoints used by new connections.
"""

import contextvars
import re
from types import ModuleType
from typing import Any


_LEGACY_STEP_RE = re.compile(
    r"^\s*[a-z0-9._-]+:[a-z0-9._-]+(?:\s*,\s*[a-z0-9._-]+:[a-z0-9._-]+)*\s*$",
    re.I,
)
_CURRENT_TOOL: contextvars.ContextVar[str] = contextvars.ContextVar("owg_compact_tool", default="")
_APPLIED_DERIVED_LIMITS: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar(
    "owg_compact_derived_limits", default={}
)
_REPEATED_WORKFLOW_SCAN_CAP = 5_000
_PROCEDURAL_DISCOVERY_SCAN_CAP = 1_000


def _readable_step_input(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    # Preserve the pre-v0.87 structural token contract. Anything else is treated
    # as human-readable progress so the readable endpoint can return helpful
    # validation instead of leaking an opaque structural parser error.
    return _LEGACY_STEP_RE.fullmatch(text) is None


class _CompactRuntimeProxy:
    def __init__(self, runtime: ModuleType) -> None:
        self._runtime = runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)

    def secure_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path == "/v1/procedural-memory/context-pack":
            p = dict(params or {})
            return {
                "family_key": p.get("family_key"),
                "step_vocabulary": "privacy_safe_semantic_in_compact_mcp",
                "structural_context_omitted": True,
                "detail": (
                    "Compact MCP omits the legacy structural context pack. Use "
                    "similar_prior_runs, readable_human_feedback and trace_lookup "
                    "for agent-facing evidence; the legacy REST view remains available."
                ),
                "derived": True,
                "authoritative": False,
                "prescriptive": False,
            }

        # Repeated-workflow discovery is a derived convenience surface, not the
        # canonical evidence path. `/v1/procedural-memory` is materially more
        # expensive than the task/summary indexes because it reconstructs execution
        # families, so it gets its own smaller discovery window. This keeps compact
        # MCP responsive on slower Windows hosts without weakening the complete,
        # paginated canonical evidence surface (`get_workflow_trace`).
        p = dict(params or {})
        if _CURRENT_TOOL.get() == "find_repeated_workflows" and path in {
            "/v1/tasks", "/v1/summary", "/v1/procedural-memory",
        }:
            cap = (
                _PROCEDURAL_DISCOVERY_SCAN_CAP
                if path == "/v1/procedural-memory"
                else _REPEATED_WORKFLOW_SCAN_CAP
            )
            try:
                requested = int(p.get("limit") or cap)
            except Exception:
                requested = cap
            applied = max(1, min(requested, cap))
            p["limit"] = applied
            limits = dict(_APPLIED_DERIVED_LIMITS.get())
            limits[path] = applied
            _APPLIED_DERIVED_LIMITS.set(limits)
        return self._runtime.secure_get(path, p)


def apply_compact_hardening(compact_module: ModuleType) -> None:
    if getattr(compact_module, "_V0873_HARDENING_APPLIED", False):
        return
    compact_module._is_readable_step_input = _readable_step_input
    compact_module.secure_runtime = _CompactRuntimeProxy(compact_module.secure_runtime)

    original_begin = compact_module.core._begin
    original_finish = compact_module.core._finish

    def begin(tool_name: str) -> None:
        _CURRENT_TOOL.set(str(tool_name or ""))
        _APPLIED_DERIVED_LIMITS.set({})
        original_begin(tool_name)

    def finish(tool_name: str, data: Any) -> dict[str, Any]:
        if tool_name == "find_repeated_workflows" and isinstance(data, dict):
            limits = dict(_APPLIED_DERIVED_LIMITS.get())
            if limits:
                data = dict(data)
                data["derived_scan_limits"] = {
                    "tasks": limits.get("/v1/tasks"),
                    "summary": limits.get("/v1/summary"),
                    "procedural_memory": limits.get("/v1/procedural-memory"),
                }
                data["derived_scan_bounded"] = True
                data["complete_history_tool"] = "get_workflow_trace"
                data["procedural_family_discovery_may_be_incomplete"] = True
        return original_finish(tool_name, data)

    compact_module.core._begin = begin
    compact_module.core._finish = finish
    compact_module._V0873_HARDENING_APPLIED = True


__all__ = ["apply_compact_hardening"]
