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
    "get_workflow_knowledge",
    "save_workflow_knowledge",
    "forget_workflow_knowledge",
    "get_task_context",
    "how_did_similar_runs_go",
    "get_agent_runs",
    "get_agent_handoff",
    "get_playbooks",
    "get_automation_capabilities",
    "read_evidence_file",
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
        return {"path": path, "evidence_refs": ["event:1"]}

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.how_did_similar_runs_go("agent:test")

    assert paths == [
        "/v1/procedural-memory",
        "/v1/procedural-memory/similar-runs",
        "/v1/procedural-memory/failure-patterns",
        "/v1/procedural-memory/approval-patterns",
        "/v1/procedural-memory/next-steps",
        "/v1/procedural-memory/context-pack",
    ]
    assert result["status"] == "ok"
    assert result["family_key"] == "agent:test"
    assert result["resolution_method"] == "exact_family_key"
    assert result["approval_request_hotspots"]["evidence_refs"] == ["event:1"]
    assert result["interpretation"] == {
        "derived": True,
        "authoritative": False,
        "prescriptive": False,
        "causal": False,
        "observed_behavior_becomes_policy": False,
        "approval_patterns_are_policy": False,
        "semantic_steps_change_family_identity": False,
    }


def test_legacy_stdio_entrypoint_remains_available_and_unchanged_in_source():
    source = (ROOT / "mcp_server" / "secure_stdio.py").read_text(encoding="utf-8")
    assert "from .secure_runtime import mcp" in source
    assert "register_agent_tools(mcp)" in source
    assert "compact" not in source


def test_new_connection_paths_use_compact_surface():
    bundle = (ROOT / "integrations" / "mcpb" / "server" / "index.js").read_text(encoding="utf-8")
    launcher = (ROOT / "mcp_server" / "launcher.py").read_text(encoding="utf-8")
    control = (ROOT / "server" / "mcp_http_control.py").read_text(encoding="utf-8")
    secure_app = (ROOT / "server" / "secure_app.py").read_text(encoding="utf-8")
    assert "launcher.py" in bundle
    assert "mcp_server.compact_stdio" in launcher
    assert "mcp_connection import stdio_connection_config" in secure_app
    assert "mcp_server.compact_http_app:app" in control
    assert "mcp_server.secure_stdio" not in bundle
    assert '"args": ["-m", "mcp_server.secure_stdio"]' not in secure_app
    assert "mcp_server.http_app:app" not in control


def test_compact_agent_summary_exposes_observed_successful_ending_without_upgrading_terminal_outcome():
    from mcp_server.compact import _agent_run_summary

    summary = _agent_run_summary({
        "execution_id": "execution:aaaaaaaaaaaaaaaa",
        "outcome_status": "unknown",
        "outcome_basis": "no_terminal_outcome_observed",
        "last_tool_status": "success",
        "last_observed_event_is_successful_tool_call": True,
        "observed_end_state": "successful_tool_call_observed_at_trace_end",
        "derived": True,
        "authoritative": False,
    })

    assert summary["outcome_status"] == "unknown"
    assert summary["last_tool_status"] == "success"
    assert summary["last_observed_event_is_successful_tool_call"] is True
    assert summary["observed_end_state"] == "successful_tool_call_observed_at_trace_end"
    assert summary["observed_outcome_summary"] == "succeeded (observed final step)"
    assert summary["observed_outcome_summary_is_terminal_run_status"] is False


def test_compact_agent_summary_uses_last_tool_before_session_end_bookkeeping():
    from mcp_server.compact import _agent_run_summary

    execution = {
        "execution_id": "execution:bbbbbbbbbbbbbbbb",
        "outcome_status": "unknown",
        "outcome_basis": "no_terminal_outcome_observed",
        "last_tool_status": "success",
        # SessionEnd is bookkeeping after the successful tool, so the legacy
        # "final event is tool" flag is false even though the last tool succeeded.
        "last_observed_event_is_successful_tool_call": False,
        "observed_end_state": "no_terminal_run_status_observed",
        "events": [
            {"operation": "tool_call", "status": "success"},
            {"event_type": "SessionEnd"},
        ],
        "derived": True,
        "authoritative": False,
    }

    summary = _agent_run_summary(execution)

    assert summary["outcome_status"] == "unknown"
    assert summary["last_tool_status"] == "success"
    assert summary["last_observed_event_is_successful_tool_call"] is False
    assert summary["observed_outcome_summary"] == "succeeded (observed final step)"
    assert summary["observed_outcome_summary_is_terminal_run_status"] is False


def test_current_context_orients_ai_to_repeated_and_agent_evidence(monkeypatch):
    import mcp_server.compact as compact

    def fake_get(path, params=None):
        if path == "/v1/workflow-trace":
            return {"rows": [{"event_id": "e1", "observed_at": "2026-10-06T10:00:00+00:00", "app": "Gmail"}]}
        if path == "/v1/context-pulse":
            return {"recent_evidence": [{"event_id": "e1", "observed_at": "2026-10-06T10:00:00+00:00", "app": "Gmail"}], "snapshot_at": "2026-10-06T10:01:00+00:00"}
        if path == "/v1/agent-execution-traces":
            return {"executions": [{"execution_id": "execution:1", "started_at": "2026-10-06T10:00:00+00:00", "ended_at": "2026-10-06T10:01:00+00:00"}]}
        if path == "/v1/tasks":
            return {"tasks": [], "patterns": [{"task_family": "email.reply", "observed_count": 3, "action_skeleton": ["gmail:open", "gmail:send"]}]}
        if path == "/v1/semantic-activity":
            return {"events": []}
        raise AssertionError(path)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    monkeypatch.setattr(compact.secure_runtime, "detail_level", lambda: "redacted")
    result = compact.get_current_work_context(limit=6)
    hints = result["navigation_hints"]
    assert result["orientation"]["recent_canonical_evidence_available"] is True
    assert result["orientation"]["repeated_work_candidates_available"] is True
    assert result["orientation"]["nearby_agent_runs_available"] is True
    assert any(hint.get("tool") == "find_repeated_workflows" and hint.get("then") == "get_workflow_evidence" for hint in hints)
    assert any(hint.get("tool") == "get_agent_runs" for hint in hints)
    assert result["orientation"]["hints_are_navigation_not_ground_truth"] is True
