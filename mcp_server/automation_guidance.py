from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_BRIEF_PATH = ROOT / "AUTOMATION_CAPABILITIES.md"


def _read_capability_brief() -> str:
    try:
        return CAPABILITY_BRIEF_PATH.read_text(encoding="utf-8").strip() + "\n"
    except Exception:
        return (
            "# OpenWorkGraph automation capability brief\n\n"
            "Treat automation feasibility as time-sensitive. Inspect the AI tools actually available now, "
            "keep OpenWorkGraph evidence separate from interpretation, and use bounded draft/read-only "
            "tests when feasibility is uncertain.\n"
        )


AUTOMATION_CAPABILITIES_MD = _read_capability_brief()
_REGISTERED_SERVER_IDS: set[int] = set()


def automation_opportunity_prompt(scope: str = "current") -> str:
    selected = str(scope or "current").strip().lower()
    if selected not in {"current", "week", "all"}:
        selected = "current"
    return f"""Analyze my OpenWorkGraph evidence for automation opportunities. Use scope={selected!r} where the available tools support it.

Reconstruct the work from canonical evidence before deciding what the task means. Treat inferred tasks, repeated families, summaries, and other derived OpenWorkGraph layers as retrieval hints rather than ground truth. Follow evidence references back to get_workflow_trace when a conclusion matters.

Then assess automation against the CURRENT automation frontier, not only your pretrained intuition. Inspect the tools, connectors, MCP servers, browser/computer controls, code execution, file access, scheduling, and approval mechanisms actually available to you. If capability details may have changed and you can verify them, do so. The resource openworkgraph://automation-capabilities is a dated search-space guide, not proof that a capability exists in this client.

Consider outcome-level automation rather than simply copying the human's clicks. Consider API/MCP/connectors, direct file or code operations, browser/computer use, deterministic plus agentic hybrids, and human approval checkpoints.

For each material opportunity, classify it as one of:
- READY TO AUTOMATE: required capabilities are actually available and the workflow/side-effect boundaries are sufficiently understood.
- TEST: plausible with current agents, but reliability, permissions, edge cases, or reconstruction remain uncertain.
- NOT CURRENTLY PRACTICAL: a concrete blocker exists; name it. Do not use this category merely because you are unsure what modern agents can do.

For TEST items, propose a bounded shadow trial on the next natural occurrence of the workflow. Default to draft/read-only/sandboxed behavior and require explicit user authorization before sends, submissions, production writes, purchases, deletes, merges, deployments, or other consequential actions. Do not claim OpenWorkGraph can faithfully replay historical work when the original content was not captured.

Use observed agent runs as evidence about what actually happened, while remembering that missing agent signals mean not observed, not unavailable. Do not infer which connectors are configured inside another AI product unless that information is actually available to you.

Return:
1. a concise reconstruction of the most important repeated/costly workflows with supporting evidence;
2. the strongest automation opportunities, each with classification, required capabilities, evidence, expected human role, and concrete blockers/uncertainties;
3. for TEST items, a safe shadow-trial plan and what should be measured (success, intervention, time, cost, errors, approvals);
4. any missing evidence that would materially change the recommendation.

Keep every automation judgment clearly derived and disposable; never present it as captured OpenWorkGraph fact.
"""


def register_automation_guidance(mcp: Any) -> None:
    """Register static capability guidance and an analysis prompt without adding tools."""
    server_id = id(mcp)
    if server_id in _REGISTERED_SERVER_IDS:
        return
    _REGISTERED_SERVER_IDS.add(server_id)

    @mcp.resource("openworkgraph://automation-capabilities")
    def automation_capabilities() -> str:
        """Dated guidance for reasoning about modern automation capabilities."""
        return AUTOMATION_CAPABILITIES_MD

    @mcp.prompt()
    def find_automation_opportunities(scope: str = "current") -> str:
        """Reconstruct observed work and assess it against current agent capabilities."""
        return automation_opportunity_prompt(scope)
