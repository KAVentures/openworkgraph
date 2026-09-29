from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_github_dot_com_can_become_an_outcome_poll_target():
    from server.outcome_tracker import _valid_ref

    good = {"host": "github.com", "owner": "acme", "repo": "widgets", "number": 42}
    assert _valid_ref(good) == ("github.com", "acme", "widgets", 42)
    assert _valid_ref({**good, "host": "evil.example"}) is None
    assert _valid_ref({**good, "host": "github.com.evil.example"}) is None


def test_watch_time_is_the_observed_run_time_not_delayed_ingest_time(tmp_path):
    code = r'''
from server.db import init_db, connect
from server.agent_ingest import ingest_agent_payloads
from server import outcome_tracker as ot
init_db(); ot.set_enabled(True)
observed = "2026-09-01T10:11:12+00:00"
event = {
    "agent_name": "Claude Code", "framework": "claude-code", "session_id": "s", "run_id": "r",
    "operation": "tool_call", "status": "success", "observed_at": observed,
    "tool_name": "Bash", "tool_category": "shell",
    "pr_watch": [{"host": "github.com", "owner": "acme", "repo": "widgets", "number": 42}],
}
ingest_agent_payloads([event])
with connect() as c:
    row = c.execute("SELECT created_at, host FROM outcome_watch").fetchone()
assert row is not None and row[0] == observed and row[1] == "github.com", row
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
