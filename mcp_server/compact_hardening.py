from __future__ import annotations

"""Compatibility-safe refinements for the compact MCP surface.

The legacy MCP server and procedural-memory REST endpoints stay unchanged. This
module only adjusts the compact entrypoints used by new connections.
"""

import re
from types import ModuleType
from typing import Any


_LEGACY_STEP_RE = re.compile(
    r"^\s*[a-z0-9._-]+:[a-z0-9._-]+(?:\s*,\s*[a-z0-9._-]+:[a-z0-9._-]+)*\s*$",
    re.I,
)


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
        return self._runtime.secure_get(path, params or {})


def apply_compact_hardening(compact_module: ModuleType) -> None:
    if getattr(compact_module, "_V0873_HARDENING_APPLIED", False):
        return
    compact_module._is_readable_step_input = _readable_step_input
    compact_module.secure_runtime = _CompactRuntimeProxy(compact_module.secure_runtime)
    compact_module._V0873_HARDENING_APPLIED = True


__all__ = ["apply_compact_hardening"]
