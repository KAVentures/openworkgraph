from __future__ import annotations

"""Run memory: content-free run summaries that outlive retention, never deletion."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import json
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events, connect
from server.agent_ingest import ingest_agent_payloads
from server.agent_session_store import session_ref as canonical_session_ref
from server.history_retention import initialize_history_retention, cleanup_expired_history, delete_history_session, delete_sessions
from server import run_memory
from server.procedural_memory import load_recent_evidence, procedural_overview, similar_runs
from server.procedural_memory_routes import _raw
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.history_policy import update_retention, update_run_memory, run_memory_policy
SECRET = "TOP-SECRET-CONTENT"
init_db(); initialize_history_retention()
# These tests exercise the explicit "Don't keep after sessions" path. New
# installs now have a separate undecided 7-day grace period, so do not rely on
# the onboarding default to stand in for an explicit ephemeral choice.
update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)
base = datetime.now(timezone.utc) - timedelta(minutes=30)

def hook(session, name, offset, **extra):
    return claude_hook_to_agent_events({"session_id": session, "prompt_id": session + "-t1", "hook_event_name": name, **extra},
                                       observed_at=(base + timedelta(seconds=offset)).isoformat())

def claude_session(session, offset=0, tests="1 failed, 4 passed in 1s"):
    events = hook(session, "SessionStart", offset) + hook(session, "UserPromptSubmit", offset + 1, prompt=SECRET)
    events += hook(session, "PostToolUse", offset + 2, tool_name="Bash", tool_use_id="a",
                   tool_input={"command": f"pytest -q # {SECRET}"}, tool_response={"stdout": f"{SECRET}\n{tests}"})
    events += hook(session, "PostToolUse", offset + 3, tool_name="Edit", tool_use_id="b",
                   tool_input={"file_path": f"/Users/x/{SECRET}/app.py"}, tool_response={"structuredPatch": [{"lines": ["+a", "-b"]}]})
    events += hook(session, "Stop", offset + 4)
    return events

def raw_count(session):
    # v0.112 canonical Claude rows use the opaque cross-sensor session id. Keep
    # legacy raw-id coverage too so this helper works for human/old fixture rows.
    opaque = canonical_session_ref("claude_code", session)
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM events WHERE session_id IN (?, ?)", (session, opaque)).fetchone()[0]

def memory_rows():
    with connect() as c:
        run_memory._ensure(c)
        return [tuple(r) for r in c.execute("SELECT * FROM run_memory").fetchall()]
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_default_policy_is_on_for_90_days_and_normalizes(tmp_path):
    _run(r'''
assert run_memory_policy() == {"enabled": True, "days": 90}
update_run_memory(enabled=False, days=30)
assert run_memory_policy() == {"enabled": False, "days": 30}
for bad in (0, 5000):
    try:
        update_run_memory(enabled=True, days=bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''', tmp_path)


def test_ephemeral_session_end_keeps_a_content_free_run_that_procedural_memory_uses(tmp_path):
    _run(r'''
assert run_memory_policy()["enabled"] is True
ingest_agent_payloads(claude_session("eph-1"))
assert raw_count("eph-1") > 0
ingest_agent_payloads(hook("eph-1", "SessionEnd", 10))
assert raw_count("eph-1") == 0  # raw history purged as promised

rows = memory_rows()
assert len(rows) == 1, rows
dump = json.dumps(rows)
assert SECRET not in dump and "eph-1" not in dump  # no content, native ids only as keyed hashes
record = json.loads(rows[0][6])
assert record["source"] == "run_memory" and record["evidence_refs"] == []
assert record["work_summary"]["tests"]["ended"] == "failing"
assert record["work_summary"]["lines"] == {"added": 1, "removed": 1}

# Procedural memory (find_repeated_workflows / how_did_similar_runs_go) still sees it.
plain = procedural_overview(load_recent_evidence())
assert plain["execution_count"] == 0
merged = procedural_overview(_raw(25000, None))
assert merged["execution_count"] == 1
family = merged["families"][0]["family_key"] if merged["families"] else record["family_key"]
runs = similar_runs(_raw(25000, None), family_key=record["family_key"])["runs"]
assert runs and runs[0]["source"] == "run_memory"
''', tmp_path)


def test_live_runs_are_not_duplicated_by_memory(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
events = claude_session("live-1")
ingest_agent_payloads(events)
run_memory.remember(load_recent_evidence())  # memory of a run whose raw evidence still exists
assert len(memory_rows()) == 1
merged = procedural_overview(_raw(25000, None))
assert merged["execution_count"] == 1
''', tmp_path)


def test_person_deleting_a_session_deletes_its_memory(tmp_path):
    _run(r'''
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
ingest_agent_payloads(claude_session("del-1"))
run_memory.remember(load_recent_evidence())
assert len(memory_rows()) == 1
result = delete_sessions("agent", ["del-1"], reason="user_deleted_session")
assert result["run_memory_deleted"] == 1 and result["run_memory_kept"] == 0
assert memory_rows() == [] and raw_count("del-1") == 0
''', tmp_path)


def test_deleting_a_memory_only_run_from_history(tmp_path):
    _run(r'''
ingest_agent_payloads(claude_session("eph-2") + hook("eph-2", "SessionEnd", 10))
[row] = memory_rows()
execution_id = json.loads(row[6])["execution_id"]
result = delete_history_session(execution_id)
assert result["run_memory_deleted"] == 1
assert memory_rows() == []
try:
    delete_history_session(execution_id)
except ValueError:
    pass
else:
    raise AssertionError("second delete must report not found")
''', tmp_path)


def test_range_deletion_forgets_overlapping_memory(tmp_path):
    _run(r'''
from server.evidence_delete import delete_database_range
ingest_agent_payloads(claude_session("eph-3") + hook("eph-3", "SessionEnd", 10))
assert len(memory_rows()) == 1
outside = delete_database_range((base - timedelta(days=3)).isoformat(), (base - timedelta(days=2)).isoformat())
assert outside["run_memory_deleted"] == 0 and len(memory_rows()) == 1
inside = delete_database_range((base - timedelta(minutes=1)).isoformat(), (base + timedelta(minutes=1)).isoformat())
assert inside["run_memory_deleted"] == 1 and memory_rows() == []
''', tmp_path)


def test_memory_retention_and_switch_off(tmp_path):
    _run(r'''
ingest_agent_payloads(claude_session("eph-4") + hook("eph-4", "SessionEnd", 10))
assert len(memory_rows()) == 1
assert run_memory.prune(now=datetime.now(timezone.utc) + timedelta(days=89)) == 0
assert run_memory.prune(now=datetime.now(timezone.utc) + timedelta(days=91)) == 1
ingest_agent_payloads(claude_session("eph-5") + hook("eph-5", "SessionEnd", 10))
assert len(memory_rows()) == 1
update_run_memory(enabled=False, days=90)
assert cleanup_expired_history(startup=False)["run_memory_pruned"] == 1
assert memory_rows() == []
# Off means nothing new is kept either.
ingest_agent_payloads(claude_session("eph-6") + hook("eph-6", "SessionEnd", 10))
assert memory_rows() == [] and raw_count("eph-6") == 0
''', tmp_path)


def test_crash_recovery_purge_keeps_memory_for_unfinished_sessions(tmp_path):
    _run(r'''
ingest_agent_payloads(claude_session("crashed"))  # no SessionEnd: OpenWorkGraph "crashed"
cleanup_expired_history(startup=True)
assert raw_count("crashed") == 0
assert len(memory_rows()) == 1
''', tmp_path)


def test_human_session_memory_is_content_free(tmp_path):
    _run(r'''
def focus(i, app, title):
    return {"event_id": f"h{i}", "observed_at": (base + timedelta(minutes=i)).isoformat(), "device_id": "d",
            "session_id": "human-eph", "app": app, "window_title": f"{title} {SECRET}", "event_type": "focus_span",
            "duration_seconds": 50, "metadata": {"activity": {"foreground_seconds": 50, "engaged_seconds": 40}}}
insert_events([focus(0, "Mail", "Inbox"), focus(1, "Editor", "report.docx"), focus(2, "Mail", "Reply")])
delete_sessions("human", ["human-eph"], reason="ephemeral_session_closed")
assert raw_count("human-eph") == 0
assert len(memory_rows()) == 1
dump = json.dumps(memory_rows())
assert SECRET not in dump and "report.docx" not in dump and "human-eph" not in dump
''', tmp_path)


def test_run_memory_routes(tmp_path):
    _run(r'''
from fastapi import FastAPI
from fastapi.testclient import TestClient
import server.history_routes as hr
app = FastAPI()
for route in hr.app.router.routes:
    if getattr(route, "path", "").startswith("/v1/run-memory") or getattr(route, "path", "") == "/v1/history-policy":
        app.router.routes.append(route)
ingest_agent_payloads(claude_session("eph-7") + hook("eph-7", "SessionEnd", 10))
with TestClient(app) as c:
    body = c.get("/v1/run-memory").json()
    assert body["total"] == 1 and body["leaves_this_computer"] is False
    assert all(not k.startswith("_") for k in body["runs"][0])
    assert c.get("/v1/history-policy").json()["run_memory"] == {"enabled": True, "days": 90}
    assert c.post("/v1/run-memory/forget", json={"execution_id": "execution:nope"}).status_code == 404
    assert c.post("/v1/run-memory/forget", json={}).status_code == 400
    assert c.put("/v1/run-memory/policy", json={"enabled": True, "days": 0}).status_code == 400
    assert c.post("/v1/run-memory/forget", json={"execution_id": body["runs"][0]["execution_id"]}).json()["run_memory_deleted"] == 1
    assert c.get("/v1/run-memory").json()["total"] == 0
''', tmp_path)
