from __future__ import annotations

import io
import json
import sys

from adapters import claude_code_hook
from shared.agent_evidence import agent_event_to_evidence
from shared.claude_code_adapter import claude_hook_to_agent_events


def test_post_tool_use_keeps_structure_and_drops_native_content():
    payload = {
        "session_id": "claude-session-1",
        "transcript_path": "/Users/private/.claude/projects/secret.jsonl",
        "cwd": "/Users/private/ConfidentialProject",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_use_id": "tool-123",
        "tool_input": {"command": "curl https://secret.invalid?token=SUPERSECRET"},
        "tool_response": {"stdout": "patient@example.com SUPERSECRET"},
        "duration_ms": 1250,
    }
    events = claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z")
    assert len(events) == 1
    event = events[0]
    assert event["operation"] == "tool_call"
    assert event["status"] == "success"
    assert event["tool_name"] == "Bash"
    assert event["tool_category"] == "shell"
    assert event["duration_seconds"] == 1.25

    canonical = agent_event_to_evidence(event)
    serialized = json.dumps(canonical, ensure_ascii=False)
    for forbidden in (
        "SUPERSECRET",
        "patient@example.com",
        "ConfidentialProject",
        "secret.jsonl",
        "curl https://",
    ):
        assert forbidden not in serialized


def test_content_heavy_hooks_are_ignored_instead_of_guessed():
    for hook, extra in (
        ("UserPromptSubmit", {"prompt": "top secret prompt"}),
        ("PreToolUse", {"tool_input": {"command": "secret"}, "tool_name": "Bash"}),
        ("Stop", {"last_assistant_message": "secret answer"}),
        ("MessageDisplay", {"message": "secret UI content"}),
    ):
        payload = {"session_id": "s1", "hook_event_name": hook, **extra}
        assert claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z") == []


def test_session_subagent_permission_and_failure_mappings_are_structural():
    cases = [
        ({"session_id": "s", "hook_event_name": "SessionStart"}, "run_started", "running"),
        ({"session_id": "s", "hook_event_name": "SessionEnd"}, "run_finished", "unknown"),
        ({"session_id": "s", "hook_event_name": "PermissionRequest", "tool_use_id": "t", "tool_name": "Edit"}, "human_approval_requested", "running"),
        # PermissionDenied is an auto-mode policy denial in current Claude Code,
        # so it must not be mislabeled as a human decision.
        ({"session_id": "s", "hook_event_name": "PermissionDenied", "tool_use_id": "t", "tool_name": "Edit"}, "error", "denied"),
        ({"session_id": "s", "hook_event_name": "StopFailure", "prompt_id": "p", "error": "private failure details"}, "error", "error"),
    ]
    for payload, operation, status in cases:
        [event] = claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z")
        assert event["operation"] == operation
        assert event["status"] == status
        assert "private failure details" not in json.dumps(event)

    [started] = claude_hook_to_agent_events(
        {"session_id": "s", "hook_event_name": "SubagentStart", "agent_id": "child", "agent_type": "Explore"},
        observed_at="2026-09-25T00:00:00Z",
    )
    [stopped] = claude_hook_to_agent_events(
        {
            "session_id": "s",
            "hook_event_name": "SubagentStop",
            "agent_id": "child",
            "agent_type": "Explore",
            "last_assistant_message": "do not store this",
            "agent_transcript_path": "/private/child.jsonl",
        },
        observed_at="2026-09-25T00:00:05Z",
    )
    assert started["run_id"] == stopped["run_id"] == "s:child"
    assert started["operation"] == "run_started"
    assert stopped["operation"] == "run_finished"
    assert "do not store this" not in json.dumps(stopped)
    assert "/private/child.jsonl" not in json.dumps(stopped)


def test_retry_of_same_hook_event_is_idempotent():
    payload = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "tool_use_id": "tool-1",
        "tool_name": "Read",
    }
    one = claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z")[0]
    two = claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:01Z")[0]
    assert one["event_id"] == two["event_id"]


def test_settings_fragment_registers_only_safe_supported_hooks():
    fragment = claude_code_hook.settings_fragment("python")
    hooks = fragment["hooks"]
    assert set(hooks) == set(claude_code_hook.SUPPORTED_EVENTS)
    assert "UserPromptSubmit" not in hooks
    assert "PreToolUse" not in hooks
    handler = hooks["PostToolUse"][0]["hooks"][0]
    assert handler["command"] == "python"
    assert handler["args"] == ["-m", "adapters.claude_code_hook"]
    assert handler["async"] is True
    assert handler["timeout"] == 2


def test_hook_cli_fails_open_without_printing_native_exception(monkeypatch, capsys):
    payload = {
        "session_id": "s",
        "hook_event_name": "PostToolUse",
        "tool_use_id": "tool-1",
        "tool_name": "Bash",
        "tool_input": {"command": "SUPERSECRET"},
    }

    class _FakeStdin:
        buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr(claude_code_hook, "post_agent_events", lambda _events: (_ for _ in ()).throw(RuntimeError("SUPERSECRET network failure")))
    monkeypatch.setenv("OWG_AGENT_ADAPTER_DEBUG", "1")
    assert claude_code_hook.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "OpenWorkGraph agent hook skipped one event" in captured.err
    assert "SUPERSECRET" not in captured.err
