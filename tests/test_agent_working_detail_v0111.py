from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import json
import os
from pathlib import Path
from server.db import init_db, connect
from server.agent_session_store import init_agent_session_store, list_sessions, session_ref
from server import agent_working_detail as wd
init_db(); init_agent_session_store(); wd.init_store()
'''


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run(
        [sys.executable, "-c", PRELUDE + code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_policy_defaults_and_capture_does_not_grant_ai_access(tmp_path):
    _run(r'''
p = wd.read_policy()
assert p["capture_working_detail"] is False
assert p["allow_ai_read_working_detail"] is False
p = wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": False, "working_detail_retention_days": 30})
assert p["capture_working_detail"] is True and p["allow_ai_read_working_detail"] is False
p = wd.write_policy({"capture_working_detail": False, "allow_ai_read_working_detail": True, "working_detail_retention_days": 30})
assert p["capture_working_detail"] is False and p["allow_ai_read_working_detail"] is False
''', tmp_path)


def test_workspace_paths_are_relative_redacted_and_cannot_escape(tmp_path):
    _run(r'''
root = Path(os.environ["HOME"]) / "project"
(root / "server").mkdir(parents=True)
(root / "server" / "auth.py").write_text("x")
outside = Path(os.environ["HOME"]) / "outside.py"
outside.write_text("secret")
inside = wd._safe_relative_path(root / "server" / "auth.py", root)
assert inside == "server/auth.py", inside
assert wd._safe_relative_path(outside, root) == ""
assert wd._safe_relative_path("../outside.py", root) == ""
link = root / "server" / "escape.py"
try:
    link.symlink_to(outside)
except (OSError, NotImplementedError):
    pass
else:
    assert wd._safe_relative_path(link, root) == ""
''', tmp_path)


def test_hook_extracts_useful_facts_without_raw_output_or_shell_secrets(tmp_path):
    _run(r'''
root = Path(os.environ["HOME"]) / "project"
(root / "tests").mkdir(parents=True)
(root / "server").mkdir(parents=True)
(root / "tests" / "test_auth.py").write_text("def test_refresh_expiry(): pass\n")
(root / "server" / "auth.py").write_text("x=1\n")
wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": False, "working_detail_retention_days": 30})
payload = {
    "session_id": "native-secret-session",
    "prompt_id": "p1",
    "hook_event_name": "PostToolUse",
    "tool_use_id": "tool-1",
    "tool_name": "Bash",
    "cwd": str(root),
    "tool_input": {"command": "API_KEY=sk-super-secret pytest tests/test_auth.py -q --token sk-super-secret"},
    "tool_response": {
        "stdout": "FAILED tests/test_auth.py::test_refresh_expiry - AssertionError: expected expiry 3600\n1 failed, 11 passed in 1.00s\nRAW_TAIL_MUST_NOT_BE_STORED",
        "stderr": "",
        "exit_code": 1,
    },
}
assert wd.capture_claude_hook(payload) is True
with connect() as c:
    rows = c.execute("SELECT facts_json, provenance_json FROM agent_working_detail").fetchall()
assert len(rows) == 1
facts = json.loads(rows[0][0]); prov = json.loads(rows[0][1])
assert facts["command"] == {"program": "pytest", "targets": ["tests/test_auth.py"]}, facts
assert facts["tests"]["status"] == "failing"
assert facts["tests"]["passed"] == 11 and facts["tests"]["failed"] == 1
assert facts["tests"]["failing_tests"] == ["tests/test_auth.py::test_refresh_expiry"]
assert facts["tests"]["error"]["type"] == "AssertionError"
assert facts["tests"]["error"]["excerpt"]["untrusted_observed_text"] is True
assert facts["exit_code"] == 1
assert prov["raw_output_stored"] is False and prov["parsed_output"] is True
dump = json.dumps([tuple(row) for row in rows])
assert "sk-super-secret" not in dump
assert "RAW_TAIL_MUST_NOT_BE_STORED" not in dump
assert str(root) not in dump
assert "API_KEY=" not in dump
''', tmp_path)


def test_unknown_test_result_stays_unknown_not_zero_pass(tmp_path):
    _run(r'''
root = Path(os.environ["HOME"]) / "project"; (root / "tests").mkdir(parents=True)
wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": False})
payload = {
    "session_id": "unknown-session", "prompt_id": "p", "hook_event_name": "PostToolUse",
    "tool_use_id": "tool-u", "tool_name": "Bash", "cwd": str(root),
    "tool_input": {"command": "pytest tests/test_auth.py -q"},
    "tool_response": {"stdout": "test process started but result summary unavailable"},
}
wd.capture_claude_hook(payload)
with connect() as c:
    raw = c.execute("SELECT facts_json FROM agent_working_detail").fetchone()[0]
facts = json.loads(raw)
assert facts["tests"] == {"status": "unknown"}, facts
assert "passed" not in facts["tests"] and "failed" not in facts["tests"]
''', tmp_path)


def test_file_tool_keeps_only_inside_workspace_relative_path(tmp_path):
    _run(r'''
root = Path(os.environ["HOME"]) / "project"; (root / "server").mkdir(parents=True)
outside = Path(os.environ["HOME"]) / "patient-secret.txt"; outside.write_text("x")
wd.write_policy({"capture_working_detail": True})
base = {"session_id": "files", "prompt_id": "p", "hook_event_name": "PostToolUse", "cwd": str(root)}
assert wd.capture_claude_hook({**base, "tool_use_id": "a", "tool_name": "Edit", "tool_input": {"file_path": str(root / "server" / "auth.py")}, "tool_response": {}})
# An outside-workspace path yields no useful facts and therefore no second row.
assert wd.capture_claude_hook({**base, "tool_use_id": "b", "tool_name": "Read", "tool_input": {"file_path": str(outside)}, "tool_response": {}}) is False
with connect() as c:
    rows = [r[0] for r in c.execute("SELECT facts_json FROM agent_working_detail").fetchall()]
assert len(rows) == 1
assert json.loads(rows[0])["files"]["paths"] == ["server/auth.py"]
assert str(root) not in rows[0] and "patient-secret.txt" not in rows[0]
''', tmp_path)


def test_repository_snapshot_is_bounded_and_workspace_relative(tmp_path):
    _run(r'''
root = Path(os.environ["HOME"]) / "repo"; root.mkdir(parents=True)
env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
def git(*args):
    return __import__("subprocess").run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env)
git("init", "-q"); (root / "app.py").write_text("print(1)\n"); git("add", "app.py"); git("commit", "-q", "-m", "init")
(root / "app.py").write_text("print(2)\n"); (root / "new.py").write_text("x\n")
state = wd.repository_state(root)
assert state["dirty"] is True and state["untracked_count"] == 1
assert "app.py" in state["changed_files"] and "new.py" in state["changed_files"]
assert len(state["head"]) == 12
assert str(root) not in json.dumps(state)
''', tmp_path)


def test_local_storage_and_ai_read_permissions_are_independent(tmp_path):
    _run(r'''
from server.agent_session_routes import _grounded_handoff
root = Path(os.environ["HOME"]) / "project"; root.mkdir(parents=True)
wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": False})
sref = session_ref("claude_code", "native-permission")
assert wd.upsert_detail(ref=wd.detail_ref("claude_code", "native-permission", "x"), session=sref, source="claude_code",
    workspace="", observed_at=wd._now(), kind="tool", facts={"tests": {"status": "unknown"}},
    provenance={"basis": "test", "raw_output_stored": False})
[sess] = [s for s in list_sessions(limit=50) if s["session_ref"] == sref]
human = _grounded_handoff(sess, message_limit=10, ai_read=False)
ai = _grounded_handoff(sess, message_limit=10, ai_read=True)
assert len(human["working_detail"]) == 1
assert ai["working_detail"] == [] and ai["working_detail_ai_access"] is False
wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": True})
ai2 = _grounded_handoff(sess, message_limit=10, ai_read=True)
assert len(ai2["working_detail"]) == 1 and ai2["working_detail_ai_access"] is True
''', tmp_path)


def test_enable_has_no_backfill_but_explicit_import_is_idempotent(tmp_path):
    _run(r'''
from datetime import datetime, timezone
home = Path(os.environ["HOME"]); root = home / "project"; (root / "tests").mkdir(parents=True)
sessions = home / ".claude" / "projects" / "demo"; sessions.mkdir(parents=True)
path = sessions / "session.jsonl"
now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
records = [
 {"type":"assistant","timestamp":now,"sessionId":"old-session","cwd":str(root),"message":{"content":[
   {"type":"thinking","thinking":"HIDDEN_REASONING_MUST_NEVER_APPEAR"},
   {"type":"tool_use","id":"t1","name":"Bash","input":{"command":"pytest tests/test_auth.py -q"}}
 ]}},
 {"type":"user","timestamp":now,"sessionId":"old-session","cwd":str(root),"message":{"content":[
   {"type":"tool_result","tool_use_id":"t1","content":"FAILED tests/test_auth.py::test_refresh_expiry - AssertionError: expected 3600\n1 failed, 11 passed in 1.0s\nRAW_IMPORT_OUTPUT"}
 ]}},
]
path.write_text("".join(json.dumps(x)+"\n" for x in records), encoding="utf-8")
# Enabling today starts at EOF: old session data is not silently imported.
wd.write_policy({"capture_working_detail": True, "allow_ai_read_working_detail": False})
assert wd.scan_once()["details_written"] == 0
with connect() as c: assert c.execute("SELECT COUNT(*) FROM agent_working_detail").fetchone()[0] == 0
# Explicit import is different and is safe to repeat.
r1 = wd.import_recent(days=7, include_structural=False, include_working_detail=True, include_visible_messages=False)
r2 = wd.import_recent(days=7, include_structural=False, include_working_detail=True, include_visible_messages=False)
assert r1["working_details"] >= 1 and r2["working_details"] >= 1
with connect() as c:
    rows = c.execute("SELECT facts_json, provenance_json FROM agent_working_detail").fetchall()
assert len(rows) == 1, rows
facts = json.loads(rows[0][0])
assert facts["command"] == {"program":"pytest","targets":["tests/test_auth.py"]}
assert facts["tests"]["status"] == "failing" and facts["tests"]["failed"] == 1
assert facts["tests"]["failing_tests"] == ["tests/test_auth.py::test_refresh_expiry"]
dump = json.dumps([tuple(row) for row in rows])
assert "HIDDEN_REASONING_MUST_NEVER_APPEAR" not in dump
assert "RAW_IMPORT_OUTPUT" not in dump
assert str(root) not in dump
assert r1["raw_tool_output_stored"] is False and r1["hidden_reasoning_imported"] is False
# Working-detail import creates only an opaque session row, enough for handoff selection.
assert any(s["session_ref"] == session_ref("claude_code", "old-session") for s in list_sessions(limit=50))
''', tmp_path)


def test_retention_and_delete_are_separate_from_canonical_evidence(tmp_path):
    _run(r'''
from datetime import datetime, timedelta, timezone
wd.write_policy({"capture_working_detail": True, "working_detail_retention_days": 7})
sref = session_ref("codex", "retention")
old = (datetime.now(timezone.utc)-timedelta(days=8)).isoformat().replace("+00:00","Z")
new = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
wd.upsert_detail(ref=wd.detail_ref("codex","retention","old"), session=sref, source="codex", workspace="", observed_at=old, kind="tool", facts={"tests":{"status":"unknown"}}, provenance={"basis":"test"})
wd.upsert_detail(ref=wd.detail_ref("codex","retention","new"), session=sref, source="codex", workspace="", observed_at=new, kind="tool", facts={"tests":{"status":"passing","passed":1,"failed":0}}, provenance={"basis":"test"})
assert wd.cleanup() == 1
with connect() as c: assert c.execute("SELECT COUNT(*) FROM agent_working_detail").fetchone()[0] == 1
assert wd.delete_all() == 1
with connect() as c: assert c.execute("SELECT COUNT(*) FROM agent_working_detail").fetchone()[0] == 0
''', tmp_path)
