from __future__ import annotations

from server.agent_execution_traces import _work_summary
from shared import tool_detail as td
from shared.codex_otel_adapter import codex_otel_to_agent_events


def _attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _codex_sse(kind: str, timestamp: str, **attrs):
    values = {
        "event.name": "codex.sse_event",
        "event.kind": kind,
        "event.timestamp": timestamp,
        "conversation.id": "conv-hardening",
        "turn.id": "turn-1",
        **attrs,
    }
    return {
        "timeUnixNano": "1790294400000000000",
        "body": {"stringValue": "MUST-NOT-BE-STORED"},
        "attributes": [_attr(k, v) for k, v in values.items()],
    }


def _logs(*records):
    return {"resourceLogs": [{"scopeLogs": [{"logRecords": list(records)}]}]}


def test_malformed_shell_quote_does_not_invent_later_commands():
    detail = td.command_detail('echo "unfinished; git push && gh pr merge')
    assert detail == {"commands": ["echo"], "git": [], "gh": [], "runs_tests": False}


def test_test_invocation_has_explicit_conservative_unknown_status():
    assert td.tool_call_detail(command="pytest -q", output="truncated output") == {
        "commands": ["pytest"],
        "test_status": "unknown",
    }
    passing = td.tool_call_detail(command="pytest -q", output="5 passed in 0.1s")
    assert "test_status" not in passing
    assert passing["tests_passed"] == 5 and passing["tests_failed"] == 0
    failing = td.tool_call_detail(command="pytest -q", output="2 failed, 3 passed in 0.1s")
    assert "test_status" not in failing
    assert failing["tests_passed"] == 3 and failing["tests_failed"] == 2


def test_sanitize_detail_only_accepts_known_test_status():
    assert td.sanitize_detail({"test_status": "unknown"}) == {"test_status": "unknown"}
    assert td.sanitize_detail({"test_status": "secret text"}) == {}


def test_latest_unknown_test_run_does_not_inherit_earlier_passing_result():
    summary = _work_summary([
        {
            "operation": "tool_call",
            "tool": {"name": "Bash", "detail": {
                "commands": ["pytest"], "tests_passed": 5, "tests_failed": 0,
            }},
        },
        {
            "operation": "tool_call",
            "tool": {"name": "Bash", "detail": {"commands": ["pytest"], "test_status": "unknown"}},
        },
    ])
    assert summary["tests"] == {
        "runs": 2,
        "known_passing_runs": 1,
        "known_failing_runs": 0,
        "unknown_result_runs": 1,
        "runs_with_failures": None,
        "runs_with_failures_status": "unknown_no_failure_count_observed",
        "ended": "unknown",
    }


def test_cursor_style_edit_without_patch_counts_as_edited_not_read_only():
    summary = _work_summary([
        {
            "operation": "tool_call",
            "tool": {"name": "edit_file", "detail": {
                "file_refs": ["f:0123456789abcdef"], "file_types": ["ts"],
            }},
        },
    ])
    assert summary["files"] == {"edited": 1, "read_only": 0}


def test_claude_style_read_stays_read_only():
    summary = _work_summary([
        {
            "operation": "tool_call",
            "tool": {"name": "Read", "detail": {
                "file_refs": ["f:0123456789abcdef"], "file_types": ["py"],
            }},
        },
    ])
    assert summary["files"] == {"edited": 0, "read_only": 1}


def test_codex_response_failed_is_an_error_model_call_and_has_distinct_id():
    events, stats = codex_otel_to_agent_events(_logs(
        _codex_sse(
            "response.completed",
            "2026-09-28T00:00:01Z",
            input_token_count=10,
            output_token_count=5,
            cached_token_count=2,
            tool_token_count=15,
        ),
        _codex_sse("response.failed", "2026-09-28T00:00:02Z", **{"error.message": "private failure"}),
    ))
    assert stats["records_seen"] == 2
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2
    assert [event["status"] for event in events] == ["success", "error"]
    assert events[0]["usage"]["total_tokens"] == 15
    assert "private failure" not in str(events)
