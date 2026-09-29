from __future__ import annotations

"""Human work joined to agent runs: rework between turns, activity during and after."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared import tool_detail
from shared import workspace_rework as wr
from shared.agent_evidence import agent_event_to_evidence
from shared.claude_code_adapter import claude_hook_to_agent_events

ROOT = Path(__file__).resolve().parents[1]
SECRET = "TOP-SECRET-CONTENT"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(tool_detail, "_file_ref_key", lambda: b"test-key")
    path = tmp_path / "project"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "app.py").write_text("print('v1')\n")
    (path / f"{SECRET}.md").write_text("notes\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _hook(repo: Path, name: str, **extra) -> tuple[dict, list[dict]]:
    payload = {"session_id": "s", "prompt_id": "p", "hook_event_name": name, "cwd": str(repo), **extra}
    events = claude_hook_to_agent_events(payload)
    return payload, events


def _step(repo: Path, name: str, now: float, lease_ok: bool = True, **extra) -> list[dict]:
    payload, events = _hook(repo, name, **extra)
    wr.process_claude_hook(payload, events, now=now, lease_ok=lease_ok)
    return events


def _between(events: list[dict]) -> dict | None:
    return next((e.get("between_turns") for e in events if e.get("operation") == "run_started"), None)


def test_person_changing_agent_edited_files_between_turns_is_counted(repo):
    t = 1_000_000.0
    (repo / "app.py").write_text("print('agent edit')\n")
    _step(repo, "PostToolUse", t, tool_name="Edit", tool_use_id="e1", tool_input={"file_path": str(repo / "app.py")})
    _step(repo, "Stop", t + 5)
    # The person fixes the agent's file and adds a new one before the next prompt.
    (repo / "app.py").write_text("print('person fix')\n")
    (repo / "notes.txt").write_text(f"{SECRET}\n")
    events = _step(repo, "UserPromptSubmit", t + 125, prompt="next")
    assert _between(events) == {"head_moved": False, "files_changed": 2, "agent_files_changed": 1, "incomplete": False, "gap_seconds": 120.0}
    evidence = agent_event_to_evidence(next(e for e in events if e["operation"] == "run_started"))
    assert evidence["metadata"]["between_turns"]["agent_files_changed"] == 1
    state_dump = "".join(p.read_text() for p in (repo.parent / "auth" / "agent_workspace_state").glob("*.json"))
    assert SECRET not in state_dump and "app.py" not in state_dump and "person fix" not in state_dump
    # The snapshot is consumed: a second prompt without a new Stop reports nothing.
    assert _between(_step(repo, "UserPromptSubmit", t + 130, prompt="again")) is None


def test_untouched_project_reports_zero_and_head_moves_are_not_guessed(repo):
    t = 2_000_000.0
    (repo / "app.py").write_text("print('agent edit')\n")
    _step(repo, "PostToolUse", t, tool_name="Write", tool_use_id="w", tool_input={"file_path": str(repo / "app.py")})
    _step(repo, "Stop", t + 1)
    assert _between(_step(repo, "UserPromptSubmit", t + 10)) == {"head_moved": False, "files_changed": 0, "agent_files_changed": 0,
                                                                  "incomplete": False, "gap_seconds": 9.0}
    _step(repo, "Stop", t + 20)
    _git(repo, "commit", "-qam", "person commits")
    assert _between(_step(repo, "UserPromptSubmit", t + 60)) == {"head_moved": True, "gap_seconds": 40.0}


def test_nothing_happens_without_a_recording_lease_or_outside_git(repo, tmp_path):
    t = 3_000_000.0
    _step(repo, "Stop", t, lease_ok=False)
    assert not list((tmp_path / "auth").glob("agent_workspace_state/*.json"))
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    _step(plain, "Stop", t)
    assert _between(_step(plain, "UserPromptSubmit", t + 5)) is None


def test_repository_fsmonitor_config_cannot_run_commands(repo, tmp_path):
    marker = tmp_path / "fsmonitor-ran"
    script = tmp_path / "monitor.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(script))
    assert wr.snapshot(str(repo), key=b"k") is not None
    assert not marker.exists()


def test_symlinked_project_paths_still_match(repo, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(repo)
    t = 4_000_000.0
    _step(link, "PostToolUse", t, tool_name="Edit", tool_use_id="e", tool_input={"file_path": str(link / "app.py")})
    _step(link, "Stop", t + 1)
    (repo / "app.py").write_text("print('person')\n")
    assert _between(_step(link, "UserPromptSubmit", t + 30))["agent_files_changed"] == 1


def test_between_turns_is_sanitized():
    base = {"agent_name": "A", "session_id": "s", "operation": "run_started", "status": "running", "observed_at": "2026-09-28T00:00:00Z"}
    bad = agent_event_to_evidence({**base, "between_turns": {"head_moved": False, "files_changed": -1, "agent_files_changed": 9,
                                                             "gap_seconds": 10**9, "path": SECRET}})
    assert bad["metadata"]["between_turns"] == {"head_moved": False}
    assert "between_turns" not in agent_event_to_evidence({**base, "between_turns": {"files_changed": 3}})["metadata"]
    tool = agent_event_to_evidence({**base, "operation": "tool_call", "between_turns": {"head_moved": False, "files_changed": 1}})
    assert "between_turns" not in tool["metadata"]


# --- the server-side join ------------------------------------------------------------------------

T0 = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)


def _agent(run, op, secs, **extra):
    return agent_event_to_evidence({"agent_name": "Claude Code", "framework": "claude-code", "session_id": "sess", "run_id": run,
                                    "operation": op, "status": "running" if op == "run_started" else "success",
                                    "observed_at": (T0 + timedelta(seconds=secs)).isoformat(), **extra})


def _human(app, secs, duration, kind="focus_span", engaged=None):
    event = {"event_id": f"h-{app}-{secs}", "observed_at": (T0 + timedelta(seconds=secs)).isoformat(), "source": "desktop",
             "session_id": "human", "app": app, "window_title": f"{SECRET} title", "event_type": kind, "duration_seconds": duration,
             "metadata": {"activity": {"engaged_seconds": engaged if engaged is not None else duration}}}
    return event


def _scenario():
    return [
        _agent("turn-1", "run_started", 0), _agent("turn-1", "tool_call", 30, tool_name="Bash"), _agent("turn-1", "run_finished", 60),
        _agent("sess:sub:a", "run_started", 20), _agent("sess:sub:a", "run_finished", 40),
        _agent("turn-2", "run_started", 300, between_turns={"head_moved": False, "files_changed": 2, "agent_files_changed": 1, "gap_seconds": 240.0}),
        _agent("turn-2", "run_finished", 320),
        _human("iTerm2", 0, 40), _human("Slack", 40, 20, engaged=10),
        _human("Visual Studio Code", 60, 120), _human("Away", 180, 60, kind="away_span"), _human("Google Chrome", 240, 60),
    ]


def test_human_activity_during_and_after_each_run():
    from server.agent_execution_traces import agent_execution_traces
    from server.human_agent_join import app_category

    traces = agent_execution_traces(_scenario(), limit=10)["executions"]
    turn1 = next(t for t in traces if t["started_at"] == T0.isoformat() and not t.get("parent_execution_id"))
    during, after = turn1["human_context"]["during"], turn1["human_context"]["after"]
    assert during == {"human_capture_observed": True, "engaged_seconds": 50.0, "away_seconds": 0.0,
                      "by_category": {"terminal": 40.0, "communication": 10.0}}
    assert after["gap_seconds"] == 240.0 and after["engaged_seconds"] == 180.0 and after["away_seconds"] == 60.0
    assert after["by_category"] == {"editor": 120.0, "browser": 60.0}
    assert after["between_turns"]["agent_files_changed"] == 1
    child = next(t for t in traces if t["started_at"] == (T0 + timedelta(seconds=20)).isoformat())
    assert "after" not in (child.get("human_context") or {})  # a subagent is inside its parent's turn
    assert SECRET not in json.dumps(traces)
    assert [app_category(a) for a in ("Terminal", "Cursor", "Claude", "Arc", "Microsoft Teams", "Finder")] == [
        "terminal", "editor", "ai_assistant", "browser", "communication", "other"]


def test_no_human_capture_is_not_reported_as_zero():
    from server.agent_execution_traces import agent_execution_traces

    agent_only = [e for e in _scenario() if e.get("source") == "agent" or str(e.get("event_type", "")).startswith("agent_")]
    turn1 = next(t for t in agent_execution_traces(agent_only, limit=10)["executions"]
                 if t["started_at"] == T0.isoformat() and not t.get("parent_execution_id"))
    after = turn1["human_context"]["after"]
    assert "engaged_seconds" not in after and after["gap_seconds"] == 240.0 and "between_turns" in after


PRELUDE = r'''
import json
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events
from server.agent_ingest import ingest_agent_payloads
from server.history_retention import initialize_history_retention
from server import run_memory, agent_brief
from shared.claude_code_adapter import claude_hook_to_agent_events
init_db(); initialize_history_retention()
base = datetime.now(timezone.utc) - timedelta(hours=1)
def hook(name, secs, **extra):
    return claude_hook_to_agent_events({"session_id": "eph", "prompt_id": extra.pop("prompt_id", "p1"), "hook_event_name": name, **extra},
                                       observed_at=(base + timedelta(seconds=secs)).isoformat())
'''


def test_run_memory_and_brief_keep_the_human_context(tmp_path):
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "c.json"), "PYTHONPATH": str(ROOT)})
    code = PRELUDE + r'''
insert_events([{"event_id": "h1", "observed_at": (base + timedelta(seconds=70)).isoformat(), "device_id": "d", "session_id": "human-1",
                "app": "Visual Studio Code", "window_title": "secret.py", "event_type": "focus_span", "duration_seconds": 100,
                "metadata": {"activity": {"engaged_seconds": 100}}}])
first = hook("UserPromptSubmit", 0) + hook("PostToolUse", 10, tool_name="Bash", tool_use_id="a", tool_input={"command": "pytest"},
             tool_response={"stdout": "3 passed in 1s"}) + hook("Stop", 60)
second = hook("UserPromptSubmit", 200, prompt_id="p2")
for e in second:
    if e["operation"] == "run_started":
        e["between_turns"] = {"head_moved": False, "files_changed": 1, "agent_files_changed": 1, "gap_seconds": 140.0}
ingest_agent_payloads(first + second + hook("Stop", 220, prompt_id="p2") + hook("SessionEnd", 230))
[record] = [r for r in run_memory.memory_runs() if r.get("actor_kind") == "agent" and (r.get("human_context") or {}).get("after")]
assert record["human_context"]["after"]["by_category"] == {"editor": 100.0}
assert record["human_context"]["after"]["between_turns"]["agent_files_changed"] == 1
assert "secret.py" not in json.dumps(record)
agent_brief.set_enabled("claude-code", True, install_hook=False)
text = agent_brief.build_brief("claude-code")["text"]
assert "the person changed files the agent had just edited in 1 of 1 turn(s); median time to the next prompt: 2 min" in text, text
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr


def test_hook_entry_point_attaches_between_turns(repo, monkeypatch):
    import io

    import adapters.claude_code_hook as hook_main
    import server.agent_spool as spool

    sent: list[list[dict]] = []
    monkeypatch.setattr(hook_main, "post_agent_events", lambda events: sent.append(events))
    monkeypatch.setattr(spool, "valid_lease", lambda now=None: {"lease_id": "x"})

    def fire(payload):
        monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(payload).encode())})())
        assert hook_main.main([]) == 0

    base = {"session_id": "s", "cwd": str(repo)}
    fire({**base, "prompt_id": "p1", "hook_event_name": "Stop"})
    (repo / "app.py").write_text("print('person')\n")
    fire({**base, "prompt_id": "p2", "hook_event_name": "UserPromptSubmit", "prompt": SECRET})
    started = next(e for e in sent[-1] if e["operation"] == "run_started")
    assert started["between_turns"]["files_changed"] == 1
    assert SECRET not in json.dumps(sent)
