from __future__ import annotations

"""Randomized evaluation of session briefs: holdout assignment and the comparison."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import json
from datetime import datetime, timedelta, timezone
from server.db import init_db, connect
from server.agent_ingest import ingest_agent_payloads
from server.history_retention import initialize_history_retention
from server import agent_brief, brief_evaluation as ev
from server.run_memory import _key, _session_ref
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.history_policy import update_retention
init_db(); initialize_history_retention()
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
base = datetime.now(timezone.utc) - timedelta(days=2)

def session(sid, arm, minute, tests, end=False):
    """One logged brief decision, then a turn observed after it."""
    ref = _session_ref(sid, key=_key())
    with connect() as c:
        c.execute(agent_brief._LOG_TABLE)
        c.execute("INSERT INTO agent_brief_log(delivered_at, framework, session_ref, scope, arm) VALUES (?, 'claude-code', ?, 'this_project', ?)",
                  ((base + timedelta(minutes=minute)).isoformat(), ref, arm))
    def hook(name, sec, **extra):
        return claude_hook_to_agent_events({"session_id": sid, "prompt_id": sid + "-t", "hook_event_name": name, **extra},
                                           observed_at=(base + timedelta(minutes=minute, seconds=sec)).isoformat())
    events = hook("UserPromptSubmit", 5) + hook("PostToolUse", 10, tool_name="Bash", tool_use_id="a", tool_input={"command": "pytest"},
                                                tool_response={"stdout": tests}) + hook("Stop", 20)
    if end:
        events += hook("SessionEnd", 30)
    ingest_agent_payloads(events)
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "c.json"), "PYTHONPATH": str(ROOT),
                "OWG_CONNECTIONS_HOME": str(tmp_path / "home"), "OWG_CLAUDE_SETTINGS_PATH": str(tmp_path / "home" / "settings.json")})
    result = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_holdout_is_random_sticky_per_session_and_off_by_default(tmp_path):
    _run(r'''
import secrets
session("hist", "brief", 0, "3 passed in 1s")  # history so a brief exists
agent_brief.set_enabled("claude-code", True, install_hook=False)
assert agent_brief.evaluation_enabled() is False
assert agent_brief.deliver("claude-code", session_id="n1")["text"]  # no evaluation: always briefed

agent_brief.set_evaluation(True)
draws = iter([5, 90])  # 5 < 20 -> control; 90 -> brief
secrets.randbelow = lambda n: next(draws)
assert agent_brief.deliver("claude-code", session_id="held")["text"] == ""
assert agent_brief.deliver("claude-code", session_id="held")["text"] == ""  # /clear keeps the arm (no new draw)
assert agent_brief.deliver("claude-code", session_id="got")["text"].startswith("OpenWorkGraph brief")
with connect() as c:
    arms = {r[0]: r[1] for r in c.execute("SELECT session_ref, arm FROM agent_brief_log").fetchall()}
assert arms[_session_ref("held", key=_key())] == "control" and arms[_session_ref("got", key=_key())] == "brief"
assert agent_brief.status()["briefs_delivered"] == 3  # held-back sessions are not counted as briefs sent
''', tmp_path)


def test_not_enough_data_is_the_honest_default(tmp_path):
    _run(r'''
assert ev.report()["status"] == "no_sessions_yet"
for i in range(3):
    session(f"b{i}", "brief", i * 10, "5 passed in 1s")
    session(f"c{i}", "control", i * 10 + 5, "1 failed, 4 passed in 1s")
r = ev.report()
assert r["status"] == "not_enough_data" and r["sessions"] == {"brief": 3, "control": 3}
assert r["metrics"]["test_fail_rate"]["verdict"] == "not_enough_data"
assert r["metrics"]["test_fail_rate"]["mean"] == {"brief": 0.0, "control": 1.0}
''', tmp_path)


def test_clear_effect_is_detected_and_null_effect_is_not(tmp_path):
    _run(r'''
for i in range(12):
    session(f"b{i}", "brief", i * 10, "5 passed in 1s" if i % 6 else "1 failed, 4 passed in 1s")
    session(f"c{i}", "control", i * 10 + 5, "1 failed, 4 passed in 1s" if i % 6 else "5 passed in 1s")
r = ev.report()
m = r["metrics"]["test_fail_rate"]
assert r["status"] == "ok" and m["sessions"] == {"brief": 12, "control": 12}
assert m["verdict"] == "briefed_better" and m["ci95"][1] < 0, m
assert ev.report()["metrics"]["test_fail_rate"]["ci95"] == m["ci95"]  # fixed seed: reproducible
assert r["metrics"]["turns"]["verdict"] == "no_clear_difference"  # no direction claimed
''', tmp_path / "effect")
    _run(r'''
for i in range(12):
    outcome = "5 passed in 1s" if i % 2 else "1 failed, 4 passed in 1s"
    session(f"b{i}", "brief", i * 10, outcome)
    session(f"c{i}", "control", i * 10 + 5, outcome)
assert ev.report()["metrics"]["test_fail_rate"]["verdict"] == "no_clear_difference"
''', tmp_path / "null")


def test_runs_before_the_brief_do_not_count_and_run_memory_does(tmp_path):
    _run(r'''
update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)
session("eph", "brief", 10, "1 failed, 1 passed in 1s", end=True)
with connect() as c:
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0  # purged: memory only
ref = _session_ref("eph", key=_key())
runs = ev._runs_by_session({ref}, (base - timedelta(days=1)).isoformat())
assert ev._session_metrics(runs[ref], (base + timedelta(minutes=10)).isoformat())["test_fail_rate"] == 1.0
assert ev._session_metrics(runs[ref], (base + timedelta(minutes=11)).isoformat())["test_fail_rate"] is None
''', tmp_path)
