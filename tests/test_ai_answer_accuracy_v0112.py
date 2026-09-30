from __future__ import annotations

import json
from pathlib import Path

import pytest


def _canonical_claude(payload: dict, observed_at: str):
    from server.agent_identity import normalize_agent_events
    from shared.agent_evidence import agent_event_to_evidence
    from shared.claude_code_adapter import claude_hook_to_agent_events

    return [
        agent_event_to_evidence(event)
        for event in normalize_agent_events(
            claude_hook_to_agent_events(payload, observed_at=observed_at)
        )
    ]


def test_hook_and_transcript_are_one_claude_execution_with_one_tool_call():
    """The same physical Claude session must not become two runs or two tool calls."""
    from server.agent_execution_traces import agent_execution_traces
    from server.agent_session_sensor import _SOURCE_SPECS, _parse_claude
    from server.agent_session_store import session_ref
    from shared.agent_evidence import agent_event_to_evidence

    native_session = "cc-9"
    safe_session = session_ref("claude_code", native_session)
    tool_id = "toolu-auth-test"
    events = []
    events += _canonical_claude(
        {"session_id": native_session, "hook_event_name": "SessionStart", "cwd": "/repo"},
        "2026-09-30T12:00:00Z",
    )
    events += _canonical_claude(
        {"session_id": native_session, "prompt_id": "prompt-1", "hook_event_name": "UserPromptSubmit", "cwd": "/repo", "prompt": "SECRET PROMPT"},
        "2026-09-30T12:00:01Z",
    )
    events += _canonical_claude(
        {
            "session_id": native_session,
            "prompt_id": "prompt-1",
            "hook_event_name": "PostToolUse",
            "cwd": "/repo",
            "tool_use_id": tool_id,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_auth.py -q"},
            "tool_response": {"stdout": "1 failed, 11 passed in 1.00s\nSECRET TOOL OUTPUT"},
        },
        "2026-09-30T12:00:10Z",
    )

    # The transcript sees the same tool call at a different timestamp. Exact
    # opaque tool identity, not timestamp proximity, must collapse the fallback.
    transcript, _messages = _parse_claude(
        {
            "type": "assistant",
            "sessionId": native_session,
            "timestamp": "2026-09-30T12:00:17Z",
            "message": {
                "model": "claude",
                "content": [
                    {"type": "text", "text": "Working"},
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Bash",
                        "input": {"command": "pytest tests/test_auth.py -q"},
                    },
                    {"type": "thinking", "thinking": "PRIVATE CHAIN"},
                ],
            },
        },
        _SOURCE_SPECS["claude_code"],
        safe_session,
        2,
        "",
    )
    events += [agent_event_to_evidence(event) for event in transcript]

    events += _canonical_claude(
        {
            "session_id": native_session,
            "prompt_id": "prompt-1",
            "hook_event_name": "PostToolUse",
            "cwd": "/repo",
            "tool_use_id": "toolu-auth-pass",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_auth.py -q"},
            "tool_response": {"stdout": "12 passed in 0.80s"},
        },
        "2026-09-30T12:00:30Z",
    )
    events += _canonical_claude(
        {"session_id": native_session, "prompt_id": "prompt-1", "hook_event_name": "Stop", "cwd": "/repo", "last_assistant_message": "SECRET"},
        "2026-09-30T12:00:31Z",
    )

    result = agent_execution_traces(events, limit=10, max_events_per_execution=100)
    assert result["agent_execution_count_considered"] == 1
    [run] = result["executions"]
    assert run["operation_counts"]["tool_call"] == 2
    tests = run["work_summary"]["tests"]
    assert tests["runs"] == 2
    assert tests["known_failing_runs"] == 1
    assert tests["known_passing_runs"] == 1
    assert tests["unknown_result_runs"] == 0
    assert tests["runs_with_failures"] == 1
    assert tests["ended"] == "passing"
    assert run["outcome_status"] == "success"

    dump = json.dumps(result, ensure_ascii=False)
    for forbidden in (native_session, "prompt-1", tool_id, "SECRET TOOL OUTPUT", "PRIVATE CHAIN", "SECRET PROMPT"):
        assert forbidden not in dump


def test_unknown_test_result_never_becomes_zero_failures():
    from server.agent_execution_traces import _work_summary

    summary = _work_summary([
        {
            "operation": "tool_call",
            "tool": {"name": "Bash", "category": "shell", "detail": {"commands": ["pytest"], "test_status": "unknown"}},
        }
    ])
    tests = summary["tests"]
    assert tests["unknown_result_runs"] == 1
    assert tests["known_failing_runs"] == 0
    assert tests["runs_with_failures"] is None
    assert tests["runs_with_failures_status"] == "unknown_no_failure_count_observed"


def test_token_usage_is_observed_or_not_observed_never_estimated():
    from server.agent_execution_traces import _token_observability

    observed = _token_observability(
        [{"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}],
        agent={"framework": "codex"},
        observation_levels=["otel"],
    )
    assert observed == {
        "status": "observed",
        "basis": "canonical_provider_or_sdk_usage_telemetry",
        "counts": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        "estimated": False,
    }

    cursor = _token_observability([], agent={"framework": "cursor"}, observation_levels=["native_trace"])
    assert cursor["status"] == "not_observed"
    assert cursor["basis"] == "cursor_hook_does_not_expose_token_usage"
    assert cursor["counts"] == {}
    assert cursor["estimated"] is False
    assert cursor["absence_means"] == "not_observed_not_zero"


def test_trace_is_compact_by_default_and_rich_is_explicit(monkeypatch):
    from mcp_server import compact

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)
    rows = [
        {
            "event_id": f"event-{i}",
            "observed_at": f"2026-09-30T12:00:{i % 60:02d}Z",
            "app": "Google Chrome",
            "event_type": "focus_span",
            "duration_seconds": 10,
            "foreground_seconds": 10,
            "engaged_seconds": 0,
            "metadata": {"very_verbose": "PRIVATE" * 1000},
            "window_title": "Sensitive customer title",
        }
        for i in range(40)
    ]

    def fake_get(_path: str, _params=None):
        return {"rows": rows, "returned": 40, "total": 80, "has_more": True, "next_cursor": "c2", "scope": "all"}

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    default = compact.get_workflow_trace(scope="all")
    assert default["detail"] == "compact"
    assert default["has_more"] is True and default["next_cursor"] == "c2"
    assert all("metadata" not in row and "window_title" not in row for row in default["rows"])
    assert len(json.dumps(default)) < 15_000

    rich = compact.get_workflow_trace(scope="all", detail="rich", limit=1)
    assert rich["detail"] == "rich"
    assert rich["rows"][0]["metadata"]["very_verbose"].startswith("PRIVATE")


def test_work_profile_current_falls_back_to_today(monkeypatch):
    from mcp_server import compact

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)
    calls = []

    def fake_get(path: str, params=None):
        calls.append((path, dict(params or {})))
        scope = (params or {}).get("scope")
        if scope == "current":
            return {"scope": "current", "fragmentation": {"foreground_seconds": 0}, "manual_transfer_count": 0, "ai_tool_usage": {}}
        if scope == "today":
            return {"scope": "today", "fragmentation": {"foreground_seconds": 660}, "manual_transfer_count": 3, "ai_tool_usage": {"ChatGPT": {"foreground_seconds": 60}}}
        raise AssertionError(scope)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    profile = compact.get_work_profile()
    assert profile["scope_requested"] == "current"
    assert profile["scope_used"] == "today"
    assert profile["fragmentation"]["foreground_seconds"] == 660
    assert profile["manual_transfer_count"] == 3
    assert "showing retained work from local today" in profile["scope_hint"].lower()
    assert calls == [("/v1/work-profile", {"scope": "current"}), ("/v1/work-profile", {"scope": "today"})]


def test_repeated_workflow_uses_readable_steps_and_foreground_fallback():
    from mcp_server.compact import _slim_pattern

    item = {
        "suggested_label": "Compose and send email",
        "task_family": "email.reply",
        "observed_count": 3,
        "action_skeleton": [
            "gmail:open_email",
            "salesforce:open_account",
            "google_sheets:update_status",
            "gmail:send",
        ],
        "surfaces": ["Gmail", "Salesforce", "Google Sheets", "Gmail"],
        "median_engaged_seconds": 0,
        "total_foreground_seconds": 1980,
    }
    result = _slim_pattern(item)
    assert result["typical_steps"] == [
        "Gmail · Open Email",
        "Salesforce · Open Account",
        "Google Sheets · Update Status",
        "Gmail · Send",
    ]
    assert result["suggested_label"] == "Gmail · Open Email → Salesforce · Open Account → Google Sheets · Update Status → Gmail · Send"
    assert result["typical_duration_seconds"] == 660
    assert result["duration_basis"] == "foreground_time_fallback_no_engagement_signal"
    assert result["median_engaged_seconds"] == 0 or "median_engaged_seconds" not in result
    assert result["total_foreground_seconds"] == 1980


def test_aggregate_history_error_names_exact_fix(monkeypatch):
    from mcp_server import compact
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    def denied(_path: str, _params=None):
        raise RuntimeError("saved history access is outside the granted range")

    monkeypatch.setattr(compact.secure_runtime, "secure_get", denied)
    with pytest.raises(ToolError) as caught:
        compact.find_repeated_workflows()
    text = str(caught.value)
    assert "All saved history" in text
    assert "get_workflow_trace" in text
    assert "since/until" in text


def test_clean_native_sensor_scan_does_not_increment_error_counter(monkeypatch, tmp_path: Path):
    from server import agent_session_sensor as sensor
    from server import agent_session_store as store
    from server import db as server_db

    server_db.DB_PATH = tmp_path / "owg.db"
    store._POLICY_PATH = tmp_path / "agent_session_policy.json"
    sensor._STATE_PATH = tmp_path / "agent_session_sensor_state.json"
    server_db.init_db()
    store.init_agent_session_store()
    store.write_agent_session_policy({"native_session_observation_enabled": False, "capture_visible_messages": False})
    monkeypatch.setitem(sensor._SOURCE_SPECS["claude_code"], "pattern", str(tmp_path / "none-claude" / "**/*.jsonl"))
    monkeypatch.setitem(sensor._SOURCE_SPECS["codex"], "pattern", str(tmp_path / "none-codex" / "**/*.jsonl"))
    sensor._STATS["errors"] = 0
    sensor._STATS["last_error"] = None

    status = sensor.scan_once()
    assert status["errors"] == 0
    assert status["last_error"] is None


def test_simulated_workday_answer_shape_is_specific_without_sensitive_content(monkeypatch):
    """A compact connected-AI view of the requested dogfood scenario stays useful."""
    from mcp_server import compact

    monkeypatch.setattr(compact.core, "_begin", lambda _name: None)
    monkeypatch.setattr(compact.core, "_finish", lambda _name, value: value)

    pattern = {
        "suggested_label": "Compose and send email",
        "task_family": "crm.update",
        "observed_count": 3,
        "action_skeleton": ["gmail:open_email", "salesforce:open_account", "google_sheets:update_status", "gmail:send"],
        "surfaces": ["Gmail", "Salesforce", "Google Sheets", "Gmail"],
        "median_engaged_seconds": 0,
        "total_foreground_seconds": 1980,
    }

    def fake_get(path: str, params=None):
        if path == "/v1/tasks":
            return {"tasks": [], "patterns": [pattern]}
        if path == "/v1/summary":
            return {"repeated_task_patterns": [pattern]}
        if path == "/v1/procedural-memory":
            return {"families": [{"family_key": "human:crm.update", "actor_kind": "human", "family_basis": "observed", "execution_count": 3}]}
        raise AssertionError(path)

    monkeypatch.setattr(compact.secure_runtime, "secure_get", fake_get)
    result = compact.find_repeated_workflows()
    [workflow] = result["patterns"]
    assert workflow["observed_count"] == 3
    assert workflow["typical_duration_seconds"] == 660
    assert workflow["typical_steps"][0] == "Gmail · Open Email"
    assert workflow["typical_steps"][-1] == "Gmail · Send"
    dump = json.dumps(result, ensure_ascii=False)
    assert "Erik Lindqvist" not in dump
    assert "Anna Svensson" not in dump
    assert "SUPERSECRET" not in dump
