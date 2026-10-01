from __future__ import annotations

"""Outcome tracking: did the agent's pull request merge, and did CI pass?"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared.tool_detail import pr_refs

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/acme/widgets/pull/42"


@pytest.mark.parametrize("kwargs, expected", [
    ({"command": "git push && gh pr create --fill", "output": f"Creating pull request\n{URL}\n"},
     [{"host": "github.com", "owner": "acme", "repo": "widgets", "number": 42}]),
    ({"command": "gh pr view 42", "output": URL}, []),  # viewing is not opening
    ({"command": "echo " + URL, "output": URL}, []),
    ({"tool_name": "mcp__github__create_pull_request", "output": {"html_url": URL}},
     [{"host": "github.com", "owner": "acme", "repo": "widgets", "number": 42}]),
    ({"command": "gh pr create", "output": "no url here"}, []),
])
def test_pr_refs_only_for_opened_pull_requests(kwargs, expected):
    assert pr_refs(**kwargs) == expected


def test_adapters_attach_pr_watch_beside_the_event_not_in_it():
    from shared.agent_evidence import agent_event_to_evidence
    from shared.claude_code_adapter import claude_hook_to_agent_events
    from shared.codex_otel_adapter import codex_otel_to_agent_events
    from shared.cursor_hook_adapter import cursor_hook_to_agent_events

    [claude] = claude_hook_to_agent_events({"session_id": "s", "prompt_id": "p", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                                            "tool_use_id": "t", "tool_input": {"command": "gh pr create --fill"},
                                            "tool_response": {"stdout": URL}})
    [cursor] = cursor_hook_to_agent_events({"hook_event_name": "postToolUse", "conversation_id": "c", "generation_id": "g",
                                            "tool_name": "Shell", "tool_input": {"command": "gh pr create"}, "tool_output": URL})
    record = {"timeUnixNano": "1790294400000000000", "attributes": [
        {"key": k, "value": {"stringValue": str(v)}} for k, v in {
            "event.name": "codex.tool_result", "event.timestamp": "2026-09-28T00:00:00Z", "conversation.id": "conv",
            "tool_name": "exec_command", "call_id": "c1", "tool_result_seq": "1", "success": "true",
            "arguments": json.dumps({"cmd": "gh pr create --fill"}), "output": URL}.items()]}
    [codex], _ = codex_otel_to_agent_events({"resourceLogs": [{"scopeLogs": [{"logRecords": [record]}]}]})
    for event in (claude, cursor, codex):
        assert event["pr_watch"][0]["number"] == 42
        stored = agent_event_to_evidence({k: v for k, v in event.items() if k != "pr_watch"})
        assert "acme" not in json.dumps(stored) and "widgets" not in json.dumps(stored)


PRELUDE = r'''
import json, subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from server.db import init_db, connect
from server.agent_ingest import ingest_agent_payloads
from server.agent_execution_traces import agent_execution_traces
from server.agent_session_store import session_ref as canonical_session_ref
from server.history_retention import initialize_history_retention, delete_sessions
from server.procedural_memory import load_recent_evidence, procedural_overview
from server import outcome_tracker as ot, run_memory
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.history_policy import update_retention
URL = "https://github.com/acme/widgets/pull/42"
init_db(); initialize_history_retention()
# Outcome tests that purge raw agent history exercise an explicit ephemeral
# retention choice, not the new-install undecided 7-day grace period.
update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)
base = datetime.now(timezone.utc) - timedelta(minutes=30)
ot._gh = lambda: "/usr/local/bin/gh"
calls = []
replies = []
def runner(args, **kwargs):
    calls.append((args, kwargs))
    if args[1:3] == ["auth", "status"]:
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    reply = replies.pop(0)
    if reply == "timeout":
        raise subprocess.TimeoutExpired(args, 20)
    if reply == "fail":
        return SimpleNamespace(returncode=1, stdout="", stderr="GraphQL: Could not resolve SECRET")
    return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")

def hook(session, name, offset, **extra):
    return claude_hook_to_agent_events({"session_id": session, "prompt_id": session + "-t", "hook_event_name": name, **extra},
                                       observed_at=(base + timedelta(seconds=offset)).isoformat())

def pr_run(session):
    return (hook(session, "UserPromptSubmit", 0)
            + hook(session, "PostToolUse", 1, tool_name="Bash", tool_use_id="a", tool_input={"command": "gh pr create --fill"},
                   tool_response={"stdout": URL})
            + hook(session, "Stop", 2))

def watches():
    with connect() as c:
        ot._ensure(c)
        return [dict(r) for r in c.execute("SELECT * FROM outcome_watch").fetchall()]

def events_dump():
    with connect() as c:
        return json.dumps([dict(r) for r in c.execute("SELECT * FROM events").fetchall()])
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"), "PYTHONPATH": str(ROOT)})
    result = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_off_by_default_nothing_is_watched_and_github_is_never_called(tmp_path):
    _run(r'''
assert ot.enabled() is False
ingest_agent_payloads(pr_run("s-off"))
assert watches() == []
assert "acme" not in events_dump()
assert ot.poll_once(runner=runner) == {"checked": 0, "resolved": 0, "errors": 0, "expired": 0}
assert calls == []
assert ot.status(runner=runner)["gh_logged_in"] is None and calls == []  # not even an auth probe while off
''', tmp_path)


def test_pr_is_watched_polled_and_resolved_into_a_content_free_outcome(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
ot.set_enabled(True)
ingest_agent_payloads(pr_run("s-on"))
[w] = watches()
assert (w["host"], w["owner"], w["repo"], w["number"], w["resolved"]) == ("github.com", "acme", "widgets", 42, 0)
assert "acme" not in events_dump()  # the link lives only in the watch list

replies.append({"state": "OPEN", "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]})
assert ot.poll_once(runner=runner)["checked"] == 1
args, kwargs = calls[-1]
assert args == ["/usr/local/bin/gh", "pr", "view", URL, "--json", "state,mergedAt,closedAt,statusCheckRollup"]
assert kwargs.get("timeout") == ot.GH_TIMEOUT_SECONDS and "shell" not in kwargs
assert ot.poll_once(runner=runner)["checked"] == 0  # not due again for 10 minutes

later = datetime.now(timezone.utc) + timedelta(minutes=11)
replies.append({"state": "MERGED", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"state": "SUCCESS"}]})
assert ot.poll_once(now=later, runner=runner)["resolved"] == 1
[w] = watches()
assert (w["host"], w["owner"], w["repo"], w["number"], w["resolved"], w["state"], w["ci"]) == ("", "", "", 0, 1, "merged", "passing")

traces = agent_execution_traces(load_recent_evidence(), limit=10)["executions"]
turn = next(t for t in traces if t.get("delivery_outcome"))
assert turn["delivery_outcome"] == {"prs": 1, "merged": 1, "closed_unmerged": 0, "open": 0, "unknown": 0, "ci": "passing"}
family = procedural_overview(load_recent_evidence(), min_support=1)["families"]
assert any(f.get("delivery", {}).get("merged") == 1 for f in family)
''', tmp_path)


def test_outcome_reaches_run_memory_after_the_session_is_purged(tmp_path):
    _run(r'''
ot.set_enabled(True)
ingest_agent_payloads(pr_run("s-eph") + hook("s-eph", "SessionEnd", 5))  # ephemeral: raw purged now
with connect() as c:
    assert c.execute("SELECT COUNT(*) FROM events WHERE session_id = 's-eph'").fetchone()[0] == 0
assert len(watches()) == 1  # still watched after the purge
replies.append({"state": "CLOSED", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}]})
ot.poll_once(runner=runner)
[record] = [r for r in run_memory.memory_runs() if r.get("actor_kind") == "agent"]
assert record["delivery_outcome"]["closed_unmerged"] == 1 and record["delivery_outcome"]["ci"] == "failing"
''', tmp_path)


def test_errors_are_codes_not_text_and_links_expire(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
ot.set_enabled(True)
ingest_agent_payloads(pr_run("s-err"))
replies.extend(["fail"])
assert ot.poll_once(runner=runner)["errors"] == 1
assert watches()[0]["error"] == "gh_failed" and "SECRET" not in json.dumps(watches())
replies.extend(["timeout"])
assert ot.poll_once(now=datetime.now(timezone.utc) + timedelta(minutes=11), runner=runner)["errors"] == 1
assert watches()[0]["error"] == "timeout"
assert ot.poll_once(now=datetime.now(timezone.utc) + timedelta(days=31), runner=runner)["expired"] == 1
w = watches()[0]
assert w["resolved"] == 1 and w["owner"] == "" and w["number"] == 0
ot._gh = lambda: None
ingest_agent_payloads(pr_run("s-nogh"))
ot.poll_once(runner=runner)
assert ot.status(runner=runner)["gh_found"] is False and ot.status(runner=runner)["last_error"] == "gh_not_found"
''', tmp_path)


def test_switching_off_and_deleting_sessions_remove_links(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
ot.set_enabled(True)
ingest_agent_payloads(pr_run("s-a") + pr_run("s-b"))
assert len(watches()) == 2
delete_sessions("agent", [canonical_session_ref("claude_code", "s-a")], reason="user_deleted_session")
assert len(watches()) == 1
assert ot.set_enabled(False)["pr_links_deleted"] == 1
assert watches() == []
''', tmp_path)


def test_hostile_refs_are_rejected(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
ot.set_enabled(True)
events = pr_run("s-bad")
tool = next(e for e in events if e.get("pr_watch"))
tool["pr_watch"] = [{"host": "github.com", "owner": "a;rm -rf /", "repo": "b", "number": 1},
                    {"host": "evil host", "owner": "a", "repo": "b", "number": 1},
                    {"host": "github.com", "owner": "a", "repo": "b", "number": -3},
                    "not a dict"]
ingest_agent_payloads(events)
assert watches() == []
''', tmp_path)
