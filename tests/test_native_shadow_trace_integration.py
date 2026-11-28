"""Contract regression against the existing native hook-to-trace endpoint."""
from server import agent_execution_trace_routes as routes
from shared.agent_evidence import agent_event_to_evidence
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.native_shadow_trace_review import digest, review


def test_native_hook_trace_review_contract(monkeypatch):
    # Exercise actual adapter, structural trace projection and route
    # serialization, with the read boundary and retained rows substituted.
    [tool] = claude_hook_to_agent_events({
        "session_id": "session-1", "prompt_id": "prompt-1",
        "hook_event_name": "PostToolUse", "tool_use_id": "tool-1",
        "tool_name": "Read",
    }, observed_at="2026-09-27T10:00:00Z")
    raw = [agent_event_to_evidence(tool)]
    monkeypatch.setattr(routes, "_require_agent_report_read", lambda request: None)
    monkeypatch.setattr(routes, "load_recent_evidence", lambda **kwargs: raw)
    trace = routes.get_agent_execution_traces(None)
    [run] = trace["executions"]
    [event] = run["events"]
    declaration = {"package_sha256": "a"*64, "cases": [{
        "case_id": "case_1", "root_execution_id": run["execution_id"],
        "expected_framework": run["agent"]["framework"],
        "reviewed_tool_labels": [event["tool"]["name"]],
    }]}
    result = review(trace, digest(trace), declaration)
    [row] = result["cases"]
    assert row["observed_tool_call_count"] == 1
    assert row["status"] == "incomplete_observation"
    assert row["coverage_gaps"] == [{"execution_id": run["execution_id"], "code": "missing_run_boundary"}]
    assert not result["skill_invocation_verified"]
    declaration["cases"][0]["reviewed_tool_labels"] = []
    [row] = review(trace, digest(trace), declaration)["cases"]
    assert row["status"] == "observed_deviation_or_failure"
    assert row["deviations"][0]["event_ref"] == event["event_ref"]
