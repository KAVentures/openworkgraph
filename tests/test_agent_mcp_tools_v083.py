from __future__ import annotations

from pathlib import Path

from mcp_server import agent_tools


class _FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


def _sample_trace_payload() -> dict:
    return {
        "executions": [
            {
                "execution_id": "execution:0123456789abcdef",
                "started_at": "2026-09-26T10:00:00+00:00",
                "ended_at": "2026-09-26T10:00:05+00:00",
                "agent": {"name": "Agent X", "provider": "test", "framework": "custom"},
                "outcome_status": "success",
                "complete_boundary_observed": True,
                "operation_counts": {"model_call": 1, "tool_call": 2},
                "approval_request_count": 1,
                "observed_coverage": {
                    "signals_observed": {"tool_call": True},
                    "absence_means": "not_observed_not_proof_of_nonoccurrence",
                    "hidden_reasoning_observed": False,
                },
                "events": [{"operation": "tool_call", "tool": {"name": "search_repo"}}],
                "derived": True,
                "authoritative": False,
            }
        ],
        "returned": 1,
        "agent_execution_count_considered": 1,
        "native_run_ids_exposed": False,
        "prompt_content_exposed": False,
        "tool_arguments_exposed": False,
        "chain_of_thought_exposed": False,
    }


def test_agent_run_list_is_bounded_compact_and_uses_secure_finish(monkeypatch):
    fake = _FakeMCP()
    agent_tools._REGISTERED_MCP_IDS.clear()
    calls = []
    begun = []
    finished = []

    def secure_get(path, params=None):
        calls.append((path, dict(params or {})))
        return _sample_trace_payload()

    monkeypatch.setattr(agent_tools.secure_runtime, "secure_get", secure_get)
    monkeypatch.setattr(agent_tools.secure_runtime.core, "_begin", lambda name: begun.append(name))
    monkeypatch.setattr(agent_tools.secure_runtime.core, "_finish", lambda name, result: finished.append((name, result)) or result)

    agent_tools.register_agent_tools(fake)
    result = fake.tools["get_agent_runs"](family_key="agent:test", limit=999, evidence_limit=999999)

    assert begun == ["get_agent_runs"]
    assert calls == [("/v1/agent-execution-traces", {
        "family_key": "agent:test",
        "limit": 100,
        "evidence_limit": 100000,
        "max_events_per_execution": 1,
    })]
    assert finished and finished[0][0] == "get_agent_runs"
    assert result["events_omitted_from_list_view"] is True
    assert result["detail_tool"] == "get_agent_execution_trace"
    assert "events" not in result["executions"][0]
    assert result["executions"][0]["observed_coverage"]["hidden_reasoning_observed"] is False


def test_agent_trace_detail_uses_opaque_execution_id_and_bounded_event_count(monkeypatch):
    fake = _FakeMCP()
    agent_tools._REGISTERED_MCP_IDS.clear()
    calls = []

    monkeypatch.setattr(agent_tools.secure_runtime, "secure_get", lambda path, params=None: calls.append((path, dict(params or {}))) or _sample_trace_payload())
    monkeypatch.setattr(agent_tools.secure_runtime.core, "_begin", lambda _name: None)
    monkeypatch.setattr(agent_tools.secure_runtime.core, "_finish", lambda _name, result: result)

    agent_tools.register_agent_tools(fake)
    result = fake.tools["get_agent_execution_trace"]("execution:0123456789abcdef", max_events=9999)

    assert result["returned"] == 1
    assert calls[0][0] == "/v1/agent-execution-traces"
    assert calls[0][1]["execution_id"] == "execution:0123456789abcdef"
    assert calls[0][1]["max_events_per_execution"] == 500
    assert calls[0][1]["limit"] == 1


def test_both_supported_mcp_transports_register_agent_tools():
    root = Path(__file__).resolve().parents[1]
    stdio = (root / "mcp_server" / "secure_stdio.py").read_text(encoding="utf-8")
    http = (root / "mcp_server" / "http_app.py").read_text(encoding="utf-8")
    for source in (stdio, http):
        assert "from .agent_tools import register_agent_tools" in source
        assert "register_agent_tools(mcp)" in source
