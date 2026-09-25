from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from server.local_auth import ensure_api_token
from . import main as core

API_URL = os.getenv("WORKFLOW_OBSERVER_API", "http://127.0.0.1:8787").rstrip("/")
_TASK_CONTEXT_PROVENANCE_KEY = "_openworkgraph_task_context_provenance"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_api_token()}"}


def secure_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=15, headers=_headers()) as client:
        response = client.get(f"{API_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def secure_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=15, headers=_headers()) as client:
        response = client.post(f"{API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def _activity_summary(result: dict[str, Any]) -> dict[str, Any]:
    timestamps: list[str] = []
    row_count = 0

    def walk(value: Any) -> None:
        nonlocal row_count
        if isinstance(value, dict):
            observed = value.get("observed_at")
            if isinstance(observed, str) and observed:
                timestamps.append(observed)
            for key, child in value.items():
                if key in {"rows", "events", "tasks", "examples", "candidates"} and isinstance(child, list):
                    row_count += len(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        size = 0
    timestamps.sort()
    return {
        "rows": row_count,
        "bytes": size,
        "range_start": timestamps[0] if timestamps else "",
        "range_end": timestamps[-1] if timestamps else "",
    }


def _snapshot_sha256(value: dict[str, Any]) -> str:
    """Hash one JSON object using the same canonicalization as task preflight."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finish_task_context(tool_name: str, source_result: dict[str, Any]) -> dict[str, Any]:
    """Protect task context, fingerprint both representations, then audit exactly what is returned.

    The source fingerprint identifies the exact local `/v1/task-context` response
    before the MCP trust-boundary transform. The MCP fingerprint identifies all
    protected result fields *before* this trusted provenance object is appended.
    Neither fingerprint attests that a model consumed or used the context.
    """
    source_sha256 = _snapshot_sha256(source_result)
    protected = core.protect_observed_payload(source_result)
    protected_sha256 = _snapshot_sha256(protected)
    protected[_TASK_CONTEXT_PROVENANCE_KEY] = {
        "schema_version": "1.0",
        "source_api_snapshot_sha256": source_sha256,
        "mcp_protected_snapshot_sha256": protected_sha256,
        "mcp_protected_snapshot_fingerprint_scope": (
            "all MCP-visible fields before _openworkgraph_task_context_provenance is appended"
        ),
        "prompt_injection_protection_applied": True,
        "mcp_tool_result_emitted": True,
        "mcp_client_receipt_attested": False,
        "model_context_consumption_attested": False,
        "model_context_use_attested": False,
        "automatic_context_injection": False,
        "source_and_mcp_representation_are_distinct": True,
    }
    core._audit_tool(tool_name, protected)
    return protected


def authorize_tool(tool_name: str) -> None:
    try:
        state = secure_get("/v1/ai-access")
    except Exception as exc:
        raise ToolError("OpenWorkGraph could not verify AI access. Keep OpenWorkGraph running and reopen its local dashboard.") from exc
    if state.get("enabled"):
        return
    try:
        secure_post("/v1/mcp-activity", {"tool": tool_name, "status": "denied", "rows": 0, "bytes": 0})
    except Exception:
        pass
    raise ToolError("OpenWorkGraph AI access is OFF. Enable AI access in the local dashboard for this run.")


def audit_tool(tool_name: str, result: dict[str, Any]) -> None:
    try:
        summary = _activity_summary(result)
        secure_post("/v1/mcp-activity", {"tool": tool_name, "status": "ok", **summary})
    except Exception:
        # Observability must never turn a successful evidence read into a failure.
        pass


# Existing MCP tools resolve these globals at call time. Swap only the local API
# transport/access hooks; tool definitions and prompt-injection filtering stay in
# mcp_server.main.
core._get = secure_get
core._authorize_tool = authorize_tool
core._audit_tool = audit_tool


@core.mcp.tool()
def get_work_profile(scope: str = "current") -> dict[str, Any]:
    """Return locally derived workflow signals for AI analysis.

    The profile contains fragmentation, linked manual transfers, timing/rhythm,
    AI-surface usage, communication-action counts, navigation/hunting candidates,
    and voluntary self-tags. These are regeneratable workflow signals, not
    productivity scores. Use get_workflow_trace when supporting evidence is needed.
    """
    if scope not in {"current", "week", "all"}:
        raise ToolError("scope must be current, week, or all")
    name = "get_work_profile"
    core._begin(name)
    result = secure_get("/v1/work-profile", {"scope": scope})
    result["evidence_tool"] = "get_workflow_trace"
    result["needs_human_interpretation"] = True
    return core._finish(name, result)


@core.mcp.tool()
def get_task_context(
    family_key: str = "",
    task_family: str = "",
    current_steps: str = "",
    after_step: str = "",
    min_support: int = 2,
    max_events: int = 10_000,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
) -> dict[str, Any]:
    """Return one read-only organizational context bundle for a task.

    Prefer an exact procedural ``family_key`` when known. ``task_family`` accepts
    only canonical human families such as ``email.reply`` or ``github.review``.
    ``current_steps`` may resolve a family only from OpenWorkGraph-generated
    structural tokens; arbitrary natural-language task descriptions are not used
    for fuzzy matching. Declared policy remains normative input while repeated
    behavior remains non-authoritative observed evidence.

    The returned MCP copy preserves the normal prompt-injection protection and
    adds source/protected snapshot fingerprints. These fingerprints establish
    representation provenance only; they do not attest model consumption or use.
    """
    name = "get_task_context"
    core._begin(name)
    result = secure_get("/v1/task-context", {
        "family_key": family_key,
        "task_family": task_family,
        "current_steps": current_steps,
        "after_step": after_step,
        "min_support": min(max(2, int(min_support)), 100),
        "limit": min(max(1, int(max_events)), 25_000),
        "run_limit": min(max(1, int(run_limit)), 5),
        "section_limit": min(max(1, int(section_limit)), 5),
        "max_steps_per_run": min(max(1, int(max_steps_per_run)), 24),
        "max_evidence_refs_per_item": min(max(0, int(max_evidence_refs_per_item)), 4),
    })
    return _finish_task_context(name, result)


@core.mcp.tool()
def get_action_policy_advisory(
    family_key: str,
    proposed_step: str,
    completed_steps: str = "",
) -> dict[str, Any]:
    """Check explicit declared policy relevant to one proposed structural action.

    This tool is advisory only. It does not authorize, execute, block, or approve
    the action, and observed workflow behavior is never used as permission. Pass
    completed OpenWorkGraph structural steps as a comma-separated list.
    """
    name = "get_action_policy_advisory"
    core._begin(name)
    completed = [item.strip() for item in str(completed_steps or "").split(",") if item.strip()]
    result = secure_post("/v1/declared-policies/action-advisory", {
        "family_key": family_key,
        "proposed_step": proposed_step,
        "completed_steps": completed,
    })
    return core._finish(name, result)


mcp = core.mcp
