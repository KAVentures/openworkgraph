from __future__ import annotations

import io
import json
import os
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
        ("PreToolUse", {"tool_input": {"command": "secret"}, "tool_name": "Bash"}),
        ("MessageDisplay", {"message": "secret UI content"}),
        # Turn hooks without a prompt_id have no turn identity; never fall back
        # to the session, or a turn's Stop would look like the session ending.
        ("UserPromptSubmit", {"prompt": "top secret prompt"}),
        ("Stop", {"last_assistant_message": "secret answer"}),
    ):
        payload = {"session_id": "s1", "hook_event_name": hook, **extra}
        assert claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z") == []


def test_turn_hooks_bound_one_prompt_without_copying_content():
    start = claude_hook_to_agent_events({
        "session_id": "s1", "prompt_id": "p1", "hook_event_name": "UserPromptSubmit",
        "prompt": "top secret prompt", "transcript_path": "/Users/x/secret.jsonl", "custom_instructions": "private",
    }, observed_at="2026-09-25T00:00:00Z")
    stop = claude_hook_to_agent_events({
        "session_id": "s1", "prompt_id": "p1", "hook_event_name": "Stop",
        "last_assistant_message": "secret answer", "stop_hook_active": False,
    }, observed_at="2026-09-25T00:01:00Z")
    tool = claude_hook_to_agent_events({
        "session_id": "s1", "prompt_id": "p1", "hook_event_name": "PostToolUse", "tool_use_id": "t", "tool_name": "Read",
    }, observed_at="2026-09-25T00:00:30Z")
    session_end = claude_hook_to_agent_events({"session_id": "s1", "hook_event_name": "SessionEnd"}, observed_at="2026-09-25T00:02:00Z")
    assert [(e["operation"], e["status"], e["run_id"]) for e in start + stop] == [
        ("run_started", "running", "p1"), ("run_finished", "success", "p1"),
    ]
    # The turn groups with its tools; the session keeps its own boundary.
    assert tool[0]["run_id"] == "p1" and session_end[0]["run_id"] == "s1"
    serialized = json.dumps(start + stop)
    for forbidden in ("top secret prompt", "secret answer", "secret.jsonl", "private"):
        assert forbidden not in serialized


def test_session_subagent_permission_and_failure_mappings_are_structural():
    cases = [
        ({"session_id": "s", "hook_event_name": "SessionStart"}, "run_started", "running"),
        ({"session_id": "s", "hook_event_name": "SessionEnd"}, "run_finished", "unknown"),
        ({"session_id": "s", "hook_event_name": "PermissionRequest", "tool_use_id": "t", "tool_name": "Edit"}, "human_approval_requested", "running"),
        # PermissionDenied can be an automatic policy denial, so it must not be
        # mislabeled as a human decision. The richer OTel decision event carries
        # the decision source when connected.
        ({"session_id": "s", "hook_event_name": "PermissionDenied", "tool_use_id": "t", "tool_name": "Edit"}, "error", "denied"),
        ({"session_id": "s", "hook_event_name": "StopFailure", "prompt_id": "p", "error": "private failure details"}, "error", "error"),
    ]
    for payload, operation, status in cases:
        [event] = claude_hook_to_agent_events(payload, observed_at="2026-09-25T00:00:00Z")
        assert event["operation"] == operation
        assert event["status"] == status
        assert "private failure details" not in json.dumps(event)

    started_events = claude_hook_to_agent_events(
        {
            "session_id": "s",
            "prompt_id": "prompt-turn-1",
            "hook_event_name": "SubagentStart",
            "agent_id": "child",
            "agent_type": "Explore",
        },
        observed_at="2026-09-25T00:00:00Z",
    )
    assert [event["operation"] for event in started_events] == ["handoff", "run_started"]
    handoff, started = started_events
    assert handoff["run_id"] == started["run_id"] == "prompt-turn-1"
    assert handoff["agent_name"] == "Claude Code"
    assert handoff["tool_name"] == "subagent:Explore"
    assert started["agent_name"] == "Claude Code/Explore"

    [stopped] = claude_hook_to_agent_events(
        {
            "session_id": "s",
            "prompt_id": "prompt-turn-1",
            "hook_event_name": "SubagentStop",
            "agent_id": "child",
            "agent_type": "Explore",
            "last_assistant_message": "do not store this",
            "agent_transcript_path": "/private/child.jsonl",
        },
        observed_at="2026-09-25T00:00:05Z",
    )
    assert stopped["run_id"] == "prompt-turn-1"
    assert stopped["operation"] == "run_finished"
    assert stopped["agent_name"] == "Claude Code/Explore"
    assert "do not store this" not in json.dumps(stopped)
    assert "/private/child.jsonl" not in json.dumps(stopped)


def test_prompt_id_correlates_tool_and_permission_hooks_to_one_turn():
    base = {"session_id": "session-1", "prompt_id": "prompt-1"}
    [tool] = claude_hook_to_agent_events(
        {**base, "hook_event_name": "PostToolUse", "tool_use_id": "t1", "tool_name": "Read"},
        observed_at="2026-09-25T00:00:00Z",
    )
    [approval] = claude_hook_to_agent_events(
        {**base, "hook_event_name": "PermissionRequest", "tool_use_id": "t2", "tool_name": "Edit"},
        observed_at="2026-09-25T00:00:01Z",
    )
    assert tool["run_id"] == approval["run_id"] == "prompt-1"
    assert tool["trace_id"] == approval["trace_id"] == "session-1"
    assert tool["sensor_id"] == approval["sensor_id"] == "agent:claude-code-hook"


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
    assert {"UserPromptSubmit", "Stop"} <= set(hooks)  # turn boundaries; content never read
    assert "PreToolUse" not in hooks
    handler = hooks["PostToolUse"][0]["hooks"][0]
    assert handler["command"] == claude_code_hook.hook_command("python")
    assert "args" not in handler
    assert handler["async"] is True
    assert handler["timeout"] == 2


def test_hook_command_resolves_adapters_package_from_any_working_directory(tmp_path):
    # Claude Code runs hooks from the session's project directory, and the
    # adapters package is not installed into the venv, so the generated command
    # must cd into the OpenWorkGraph root before running ``-m``. Paths with
    # spaces (e.g. "Application Support") must be quoted for the host shell.
    import subprocess

    command = claude_code_hook.hook_command(sys.executable)
    assert command.startswith("cd ")
    assert command.endswith(" -m adapters.claude_code_hook")
    if os.name == "nt":
        assert command.startswith("cd /d ")

    # Exercise the generated shell command itself from an unrelated cwd. Using
    # the module's own no-network print mode avoids nested shell quoting that
    # would otherwise make this regression test shell-specific.
    result = subprocess.run(
        command + " --print-settings",
        shell=True, cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert "hooks" in parsed


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
