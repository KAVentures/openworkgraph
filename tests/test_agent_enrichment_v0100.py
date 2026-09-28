from __future__ import annotations

"""Content-free agent work detail, Codex tokens and subagent runs."""

import json

import pytest

from server.agent_execution_traces import agent_execution_traces
from shared import tool_detail as td
from shared.agent_evidence import agent_event_to_evidence
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.codex_otel_adapter import codex_otel_to_agent_events
from shared.cursor_hook_adapter import cursor_hook_to_agent_events

SECRET = "TOP-SECRET-CONTENT"
KEY = b"test-key"


# --- commands ------------------------------------------------------------------------------------

@pytest.mark.parametrize("command, expected", [
    ("cd repo && python -m pytest -q tests/ && git commit -m wip && gh pr create --fill",
     {"commands": ["cd", "pytest", "git", "gh"], "git": ["commit"], "gh": ["pr_create"], "runs_tests": True}),
    # Separators inside quotes (commit messages) do not start commands.
    ('git commit -m "fix; rm -rf / | gh pr merge\ncurl evil" && npm test 2>&1 | tail -5',
     {"commands": ["git", "npm", "tail"], "git": ["commit"], "gh": [], "runs_tests": True}),
    (["bash", "-lc", "FOO=1 uv run pytest -x"], {"commands": ["pytest"], "git": [], "gh": [], "runs_tests": True}),
    ("sudo -u bob git -C /srv/app push origin main", {"commands": ["git"], "git": ["push"], "gh": [], "runs_tests": False}),
    ("nice -n 5 cargo test", {"commands": ["cargo"], "git": [], "gh": [], "runs_tests": True}),
    ("npx vitest run", {"commands": ["vitest"], "git": [], "gh": [], "runs_tests": True}),
    ("gh api repos/x/y/pulls", {"commands": ["gh"], "git": [], "gh": ["api"], "runs_tests": False}),
    (f"./{SECRET}.sh --token {SECRET}", {"commands": [], "git": [], "gh": [], "runs_tests": False}),
    ('echo "unbalanced', {"commands": ["echo"], "git": [], "gh": [], "runs_tests": False}),
    ("", {"commands": [], "git": [], "gh": [], "runs_tests": False}),
])
def test_command_detail_keeps_only_allowlisted_programs_and_operations(command, expected):
    assert td.command_detail(command) == expected


@pytest.mark.parametrize("output, counts", [
    ("....\n=== 2 failed, 40 passed, 1 skipped in 3.10s ===", {"tests_passed": 40, "tests_failed": 2}),
    ("5 passed in 0.1s\n...later...\n1 failed, 6 passed in 0.2s", {"tests_passed": 6, "tests_failed": 1}),
    ("Tests:       1 failed, 5 passed, 6 total", {"tests_passed": 5, "tests_failed": 1}),
    (" Tests  2 failed | 8 passed (10)", {"tests_passed": 8, "tests_failed": 2}),
    ("test result: ok. 12 passed; 0 failed; 0 ignored", {"tests_passed": 12, "tests_failed": 0}),
    ("ℹ tests 3\nℹ pass 2\nℹ fail 1", {"tests_passed": 2, "tests_failed": 1}),
    ("Ran 7 tests in 0.004s\n\nFAILED (failures=1, errors=1)", {"tests_passed": 5, "tests_failed": 2}),
    ("Ran 7 tests in 0.004s\n\nOK", {"tests_passed": 7, "tests_failed": 0}),
    (f"{SECRET} nothing recognisable", {}),
])
def test_test_counts_from_runner_summaries(output, counts):
    assert td.test_counts(output) == counts


def test_test_counts_need_a_test_command():
    detail = td.tool_call_detail(command="cat results.txt", output="99 passed in 1.0s")
    assert "tests_passed" not in detail


# --- files ---------------------------------------------------------------------------------------

def test_file_refs_are_keyed_stable_and_reveal_nothing():
    ref = td.file_ref("/Users/x/secret-project/app.py", key=KEY)
    assert ref.startswith("f:") and len(ref) == 18
    assert ref == td.file_ref("/Users/x/secret-project/./app.py", key=KEY)
    assert ref != td.file_ref("/Users/x/secret-project/app.py", key=b"other")
    assert "secret" not in ref and "app" not in ref
    assert td.file_type("/a/b/App.TSX") == "tsx"
    assert td.file_type("/a/.env") == ""
    assert td.file_type("/a/Dockerfile") == "dockerfile"
    assert td.file_type(f"/a/{SECRET}.weird") == ""


def test_patch_line_counts_structured_and_unified():
    hunks = [{"lines": [" keep", "-old", "+new", "+more"]}, {"lines": ["+x"]}]
    assert td.patch_line_counts(hunks) == (3, 1)
    assert td.patch_line_counts("--- a/x\n+++ b/x\n@@\n-a\n+b\n") == (1, 1)
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n+c\n*** Add File: README.md\n+hi\n*** End Patch"
    detail = td.apply_patch_detail(patch, key=KEY)
    assert detail["lines_added"] == 3 and detail["lines_removed"] == 1
    assert detail["file_types"] == ["py", "md"] and len(detail["file_refs"]) == 2


def test_sanitize_detail_is_the_gate():
    hostile = {
        "commands": ["pytest", SECRET, "rm -rf /", "pytest"],
        "git": ["commit", "commit --amend"],
        "gh": ["pr_create", SECRET],
        "file_types": ["py", "exe-" + SECRET],
        "file_refs": ["f:0123456789abcdef", "f:" + SECRET, "/etc/passwd"],
        "lines_added": True,
        "lines_removed": -3,
        "tests_passed": 10**12,
        "tests_failed": "4",
        "stdout": SECRET,
        "path": "/Users/x/" + SECRET,
    }
    out = td.sanitize_detail(hostile)
    assert out == {
        "commands": ["pytest"], "git": ["commit"], "gh": ["pr_create"], "file_types": ["py"],
        "file_refs": ["f:0123456789abcdef"], "tests_failed": 4,
    }
    assert td.sanitize_detail("not a dict") == {}
    assert len(td.sanitize_detail({"commands": sorted(td.COMMANDS)})["commands"]) == td.MAX_ITEMS


def test_evidence_keeps_detail_only_on_tool_calls():
    base = {"agent_name": "A", "session_id": "s", "observed_at": "2026-09-28T00:00:00Z", "status": "success",
            "tool_detail": {"commands": ["pytest"], "stdout": SECRET}}
    tool = agent_event_to_evidence({**base, "operation": "tool_call", "tool_name": "Bash"})
    assert tool["metadata"]["tool"]["detail"] == {"commands": ["pytest"]}
    assert tool["metadata"]["privacy"]["tool_arguments_captured"] is False
    model = agent_event_to_evidence({**base, "operation": "model_call"})
    assert "detail" not in model["metadata"]["tool"]
    assert SECRET not in json.dumps([tool, model])


# --- Claude Code ---------------------------------------------------------------------------------

def _claude(hook, **extra):
    return claude_hook_to_agent_events({"session_id": "sess", "prompt_id": "turn-1", "hook_event_name": hook, **extra},
                                       observed_at="2026-09-28T00:00:00Z")


def test_claude_bash_and_edit_detail_without_content(monkeypatch):
    monkeypatch.setattr(td, "_file_ref_key", lambda: KEY)
    [bash] = _claude("PostToolUse", tool_name="Bash", tool_use_id="t1",
                     tool_input={"command": f"pytest -q && git push  # {SECRET}"},
                     tool_response={"stdout": f"{SECRET}\n3 failed, 9 passed in 2.0s", "stderr": SECRET})
    assert bash["tool_detail"] == {"commands": ["pytest", "git"], "git": ["push"], "tests_passed": 9, "tests_failed": 3}
    [edit] = _claude("PostToolUse", tool_name="Edit", tool_use_id="t2",
                     tool_input={"file_path": f"/Users/x/{SECRET}/main.py", "old_string": SECRET, "new_string": SECRET},
                     tool_response={"structuredPatch": [{"lines": [f"-{SECRET}", f"+{SECRET}", f"+{SECRET}"]}]})
    assert edit["tool_detail"]["lines_added"] == 2 and edit["tool_detail"]["lines_removed"] == 1
    assert edit["tool_detail"]["file_types"] == ["py"]
    [write] = _claude("PostToolUse", tool_name="Write", tool_use_id="t3",
                      tool_input={"file_path": "/x/new.md", "content": "a\nb\nc"}, tool_response={"type": "create"})
    assert write["tool_detail"]["lines_added"] == 3 and write["tool_detail"]["lines_removed"] == 0
    [read] = _claude("PostToolUse", tool_name="Read", tool_use_id="t4", tool_input={"file_path": "/x/new.md"},
                     tool_response={"file": {"content": SECRET}})
    assert read["tool_detail"]["file_refs"] == write["tool_detail"]["file_refs"]
    assert "lines_added" not in read["tool_detail"]
    stored = [agent_event_to_evidence(e) for e in (bash, edit, write, read)]
    assert SECRET not in json.dumps(stored)


def test_tool_detail_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("OWG_AGENT_TOOL_DETAIL", "0")
    [bash] = _claude("PostToolUse", tool_name="Bash", tool_use_id="t1", tool_input={"command": "pytest"})
    assert "tool_detail" not in bash


def test_claude_subagent_is_its_own_run_linked_to_the_turn(monkeypatch):
    monkeypatch.setattr(td, "_file_ref_key", lambda: KEY)
    raw = []
    raw += _claude("UserPromptSubmit", prompt=SECRET)
    raw += _claude("PostToolUse", tool_name="Bash", tool_use_id="p1", tool_input={"command": "pytest"},
                   tool_response={"stdout": "1 failed, 4 passed in 1s"})
    raw += _claude("SubagentStart", agent_id="agent-x", agent_type="Explore")
    # Hooks fired inside the subagent carry its agent_id (and need not carry the
    # parent's prompt_id).
    raw += claude_hook_to_agent_events({"session_id": "sess", "hook_event_name": "PostToolUse", "agent_id": "agent-x",
                                        "agent_type": "Explore", "tool_name": "Edit", "tool_use_id": "c1",
                                        "tool_input": {"file_path": "/x/a.py"},
                                        "tool_response": {"structuredPatch": [{"lines": ["+a", "+b", "-c"]}]}},
                                       observed_at="2026-09-28T00:00:01Z")
    raw += _claude("SubagentStop", agent_id="agent-x", agent_type="Explore")
    raw += _claude("PostToolUse", tool_name="Bash", tool_use_id="p2", tool_input={"command": "pytest && git commit -m x"},
                   tool_response={"stdout": "5 passed in 1s"})
    raw += _claude("Stop")
    child_runs = {e["run_id"] for e in raw if e.get("agent_name", "").endswith("/Explore") or e["operation"] == "run_finished" and e["run_id"] != "turn-1"}
    assert child_runs == {"sess:sub:agent-x"}

    evidence = [agent_event_to_evidence(e) for e in raw]
    traces = agent_execution_traces(evidence, limit=10, max_events_per_execution=50)["executions"]
    assert len(traces) == 2
    parent = next(t for t in traces if t.get("child_execution_ids"))
    child = next(t for t in traces if t.get("parent_execution_id"))
    assert child["parent_execution_id"] == parent["execution_id"]
    assert parent["child_execution_ids"] == [child["execution_id"]]
    assert parent["work_summary"]["tests"] == {"runs": 2, "runs_with_failures": 1, "last_passed": 5, "last_failed": 0, "ended": "passing"}
    assert parent["work_summary"]["git"] == {"commit": 1}
    assert child["work_summary"]["lines"] == {"added": 2, "removed": 1}
    assert child["work_summary"]["files"] == {"edited": 1, "read_only": 0}
    assert SECRET not in json.dumps(traces)


def test_long_subagent_ids_stay_valid_ingress_ids():
    [_, started] = claude_hook_to_agent_events({"session_id": "s" * 120, "hook_event_name": "SubagentStart",
                                                "agent_id": "a" * 60, "agent_type": "Plan"})
    assert started["run_id"].startswith("sub:") and len(started["run_id"]) <= 128


# --- Cursor --------------------------------------------------------------------------------------

def test_cursor_command_and_file_detail(monkeypatch):
    monkeypatch.setattr(td, "_file_ref_key", lambda: KEY)
    [shell] = cursor_hook_to_agent_events({"hook_event_name": "postToolUseFailure", "conversation_id": "c", "generation_id": "g",
                                           "tool_name": "Shell", "tool_input": {"command": f"npm test -- {SECRET}"},
                                           "tool_output": f"{SECRET}\nTests:       2 failed, 8 passed, 10 total"})
    assert shell["tool_detail"] == {"commands": ["npm"], "tests_passed": 8, "tests_failed": 2}
    [edit] = cursor_hook_to_agent_events({"hook_event_name": "postToolUse", "conversation_id": "c", "generation_id": "g",
                                          "tool_name": "edit_file", "tool_input": {"target_file": f"/{SECRET}/x.ts"}})
    assert edit["tool_detail"]["file_types"] == ["ts"]
    assert SECRET not in json.dumps([agent_event_to_evidence(shell), agent_event_to_evidence(edit)])


# --- Codex ---------------------------------------------------------------------------------------

def _attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _codex(name, ts, conversation="conv", **attrs):
    return {"timeUnixNano": "1790294400000000000", "body": {"stringValue": SECRET},
            "attributes": [_attr(k, v) for k, v in {"event.name": name, "event.timestamp": ts,
                                                     "conversation.id": conversation, "model": "gpt-x", **attrs}.items()]}


def _logs(*records):
    return {"resourceLogs": [{"scopeLogs": [{"logRecords": list(records)}]}]}


def _trace_copy(record):
    return {"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "t", "spanId": "s", "events": [{
        "timeUnixNano": record["timeUnixNano"],
        "attributes": [a for a in record["attributes"] if a["key"] not in {"arguments", "output"}],
    }]}]}]}]}


def _codex_tool(seq="1", **attrs):
    return _codex("codex.tool_result", "2026-09-28T00:00:02Z", tool_name="exec_command", call_id=f"call-{seq}",
                  tool_result_seq=seq, success=True, duration_ms=10,
                  arguments=json.dumps({"cmd": f"pytest -q # {SECRET}"}), output=f"{SECRET}\n2 failed, 9 passed in 1.0s", **attrs)


def test_codex_tokens_come_from_response_completed_and_requests_are_not_double_counted():
    events, stats = codex_otel_to_agent_events(_logs(
        _codex("codex.api_request", "2026-09-28T00:00:00Z", attempt=1, **{"http.response.status_code": 500, "error.message": SECRET}),
        _codex("codex.api_request", "2026-09-28T00:00:01Z", attempt=2, success=True, **{"http.response.status_code": 200}),
        _codex("codex.sse_event", "2026-09-28T00:00:01.5Z", **{"event.kind": "response.output_text.delta"}),
        _codex("codex.sse_event", "2026-09-28T00:00:02Z", **{"event.kind": "response.completed", "input_token_count": 1200,
               "output_token_count": 300, "cached_token_count": 800, "reasoning_token_count": 50, "tool_token_count": 1500}),
    ))
    models = [e for e in events if e["operation"] == "model_call"]
    assert [m["status"] for m in models] == ["error", "success"]
    assert models[1]["usage"] == {"input_tokens": 1200, "output_tokens": 300, "cached_input_tokens": 800, "total_tokens": 1500}
    assert "usage" not in models[0] or not models[0]["usage"]
    assert stats["records_ignored"] == 2
    assert SECRET not in json.dumps(events)


def test_codex_shell_and_apply_patch_detail(monkeypatch):
    monkeypatch.setattr(td, "_file_ref_key", lambda: KEY)
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-" + SECRET + "\n+a\n+b\n*** End Patch"
    events, _ = codex_otel_to_agent_events(_logs(
        _codex_tool(),
        _codex("codex.tool_result", "2026-09-28T00:00:03Z", tool_name="apply_patch", call_id="call-2", tool_result_seq="2",
               success=True, arguments=json.dumps({"input": patch}), output=SECRET),
    ))
    shell, apply = events
    assert shell["tool_detail"] == {"commands": ["pytest"], "tests_passed": 9, "tests_failed": 2}
    assert apply["tool_detail"]["lines_added"] == 2 and apply["tool_detail"]["lines_removed"] == 1
    assert apply["tool_detail"]["file_types"] == ["py"]
    assert SECRET not in json.dumps(events)


@pytest.mark.parametrize("order", ["trace_first", "log_first"])
def test_codex_detail_survives_whichever_copy_is_stored_first(order):
    from server.agent_ingest import ingest_codex_otel_payload
    from server.db import rows

    conversation = f"conv-{order}"
    log = _codex_tool(conversation=conversation)
    trace = _trace_copy(log)
    payloads = [trace, _logs(log)] if order == "trace_first" else [_logs(log), trace]
    for payload in payloads:
        ingest_codex_otel_payload(payload)
    stored = [r for r in rows("SELECT * FROM events WHERE source = 'agent'") if r["session_id"] == conversation]
    assert len(stored) == 1
    assert stored[0]["metadata"]["tool"]["detail"] == {"commands": ["pytest"], "tests_passed": 9, "tests_failed": 2}
    assert SECRET not in json.dumps(stored)


def test_duplicate_copies_never_overwrite_stored_detail():
    from server.agent_ingest import ingest_agent_payloads
    from server.db import rows

    def tool(detail, session="merge-s"):
        return {"event_id": "merge-evt", "agent_name": "A", "framework": "custom-agent", "session_id": session,
                "observed_at": "2026-09-28T00:00:00Z", "operation": "tool_call", "status": "success",
                "tool_name": "Bash", "tool_detail": detail}

    ingest_agent_payloads([tool({"commands": ["pytest"]})])
    ingest_agent_payloads([tool({"commands": ["git"], "git": ["push"]})])
    [row] = [r for r in rows("SELECT * FROM events WHERE event_id = 'merge-evt'")]
    assert row["metadata"]["tool"]["detail"] == {"commands": ["pytest"]}


# --- MCP summaries -------------------------------------------------------------------------------

def test_mcp_run_summaries_carry_work_summary_and_links():
    from mcp_server.agent_tools import _run_summary
    from mcp_server.compact import _agent_run_summary

    execution = {"execution_id": "execution:x", "work_summary": {"tests": {"ended": "passing"}},
                 "parent_execution_id": "execution:p", "child_execution_ids": [], "usage_totals": {"total_tokens": 5},
                 "events": [{"secret": SECRET}]}
    for summary in (_run_summary(execution), _agent_run_summary(execution)):
        assert summary["work_summary"] == {"tests": {"ended": "passing"}}
        assert summary["parent_execution_id"] == "execution:p"
        assert summary["usage_totals"] == {"total_tokens": 5}
        assert SECRET not in json.dumps(summary)


def test_ephemeral_history_keeps_the_session_when_a_subagent_finishes(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "data"))
    from server.agent_ingest import ingest_agent_payloads
    from server.db import init_db, rows
    from shared.history_policy import update_retention

    init_db()
    update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)

    def count():
        return len([r for r in rows("SELECT session_id FROM events WHERE source = 'agent'") if r["session_id"] == "eph-sub"])

    def hook(name, **extra):
        return claude_hook_to_agent_events({"session_id": "eph-sub", "prompt_id": "t1", "hook_event_name": name, **extra})

    ingest_agent_payloads(hook("UserPromptSubmit") + hook("SubagentStart", agent_id="a1", agent_type="Explore"))
    ingest_agent_payloads(hook("SubagentStop", agent_id="a1", agent_type="Explore") + hook("Stop"))
    assert count() == 5
    ingest_agent_payloads(hook("SessionEnd"))
    assert count() == 0
