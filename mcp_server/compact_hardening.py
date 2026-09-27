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

    @compact_module.mcp.tool()
    def get_context_pulse(
        cursor: str | None = None,
        recent_limit: int = 12,
        finding_limit: int = 6,
        lookback_days: int = 30,
        recent_detail: str = "compact",
    ) -> dict[str, Any]:
        """Return what changed since the last check plus changed factual findings.

        Pass ``next_cursor`` back unchanged on the next call. The recent section is
        canonical-origin evidence that arrived since this caller's bookmark.
        Findings are deterministic evidence-backed aggregates, never advice. A
        repeated-workflow finding may use task inference, and says so explicitly.
        Defaults deliberately fit a small context budget; raise the limits, use
        ``recent_detail='rich'``, or call get_workflow_trace when more is needed.
        """
        name = "get_context_pulse"
        compact_module.core._begin(name)
        params: dict[str, Any] = {
            "recent_limit": min(max(1, int(recent_limit)), 500),
            "finding_limit": min(max(0, int(finding_limit)), 50),
            "lookback_days": min(max(7, int(lookback_days)), 90),
            "recent_detail": "rich" if str(recent_detail).lower() == "rich" else "compact",
        }
        if cursor not in (None, ""):
            params["cursor"] = cursor
        return compact_module.core._finish(
            name,
            compact_module.secure_runtime.secure_get("/v1/context-pulse", params),
        )

    compact_module.get_context_pulse = get_context_pulse
    compact_module._V0873_HARDENING_APPLIED = True


__all__ = ["apply_compact_hardening"]
