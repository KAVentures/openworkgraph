from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, tmp_path: Path, timeout: int = 120) -> str:
    env = {
        **os.environ,
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_public_agent_tool_names_are_readable_and_custom_names_remain_opaque():
    sys.path.insert(0, str(ROOT))
    from server.agent_tool_labels import readable_tool_name

    for name in ("Bash", "Read", "Edit", "Grep", "WebFetch", "apply_patch", "shell", "file_search"):
        assert readable_tool_name(name) == name
    assert readable_tool_name("mcp__github__create_pull_request") == "mcp__github__create_pull_request"

    for name in ("customTool", "Bash(rm -rf /)", "send_invoice_to_acme_4471234", "/usr/bin/thing"):
        assert readable_tool_name(name).startswith("tool:")

    out = readable_tool_name("mcp__gmail__send_email_to_anna_svensson")
    assert out.startswith("mcp__gmail__tool:") and "anna" not in out
    assert readable_tool_name("mcp__erik_lindqvist__read").startswith("tool:")


def test_subagent_execution_is_linked_to_parent(tmp_path):
    out = _run(r'''
import json
from server.db import init_db, insert_events, connect
from shared.claude_code_adapter import claude_hook_to_agent_events
from shared.agent_evidence import agent_event_to_evidence
from server.agent_execution_traces import agent_execution_traces

init_db()
base={"session_id":"S","cwd":"/x"}
seq=[
 ("SessionStart",{}),
 ("PostToolUse",{"tool_name":"Read","tool_use_id":"a"}),
 ("SubagentStart",{"agent_id":"sub1","agent_type":"Explore"}),
 ("PostToolUse",{"tool_name":"Glob","tool_use_id":"b","agent_id":"sub1","agent_type":"Explore"}),
 ("SubagentStop",{"agent_id":"sub1","agent_type":"Explore"}),
 ("SessionEnd",{"reason":"exit"}),
]
events=[]
for hook,extra in seq:
    for payload in claude_hook_to_agent_events({**base,"hook_event_name":hook,**extra}):
        events.append(agent_event_to_evidence(payload))
insert_events(events)
with connect() as conn:
    rows=[dict(r) for r in conn.execute("SELECT * FROM events")]
for row in rows:
    if isinstance(row.get("metadata_json"),str):
        row["metadata"]=json.loads(row["metadata_json"])
traces=agent_execution_traces(rows)["executions"]
child=[t for t in traces if t["agent"]["name"].endswith("/Explore")][0]
parent=[t for t in traces if t["agent"]["name"]=="Claude Code"][0]
assert child["parent_execution_id"]==parent["execution_id"], traces
assert child["execution_id"] in parent["child_execution_ids"]
assert "tool:filesystem:Read" in parent["structural_steps"], parent["structural_steps"]
print("ok")
''', tmp_path)
    assert "ok" in out


def test_manual_claude_setup_matches_structural_telemetry_privacy(tmp_path):
    out = _run(r'''
import io, json
from contextlib import redirect_stdout
from adapters.claude_code_hook import main

buf=io.StringIO()
with redirect_stdout(buf):
    assert main(["--print-settings"])==0
full=json.loads(buf.getvalue())
assert "hooks" in full and "env" in full
for key in (
 "OTEL_LOG_USER_PROMPTS", "OTEL_LOG_ASSISTANT_RESPONSES", "OTEL_LOG_TOOL_DETAILS",
 "OTEL_LOG_TOOL_CONTENT", "OTEL_LOG_RAW_API_BODIES",
):
    assert full["env"][key]=="0"
assert full["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"]=="1"

buf=io.StringIO()
with redirect_stdout(buf):
    assert main(["--print-settings","--hooks-only"])==0
hooks=json.loads(buf.getvalue())
assert "hooks" in hooks and "env" not in hooks
print("ok")
''', tmp_path)
    assert "ok" in out


def test_redaction_extension_handles_reported_edge_cases_without_new_traps(tmp_path):
    out = _run(r'''
from server import ai_context
import server.ai_context_routes  # installs extension
from server.contextual_redaction_extensions import augment

redact=ai_context.contextual_text_redactor({"never_redact":[],"always_redact":[]})
for text,keep in (
    ("Svensson, Karl - Outlook", "Outlook"),
    ("anna svensson | Teams", "Teams"),
    ("Linda Chen's Personal Meeting Room", "Personal Meeting Room"),
    ("Maria de Souza - Calendar", "Calendar"),
):
    result=redact(text)
    assert keep in result, (text,result)
    for leaked in ("Svensson","Karl","anna","svensson","Linda","Chen","Maria","Souza"):
        if leaked.casefold() in text.casefold():
            assert leaked.casefold() not in result.casefold(), (text,result,leaked)

# Owner/learned tokens can be created by the presentation pass before contextual
# redaction. The extension must still redact the other person after a slash.
mock=augment(lambda s: s.replace("Koyar", "PERSON_A1B2C3"), {"never_redact":[]})
slash=mock("Koyar / Linnea")
assert "Linnea" not in slash and "PERSON_A1B2C3" in slash, slash

for text in ("Q3 / Q4", "mark as read", "Van rental", "De Beers"):
    assert redact(text)==text, (text,redact(text))
print("ok")
''', tmp_path)
    assert "ok" in out


def test_context_pulse_compact_rows_transfer_and_repeated_agent_failure(tmp_path):
    out = _run(r'''
import json, sqlite3
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events, DB_PATH
from server.context_pulse import context_pulse
from shared.agent_evidence import agent_event_to_evidence

init_db()
now=datetime.now(timezone.utc)
rows=[]
for i in range(30):
    rows.append({
        "event_id":f"e{i}",
        "observed_at":(now-timedelta(minutes=60-i)).isoformat(),
        "session_id":"s","app":"Google Chrome","window_title":"Inbox - Gmail",
        "event_type":"browser_click","source":"browser","duration_seconds":0,
        "metadata":{"hostname":"mail.google.com","label":"Send"},
    })
# Two separate Claude runs failing at Bash.
for run in ("r1","r2"):
    for index,(operation,status) in enumerate((("run_started","running"),("tool_call","error"),("run_finished","error"))):
        rows.append(agent_event_to_evidence({
            "event_id":f"{run}-{index}","observed_at":(now+timedelta(seconds=index)).isoformat(),
            "session_id":run,"run_id":run,"trace_id":run,"agent_name":"Claude Code",
            "provider":"anthropic","framework":"claude-code","operation":operation,
            "status":status,"observation_level":"native_trace",
            "tool_name":"Bash" if operation=="tool_call" else "",
            "tool_category":"shell" if operation=="tool_call" else "none",
        }))
insert_events(rows)

# Context events are the privacy-safe structural copy/paste index. Contents are
# never stored; the transfer id only links copy and paste observations.
db=sqlite3.connect(DB_PATH)
for i in range(4):
    t=now-timedelta(minutes=20-i*3)
    for k,(surface,action) in enumerate((("Salesforce","copy"),("Google Sheets","paste"))):
        db.execute(
            "INSERT INTO context_events(event_id,observed_at,session_id,source,surface,action,metadata_json) VALUES(?,?,?,?,?,?,?)",
            (f"x{i}{k}",(t+timedelta(seconds=k*20)).isoformat(),"s","browser",surface,action,json.dumps({"clipboard_transfer_id":f"T{i}"})),
        )
db.commit(); db.close()

first=context_pulse(recent_limit=12,finding_limit=6)
assert first["recent_returned"]<=12
assert len(json.dumps(first))<8000, len(json.dumps(first))
assert first["recent_detail"]=="compact"
assert first["recent_evidence"] and "metadata" not in first["recent_evidence"][0]
kinds={finding["finding_kind"] for finding in first["findings"]}
assert "manual_transfer" in kinds, kinds
assert "agent_repeated_failure" in kinds, kinds
transfer=[f for f in first["findings"] if f["finding_kind"]=="manual_transfer"][0]
assert transfer["source_surface"]=="Salesforce" and transfer["destination_surface"]=="Google Sheets"
assert transfer["clipboard_contents_captured"] is False
failure=[f for f in first["findings"] if f["finding_kind"]=="agent_repeated_failure"][0]
assert failure["failing_step"]=="tool:shell:Bash", failure
rich=context_pulse(recent_detail="rich",recent_limit=1,finding_limit=0)
assert rich["recent_evidence"] and "metadata" in rich["recent_evidence"][0]
print("ok")
''', tmp_path)
    assert "ok" in out
