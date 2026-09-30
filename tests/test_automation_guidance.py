from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _listed_surfaces(tmp_path: Path) -> tuple[set[str], set[str], set[str]]:
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
        args=["-m", "mcp_server.compact_stdio"],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            return (
                {tool.name for tool in tools.tools},
                {prompt.name for prompt in prompts.prompts},
                {str(resource.uri) for resource in resources.resources},
            )


def test_compact_mcp_exposes_guidance_as_prompt_and_resource_not_tool(tmp_path):
    tools, prompts, resources = asyncio.run(_listed_surfaces(tmp_path))
    assert "find_automation_opportunities" in prompts
    assert any(uri.rstrip("/") == "openworkgraph://automation-capabilities" for uri in resources)
    assert "get_automation_capabilities" not in tools
    assert "find_automation_opportunities" not in tools


def test_automation_prompt_is_frontier_aware_and_shadow_safe():
    from mcp_server.automation_guidance import automation_opportunity_prompt

    prompt = automation_opportunity_prompt("week")
    assert "scope='week'" in prompt
    assert "CURRENT automation frontier" in prompt
    assert "READY TO AUTOMATE" in prompt
    assert "TEST" in prompt
    assert "NOT CURRENTLY PRACTICAL" in prompt
    assert "shadow trial" in prompt
    assert "explicit user authorization" in prompt
    assert "not observed, not unavailable" in prompt
    assert "replay historical work" in prompt


def test_capability_brief_is_dated_and_does_not_claim_client_capabilities():
    from mcp_server.automation_guidance import AUTOMATION_CAPABILITIES_MD

    assert "2026-09-30" in AUTOMATION_CAPABILITIES_MD
    assert "not a claim that every AI client" in AUTOMATION_CAPABILITIES_MD
    assert "Current AI tool surface" in AUTOMATION_CAPABILITIES_MD
    assert "Absence of an observed capability" in AUTOMATION_CAPABILITIES_MD
    assert "Shadow trials instead of historical replay" in AUTOMATION_CAPABILITIES_MD
