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

# Compact Context keeps complete history on the canonical, paginated
# get_workflow_trace surface. Derived convenience/index tools use bounded windows
# so their recomputation cannot block the MCP transport on slower hosts.
_TASK_INDEX_CAP = 5_000
_PROCEDURAL_DISCOVERY_CAP = 1_000
_TASK_CONTEXT_CAP = 2_000
_PROCEDURAL_FEEDBACK_CAP = 2_000
_AGENT_INDEX_CAP = 5_000

_DERIVED_CAPS: dict[str, dict[str, tuple[str, int]]] = {
    "find_repeated_workflows": {
        "/v1/tasks": ("limit", _TASK_INDEX_CAP),
        "/v1/summary": ("limit", _TASK_INDEX_CAP),
        "/v1/procedural-memory": ("limit", _PROCEDURAL_DISCOVERY_CAP),
    },
    "get_task_context": {
        "/v1/task-context": ("limit", _TASK_CONTEXT_CAP),
    },
    "how_did_similar_runs_go": {
        "/v1/procedural-memory": ("limit", _PROCEDURAL_DISCOVERY_CAP),
        "/v1/tasks": ("limit", _TASK_CONTEXT_CAP),
        "/v1/procedural-memory/readable-feedback": ("limit", _PROCEDURAL_FEEDBACK_CAP),
        "/v1/procedural-memory/similar-runs": ("limit", _PROCEDURAL_FEEDBACK_CAP),
        "/v1/procedural-memory/failure-patterns": ("limit", _PROCEDURAL_FEEDBACK_CAP),
        "/v1/procedural-memory/approval-patterns": ("limit", _PROCEDURAL_FEEDBACK_CAP),
        "/v1/procedural-memory/next-steps": ("limit", _PROCEDURAL_FEEDBACK_CAP),
    },
    "get_agent_runs": {
        "/v1/agent-execution-traces": ("evidence_limit", _AGENT_INDEX_CAP),
    },
}


def _readable_step_input(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    # Preserve the pre-v0.87 structural token contract. Anything else is treated
    # as human-readable progress so the readable endpoint can return helpful
    # validation instead of leaking an opaque structural parser error.
    return _LEGACY_STEP_RE.fullmatch(text) is None


def _bounded_param(params: dict[str, Any], key: str, cap: int) -> int:
    try:
        requested = int(params.get(key) or cap)
    except Exception:
        requested = cap
    applied = max(1, min(requested, cap))
    params[key] = applied
    return applied


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

        p = dict(params or {})
        tool_name = _CURRENT_TOOL.get()
        spec = _DERIVED_CAPS.get(tool_name, {}).get(path)
        if spec:
            key, cap = spec
            applied = _bounded_param(p, key, cap)
            limits = dict(_APPLIED_DERIVED_LIMITS.get())
            limits[f"{path}:{key}"] = applied
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
        limits = dict(_APPLIED_DERIVED_LIMITS.get())
        if limits and isinstance(data, dict):
            data = dict(data)
            data["derived_scan_limits"] = limits
            data["derived_scan_bounded"] = True
            data["derived_scan_may_be_incomplete"] = True
            data["complete_history_tool"] = "get_workflow_trace"
        return original_finish(tool_name, data)

    compact_module.core._begin = begin
    compact_module.core._finish = finish
    compact_module._V0873_HARDENING_APPLIED = True


__all__ = ["apply_compact_hardening"]
