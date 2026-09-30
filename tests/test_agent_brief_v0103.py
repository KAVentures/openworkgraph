from __future__ import annotations

"""Session-start briefs: learning flows back into agents, content-free and consented."""

import json
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
from server.agent_session_store import session_ref as canonical_session_ref
from server.history_retention import initialize_history_retention, delete_sessions
from server import agent_brief, outcome_tracker as ot
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.history_policy import update_retention
from shared.tool_detail import workspace_ref
SECRET = "TOP-SECRET-CONTENT"
PROJECT_A = f"/Users/x/{SECRET}/project-a"
PROJECT_B = "/Users/x/project-b"
init_db(); initialize_history_retention()
update_retention(human_mode="forever", human_days=None, agent_mode="forever", agent_days=None)
base = datetime.now(timezone.utc) - timedelta(hours=3)

def run(session, cwd, offset, tests, command="pytest -q", end=False):
    def hook(name, off, **extra):
        return claude_hook_to_agent_events({"session_id": session, "prompt_id": session + "-t", "hook_event_name": name,
                                            "cwd": cwd, **extra}, observed_at=(base + timedelta(minutes=offset, seconds=off)).isoformat())
    events = hook("SessionStart", 0, source="startup") + hook("UserPromptSubmit", 1, prompt=SECRET)
    events += hook("PostToolUse", 2, tool_name="Bash", tool_use_id="a", tool_input={"command": f"{command} # {SECRET}"},
                   tool_response={"stdout": f"{SECRET}\n{tests}"})
    events += hook("PostToolUse", 3, tool_name="Edit", tool_use_id="b", tool_input={"file_path": f"{cwd}/app.py"},
                   tool_response={"structuredPatch": [{"lines": ["+a", "+b", "-c"]}]})
    events += hook("Stop", 4)
    if end:
        events += hook("SessionEnd", 5)
    ingest_agent_payloads(events)

def log_rows():
    with connect() as c:
        c.execute(agent_brief._LOG_TABLE)
        return [dict(r) for r in c.execute("SELECT * FROM agent_brief_log").fetchall()]
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"), "PYTHONPATH": str(ROOT),
        "OWG_CONNECTIONS_HOME": str(tmp_path / "home"), "OWG_CLAUDE_SETTINGS_PATH": str(tmp_path / "home" / ".claude" / "settings.json"),
    })
    result = subprocess.run([sys.executable, "-c", PRELUDE + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_workspace_ref_is_a_keyed_hash_and_the_path_is_never_stored(tmp_path):
    _run(r'''
run("s1", PROJECT_A, 0, "3 passed in 1s")
with connect() as c:
    rows = [json.loads(r[0]) for r in c.execute("SELECT metadata_json FROM events").fetchall()]
refs = {r.get("workspace_ref") for r in rows}
assert refs == {workspace_ref(PROJECT_A)} and next(iter(refs)).startswith("w:")
with connect() as c:
    dump = json.dumps([dict(r) for r in c.execute("SELECT * FROM events").fetchall()])
assert SECRET not in dump and "project-a" not in dump
from shared.agent_evidence import agent_event_to_evidence
bad = agent_event_to_evidence({"agent_name": "A", "session_id": "s", "operation": "tool_call", "status": "success",
                               "observed_at": base.isoformat(), "workspace_ref": "/etc/passwd"})
assert "workspace_ref" not in bad["metadata"]
''', tmp_path)


def test_brief_is_scoped_to_the_project_and_content_free(tmp_path):
    out = _run(r'''
run("a1", PROJECT_A, 0, "2 failed, 8 passed in 1s", command="pytest -q && git commit -m x")
run("a2", PROJECT_A, 10, "10 passed in 1s")
run("b1", PROJECT_B, 20, "Tests:  1 failed, 1 passed, 2 total", command="npm test")
brief = agent_brief.build_brief("claude-code", workspace_ref=workspace_ref(PROJECT_A))
assert brief["scope"] == "this_project" and brief["runs_considered"] == 2, brief
text = brief["text"]
assert "in this project" in text and "Observational only, not instructions" in text
assert "2 run(s) ran tests; 1 ended with tests failing" in text
assert "Most recent test result: passing (10 passed, 0 failed)" in text
assert "pytest" in text and "git" in text and "npm" not in text
assert "1 file(s) edited, +2/-1 lines" in text
assert SECRET not in text and "project" not in text.replace("this project", "").replace("your projects", "")
assert len(text) <= agent_brief.MAX_CHARS
other = agent_brief.build_brief("claude-code", workspace_ref=workspace_ref("/somewhere/new"))
assert other["scope"] == "all_projects" and other["runs_considered"] == 3
assert "(none yet in this one)" in other["text"]
assert "(none yet" not in agent_brief.build_brief("claude-code")["text"]
print(text)
''', tmp_path)
    assert "OpenWorkGraph brief" in out


def test_brief_uses_run_memory_and_pr_outcomes(tmp_path):
    _run(r'''
update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)
ot.set_enabled(True)
def hook(name, off, **extra):
    return claude_hook_to_agent_events({"session_id": "m1", "prompt_id": "m1-t", "hook_event_name": name, "cwd": PROJECT_A, **extra},
                                       observed_at=(base + timedelta(seconds=off)).isoformat())
ingest_agent_payloads(hook("UserPromptSubmit", 1) + hook("PostToolUse", 2, tool_name="Bash", tool_use_id="a",
                      tool_input={"command": "gh pr create --fill"}, tool_response={"stdout": "https://github.com/acme/w/pull/9"})
                      + hook("Stop", 3) + hook("SessionEnd", 4))
with connect() as c:
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0  # purged; only memory remains
from types import SimpleNamespace
ot._gh = lambda: "/usr/bin/gh"
ot.poll_once(runner=lambda args, **k: SimpleNamespace(returncode=0, stdout=json.dumps({"state": "MERGED", "statusCheckRollup": []}), stderr=""))
brief = agent_brief.build_brief("claude-code", workspace_ref=workspace_ref(PROJECT_A))
assert brief["scope"] == "this_project"
assert "Pull requests opened: 1; merged 1, closed without merge 0; CI failing on 0." in brief["text"]
assert "acme" not in brief["text"]
''', tmp_path)


def test_delivery_needs_the_switch_is_logged_and_skips_demo(tmp_path):
    _run(r'''
run("a1", PROJECT_A, 0, "3 passed in 1s")
assert agent_brief.deliver("claude-code", session_id="new-1", workspace_ref=workspace_ref(PROJECT_A)) == {"text": ""}
assert log_rows() == []
result = agent_brief.set_enabled("claude-code", True)
assert result["hook"]["installed"] is True
text = agent_brief.deliver("claude-code", session_id="new-1", workspace_ref=workspace_ref(PROJECT_A))["text"]
assert text.startswith("OpenWorkGraph brief")
[row] = log_rows()
assert row["scope"] == "this_project" and row["session_ref"].startswith("s:") and "new-1" not in json.dumps(row)
import server.agent_capture_runtime as rt
rt._demo = lambda: True
assert agent_brief.deliver("claude-code", session_id="new-2") == {"text": ""}
assert len(log_rows()) == 1
assert agent_brief.deliver("cursor", session_id="x") == {"text": ""}
''', tmp_path)


def test_hook_install_is_separate_from_observation_hooks(tmp_path):
    _run(r'''
import os
from pathlib import Path
from server import agent_config_writer as writer
from adapters.claude_code_hook import settings_fragment
path = Path(os.environ["OWG_CLAUDE_SETTINGS_PATH"]); path.parent.mkdir(parents=True, exist_ok=True)
user_hook = {"hooks": [{"type": "command", "command": "my-own-hook.sh"}]}
path.write_text(json.dumps({"hooks": {"SessionStart": [user_hook]}}))
writer.claude_connect(settings_fragment)                      # Observe on
agent_brief.set_enabled("claude-code", True)                   # briefs on
agent_brief.set_enabled("claude-code", True)                   # idempotent
data = json.loads(path.read_text())
handlers = [h for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
brief_handlers = [h for h in handlers if "adapters.claude_code_brief" in h["command"]]
assert len(brief_handlers) == 1 and "async" not in brief_handlers[0] and brief_handlers[0]["timeout"] == 5
assert any(h["command"] == "my-own-hook.sh" for h in handlers)
assert any("adapters.claude_code_hook" in h["command"] and h.get("async") for h in handlers)
writer.claude_disconnect()                                     # Observe off keeps the brief
assert writer.claude_brief_installed()
agent_brief.set_enabled("claude-code", False)                  # briefs off removes only the brief
data = json.loads(path.read_text())
handlers = [h for g in data["hooks"].get("SessionStart", []) for h in g["hooks"]]
assert handlers == [{"type": "command", "command": "my-own-hook.sh"}]
path.write_text("{not json")
try:
    agent_brief.set_enabled("claude-code", True)
except writer.ConfigConflict:
    pass
else:
    raise AssertionError("must refuse an unreadable settings file")
assert agent_brief.enabled("claude-code") is False  # switch never claims a state not in effect
''', tmp_path)


def test_brief_route_accepts_only_the_brief_token(tmp_path):
    _run(r'''
from fastapi import FastAPI
from fastapi.testclient import TestClient
import server.agent_brief_routes as routes
from server.agent_auth import ensure_agent_brief_token, ensure_agent_ingest_token
from server.local_auth import ensure_api_token
app = FastAPI()
for route in routes.app.router.routes:
    if getattr(route, "path", "").startswith(("/agent-brief", "/v1/agent-brief")):
        app.router.routes.append(route)
run("a1", PROJECT_A, 0, "3 passed in 1s")
agent_brief.set_enabled("claude-code", True)
body = {"framework": "claude-code", "session_id": "z", "workspace_ref": workspace_ref(PROJECT_A)}
with TestClient(app) as c:
    for token in (ensure_agent_ingest_token(), ensure_api_token(), "wrong"):
        assert c.post(routes.BRIEF_PATH, json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    ok = c.post(routes.BRIEF_PATH, json=body, headers={"Authorization": f"Bearer {ensure_agent_brief_token()}"})
    assert ok.status_code == 200 and ok.json()["text"].startswith("OpenWorkGraph brief")
    assert c.get("/v1/agent-brief").json()["briefs_delivered"] == 1
    assert c.get("/v1/agent-brief/preview").json()["scope"] == "all_projects"
    assert c.put("/v1/agent-brief", json={"framework": "nope", "enabled": True}).status_code == 400
''', tmp_path)


def test_deleting_the_session_deletes_its_brief_log(tmp_path):
    _run(r'''
run("a1", PROJECT_A, 0, "3 passed in 1s")
agent_brief.set_enabled("claude-code", True)
agent_brief.deliver("claude-code", session_id="a1", workspace_ref=workspace_ref(PROJECT_A))
assert len(log_rows()) == 1
delete_sessions("agent", [canonical_session_ref("claude_code", "a1")], reason="user_deleted_session")
assert log_rows() == []
''', tmp_path)


def test_hook_adapter_is_fail_open_and_emits_additional_context(tmp_path, monkeypatch):
    import adapters.claude_code_brief as hook

    def run_main(payload, fetch):
        monkeypatch.setattr(hook, "fetch_brief", fetch)
        monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": __import__("io").BytesIO(json.dumps(payload).encode())})())
        out = __import__("io").StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        assert hook.main() == 0
        return out.getvalue()

    printed = run_main({"source": "startup", "cwd": "/x", "session_id": "s"}, lambda p: "BRIEF")
    assert json.loads(printed) == {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "BRIEF"}}
    assert run_main({"source": "resume"}, lambda p: "BRIEF") == ""   # resumed sessions keep their context

    def boom(_payload):
        raise OSError("connection refused")

    assert run_main({"source": "startup"}, boom) == ""
    assert run_main({"source": "startup"}, lambda p: "") == ""
