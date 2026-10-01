from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TOOLS = {
    "get_current_work_context",
    "get_context_pulse",
    "list_history",
    "search_work",
    "get_workflow_trace",
    "get_work_profile",
    "find_repeated_workflows",
    "get_workflow_evidence",
    "get_task_context",
    "how_did_similar_runs_go",
    "get_agent_runs",
    "get_agent_handoff",
    "get_playbooks",
}
EXPERIMENTAL_GOVERNANCE_TOOLS = {
    "get_action_policy_advisory",
    "get_governed_context_pack",
}


async def _listed_tools(tmp_path: Path, *, governance: bool) -> set[str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT),
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_API": "http://127.0.0.1:1",
    })
    if governance:
        env["OWG_EXPERIMENTAL_GOVERNANCE"] = "1"
    else:
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
            listed = await session.list_tools()
            return {tool.name for tool in listed.tools}


def test_compact_stdio_exposes_exact_default_tool_surface(tmp_path):
    names = asyncio.run(_listed_tools(tmp_path, governance=False))
    assert names == DEFAULT_TOOLS
    assert "get_agent_execution_trace" not in names
    assert "search_work_history" not in names
    assert "get_work_session" not in names
    assert "get_approval_patterns" not in names


def test_compact_stdio_adds_only_normative_governance_tools_when_enabled(tmp_path):
    names = asyncio.run(_listed_tools(tmp_path, governance=True))
    assert names == DEFAULT_TOOLS | EXPERIMENTAL_GOVERNANCE_TOOLS


def test_compact_agent_tool_uses_optional_execution_id_for_detail(monkeypatch):
    from mcp_server import compact

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def fake_get(path: str, params: dict | None = None):
        calls.append((path, dict(params or {})))
        return {"executions": [{"execution_id": "execution:abc", "events": [{"operation": "tool_call"}]}]}

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)

    listed = compact.get_agent_runs(limit=5)
    assert listed["events_omitted_from_list_view"] is True
    assert calls[-1][1]["max_events_per_execution"] == 1
    assert "execution_id" not in calls[-1][1]

    detailed = compact.get_agent_runs(execution_id="execution:abc", max_events=37)
    assert detailed["executions"][0]["events"][0]["operation"] == "tool_call"
    assert calls[-1][1]["execution_id"] == "execution:abc"
    assert calls[-1][1]["max_events_per_execution"] == 37
    assert calls[-1][1]["limit"] == 1


def test_similar_run_feedback_combines_observational_views(monkeypatch):
    from mcp_server import compact

    paths: list[str] = []
    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def fake_get(path: str, params: dict | None = None):
        paths.append(path)
        if path == "/v1/procedural-memory":
            return {
                "families": [
                    {
                        "family_key": "agent:test",
                        "actor_kind": "agent",
                        "family_basis": "structural_signature",
                        "execution_count": 2,
                        "positive_example_count": 2,
                        "explicit_failure_count": 0,
                        "confidence": "low",
                    }
                ]
            }
        if path == "/v1/run-memory":
            return {"runs": [{"family_key": "agent:test", "outcome_status": "unknown"}]}
        if path == "/v1/task-context":
            return {"approval_hotspots": [], "observed_next_steps": [], "context": []}
        return {}

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.how_did_similar_runs_go(family_key="agent:test")
    assert "/v1/procedural-memory" in paths
    assert "/v1/run-memory" in paths
    assert result["interpretation"]["observed_success_is_not_guaranteed_success"] is True
