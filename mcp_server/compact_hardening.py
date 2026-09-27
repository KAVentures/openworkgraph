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
_APPLIED_DERIVED_LIMIT: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "owg_compact_derived_limit", default=None
)
_REPEATED_WORKFLOW_SCAN_CAP = 5_000


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
        # canonical evidence path. Scanning 25k-100k rows three times synchronously
        # caused Windows MCP calls to exceed the transport timeout. Keep this one
        # overview bounded; callers that need complete retained history should page
        # get_workflow_trace instead. The cap is surfaced in the final tool result.
        p = dict(params or {})
        if _CURRENT_TOOL.get() == "find_repeated_workflows" and path in {
            "/v1/tasks", "/v1/summary", "/v1/procedural-memory",
        }:
            try:
                requested = int(p.get("limit") or _REPEATED_WORKFLOW_SCAN_CAP)
            except Exception:
                requested = _REPEATED_WORKFLOW_SCAN_CAP
            applied = max(1, min(requested, _REPEATED_WORKFLOW_SCAN_CAP))
            p["limit"] = applied
            _APPLIED_DERIVED_LIMIT.set(applied)
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
        _APPLIED_DERIVED_LIMIT.set(None)
        original_begin(tool_name)

    def finish(tool_name: str, data: Any) -> dict[str, Any]:
        if tool_name == "find_repeated_workflows" and isinstance(data, dict):
            applied = _APPLIED_DERIVED_LIMIT.get()
            if applied is not None:
                data = dict(data)
                data["evidence_limit_applied"] = applied
                data["derived_scan_bounded"] = True
                data["complete_history_tool"] = "get_workflow_trace"
        return original_finish(tool_name, data)

    compact_module.core._begin = begin
    compact_module.core._finish = finish
    compact_module._V0873_HARDENING_APPLIED = True


__all__ = ["apply_compact_hardening"]
