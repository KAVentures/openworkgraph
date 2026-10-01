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
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.history_policy import update_retention
init_db(); initialize_history_retention()
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
base = datetime.now(timezone.utc) - timedelta(days=2)

def session(sid, arm, minute, tests, end=False):
    """One logged brief decision, then a turn observed after it.

    When an evaluation trial is active, seed the explicit trial assignment too;
    before evaluation this remains an ordinary, non-randomized brief log.
    """
    ref = agent_brief._evaluation_session_ref("claude-code", sid)
    assigned_at = (base + timedelta(minutes=minute)).isoformat()
    with connect() as c:
        agent_brief._ensure_trial_table(c)
        state = agent_brief.evaluation_state()
        if state.get("enabled") and state.get("trial_id"):
            c.execute("INSERT OR REPLACE INTO agent_brief_trial_assignment(trial_id, session_ref, arm, assigned_at) VALUES (?, ?, ?, ?)",
                      (state["trial_id"], ref, arm, assigned_at))
        c.execute("INSERT INTO agent_brief_log(delivered_at, framework, session_ref, scope, arm) VALUES (?, 'claude-code', ?, 'this_project', ?)",
                  (assigned_at, ref, arm))
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


def test_holdout_is_random_sticky_per_trial_and_historical_briefs_are_excluded(tmp_path):
    _run(r'''
import secrets
session("hist", "brief", 0, "3 passed in 1s")  # ordinary pre-evaluation history
agent_brief.set_enabled("claude-code", True, install_hook=False)
assert agent_brief.evaluation_enabled() is False
assert agent_brief.deliver("claude-code", session_id="n1")["text"]  # ordinary pre-evaluation brief

first = agent_brief.set_evaluation(True)
assert first["trial_id"].startswith("trial:") and first["enabled"] is True
draws = iter([5, 90])  # 5 < 20 -> control; 90 -> brief
secrets.randbelow = lambda n: next(draws)
assert agent_brief.deliver("claude-code", session_id="held")["text"] == ""
assert agent_brief.deliver("claude-code", session_id="held")["text"] == ""  # /clear keeps the arm (no new draw)
assert agent_brief.deliver("claude-code", session_id="got")["text"].startswith("OpenWorkGraph brief")
with connect() as c:
    arms = {r[0]: r[1] for r in c.execute("SELECT session_ref, arm FROM agent_brief_log").fetchall()}
assert arms[agent_brief._evaluation_session_ref("claude-code", "held")] == "control"
assert arms[agent_brief._evaluation_session_ref("claude-code", "got")] == "brief"
assert agent_brief.status()["briefs_delivered"] == 3  # hist + n1 + got; held is not counted as delivered
# Historical non-randomized briefs never enter the trial comparison.
r = ev.report()
assert r["sessions"] == {"brief": 1, "control": 1}, r

old_trial = agent_brief.set_evaluation(False)["trial_id"]
new_trial = agent_brief.set_evaluation(True)["trial_id"]
assert new_trial != old_trial  # a later experiment is a new cohort
assert ev.report()["sessions"] == {"brief": 0, "control": 0}
''', tmp_path)


def test_not_enough_data_is_the_honest_default(tmp_path):
    _run(r'''
assert ev.report()["status"] == "no_sessions_yet"
agent_brief.set_evaluation(True)
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
agent_brief.set_evaluation(True)
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
agent_brief.set_evaluation(True)
for i in range(12):
    outcome = "5 passed in 1s" if i % 2 else "1 failed, 4 passed in 1s"
    session(f"b{i}", "brief", i * 10, outcome)
    session(f"c{i}", "control", i * 10 + 5, outcome)
assert ev.report()["metrics"]["test_fail_rate"]["verdict"] == "no_clear_difference"
''', tmp_path / "null")


def test_runs_before_the_brief_do_not_count_and_run_memory_does(tmp_path):
    _run(r'''
agent_brief.set_evaluation(True)
update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)
session("eph", "brief", 10, "1 failed, 1 passed in 1s", end=True)
with connect() as c:
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0  # purged: memory only
ref = agent_brief._evaluation_session_ref("claude-code", "eph")
runs = ev._runs_by_session({ref}, (base - timedelta(days=1)).isoformat())
assert ev._session_metrics(runs[ref], (base + timedelta(minutes=10)).isoformat())["test_fail_rate"] == 1.0
assert ev._session_metrics(runs[ref], (base + timedelta(minutes=11)).isoformat())["test_fail_rate"] is None
''', tmp_path)


def test_open_pull_requests_are_right_censored_not_counted_as_non_merges(tmp_path):
    _run(r'''
run = {"started_at": base.isoformat(), "work_summary": {"commands": {"pytest": 1}},
       "delivery_outcome": {"prs": 2, "merged": 1, "closed_unmerged": 0, "open": 1}}
metrics = ev._session_metrics([run], (base - timedelta(seconds=1)).isoformat())
assert metrics["pr_merge_rate"] == 1.0  # 1 / 1 resolved, not 1 / 2 opened
only_open = {"started_at": base.isoformat(), "work_summary": {"commands": {"pytest": 1}},
             "delivery_outcome": {"prs": 1, "merged": 0, "closed_unmerged": 0, "open": 1}}
assert ev._session_metrics([only_open], (base - timedelta(seconds=1)).isoformat())["pr_merge_rate"] is None
''', tmp_path)
