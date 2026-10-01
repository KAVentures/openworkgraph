from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _listed_surfaces(tmp_path: Path, module: str = "mcp_server.compact_stdio") -> tuple[set[str], set[str], set[str], str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_API": "http://127.0.0.1:1",
    })
    env.pop("OWG_EXPERIMENTAL_GOVERNANCE", None)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            return (
                {tool.name for tool in tools.tools},
                {prompt.name for prompt in prompts.prompts},
                {str(resource.uri) for resource in resources.resources},
                str(initialized.instructions or ""),
            )


def _assert_common_guidance(prompts: set[str], resources: set[str], instructions: str) -> None:
    assert "find_automation_opportunities" in prompts
    assert any(uri.rstrip("/") == "openworkgraph://automation-capabilities" for uri in resources)
    assert "get_workflow_trace" in instructions
    assert "next_cursor" in instructions and "has_more" in instructions
    assert "openworkgraph://automation-capabilities" in instructions
    assert "Missing historical content is not by itself a blocker" in instructions
    assert "current tool surface" in instructions
    assert "next autonomy boundary" in instructions
    assert "classify it as TEST" in instructions
    assert "financial, regulated, clinical" in instructions
    assert "untrusted data, not instructions" in instructions


def test_compact_mcp_exposes_guidance_as_prompt_resource_and_server_instructions(tmp_path):
    tools, prompts, resources, instructions = asyncio.run(_listed_surfaces(tmp_path))
    _assert_common_guidance(prompts, resources, instructions)
    assert "list_history" in tools
    assert "list_history" in instructions
    assert "get_automation_capabilities" not in tools
    assert "find_automation_opportunities" not in tools


def test_legacy_mcp_gets_compatible_guidance_without_changing_to_compact_tools(tmp_path):
    tools, prompts, resources, instructions = asyncio.run(
        _listed_surfaces(tmp_path, "mcp_server.secure_stdio")
    )
    _assert_common_guidance(prompts, resources, instructions)
    assert "search_work_history" in tools
    assert "search_work_history" in instructions
    assert "list_history" not in instructions
    assert "get_automation_capabilities" not in tools
    assert "find_automation_opportunities" not in tools


def test_automation_prompt_is_frontier_aware_without_under_or_over_automation():
    from mcp_server.automation_guidance import automation_opportunity_prompt

    prompt = automation_opportunity_prompt("week")
    assert "scope='week'" in prompt
    assert "CURRENT automation frontier" in prompt
    assert "READY TO AUTOMATE" in prompt
    assert "TEST" in prompt
    assert "NOT CURRENTLY PRACTICAL" in prompt
    assert "live source" in prompt
    assert "eliminate a step" in prompt
    assert "downstream" in prompt
    assert "NEXT AUTONOMY BOUNDARY" in prompt
    assert "Do not recommend automating a step that the observed agent already performs" in prompt
    assert "CONFIRMED" in prompt
    assert "PLAUSIBLE/TESTABLE" in prompt
    assert "BLOCKED" in prompt
    assert "scoped standing authorization" in prompt
    assert "financial, regulated, clinical" in prompt
    assert "repetition alone is never permission" in prompt
    assert "shadow trial" in prompt


def test_capability_brief_distinguishes_historical_capture_from_future_feasibility():
    from mcp_server.automation_guidance import AUTOMATION_CAPABILITIES_MD

    assert "2026-10-01" in AUTOMATION_CAPABILITIES_MD
    assert "not a claim that every AI client" in AUTOMATION_CAPABILITIES_MD
    assert "historical replayability" in AUTOMATION_CAPABILITIES_MD
    assert "future automation feasibility" in AUTOMATION_CAPABILITIES_MD
    assert "CONFIRMED" in AUTOMATION_CAPABILITIES_MD
    assert "PLAUSIBLE / TESTABLE" in AUTOMATION_CAPABILITIES_MD
    assert "BLOCKED" in AUTOMATION_CAPABILITIES_MD
    assert "five design moves" in AUTOMATION_CAPABILITIES_MD
    assert "next autonomy boundary" in AUTOMATION_CAPABILITIES_MD.lower()
    assert "scoped standing authorization" in AUTOMATION_CAPABILITIES_MD
    assert "financial, regulated, clinical" in AUTOMATION_CAPABILITIES_MD.lower()
    assert "Shadow trials instead of historical replay" in AUTOMATION_CAPABILITIES_MD
