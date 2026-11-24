"""The owner's activity glossary: notes per activity, coverage, CSV, deletion with the log, agent view."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_activity_glossary as gl
from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """8 cases Open > Approve > Close, 2 of them also Escalate."""
    rows = ["case,activity,time"]
    for i in range(8):
        steps = ["Open", "Approve"] + (["Escalate"] if i < 2 else []) + ["Close"]
        rows += [f"c{i},{a},2024-01-{1 + i:02d}T{8 + j:02d}:00:00Z" for j, a in enumerate(steps)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="g", source_format="csv", mapping=MAPPING)["log_id"]


def test_import_preview_detects_changes_and_refuses_stale_apply():
    log = _log()
    gl.set_note(log, {"activity": "Approve", "description": "Original"})
    text = "activity,description\nApprove,Updated\nClose,New\nGhost,Missing\n"
    review = gl.import_csv(log, text, preview=True)
    assert [(row["activity"], row["action"]) for row in review["changes"]] == [
        ("Approve", "update"), ("Close", "add")]
    assert review["skipped_not_in_log"] == ["Ghost"]
    gl.set_note(log, {"activity": "Approve", "description": "Edited meanwhile"})
    with pytest.raises(EventLogWorkspaceError, match="Preview the import again"):
        gl.import_csv(log, text, expected_fingerprint=review["fingerprint"])
    assert next(row for row in gl.glossary(log)["activities"] if row["activity"] == "Approve")["description"] == "Edited meanwhile"
    review = gl.import_csv(log, text, preview=True)
    applied = gl.import_csv(log, text, expected_fingerprint=review["fingerprint"])
    assert applied["imported"] == 2


def test_glossary_csv_retains_literal_activity_spaces_and_refuses_ambiguous_guards():
    rows = ['case,activity,time', 'a, Check ,2024-01-01T00:00:00Z',
            'b,Check,2024-01-01T00:00:01Z', 'c,=Total,2024-01-01T00:00:02Z']
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="literal",
                               source_format="csv", mapping=MAPPING)["log_id"]
    gl.set_note(log, {"activity": " Check ", "description": "spaced"})
    gl.set_note(log, {"activity": "Check", "description": "plain"})
    gl.set_note(log, {"activity": "=Total", "description": "formula"})
    exported = gl.glossary_csv(log)
    gl.delete_note(log, " Check ")
    gl.delete_note(log, "Check")
    gl.delete_note(log, "=Total")
    gl.import_csv(log, exported)
    found = {row["activity"]: row["description"] for row in gl.glossary(log)["activities"]}
    assert found == {" Check ": "spaced", "Check": "plain", "=Total": "formula"}


def test_notes_coverage_and_clearing():
    log = _log()
    empty = gl.glossary(log)
    assert [a["activity"] for a in empty["activities"]] == ["Approve", "Close", "Open", "Escalate"]
    assert empty["coverage"] == {"activities_in_log": 4, "described": 0, "events_described_share": 0.0,
                                 "notes_not_in_log": 0}
    r = gl.set_note(log, {"activity": "Approve", "description": "Manager approves the request",
                          "owner_team": "Finance", "system": "SAP", "automation": "candidate"})
    row = {a["activity"]: a for a in r["activities"]}["Approve"]
    assert row["described"] and row["owner_team"] == "Finance" and row["automation"] == "candidate" and row["updated_at"]
    assert r["coverage"]["described"] == 1 and r["coverage"]["events_described_share"] == round(8 / 26, 4)
    assert r["automation"]["candidate"] == 1
    gl.set_note(log, {"activity": "Approve", "description": "  Approves  ", "automation": None})
    again = {a["activity"]: a for a in gl.glossary(log)["activities"]}["Approve"]
    assert again["description"] == "Approves" and again["owner_team"] == "" and again["automation"] is None
    cleared = gl.set_note(log, {"activity": "Approve"})
    assert cleared["removed"] and cleared["coverage"]["described"] == 0


def test_validation_unknown_activities_and_delete():
    log = _log()
    for bad in ({"activity": "Nope", "description": "x"}, {"activity": "Open", "automation": "robot"},
                {"activity": "Open", "extra": 1}, {"description": "x"}, {"activity": "Open", "system": "a\x01b"},
                {"activity": "Open", "description": "x" * 2001}):
        with pytest.raises(EventLogWorkspaceError):
            gl.set_note(log, bad)
    gl.set_note(log, {"activity": "Open", "description": "Request arrives"})
    assert gl.delete_note(log, "Open")["coverage"]["described"] == 0
    with pytest.raises(EventLogWorkspaceError, match="no glossary entry"):
        gl.delete_note(log, "Open")


def test_csv_is_spreadsheet_safe():
    log = _log()
    gl.set_note(log, {"activity": "Close", "description": "=HYPERLINK(\"x\")", "system": "@sys"})
    rows = list(csv.reader(io.StringIO(gl.glossary_csv(log))))
    assert rows[0] == ["activity", "in_log", "events", "description", "owner_team", "system", "automation"]
    close = [r for r in rows if r[0] == "Close"][0]
    assert close[3].startswith("'=") and close[5] == "'@sys" and close[1] == "yes"


def test_csv_guards_formulas_after_spaces_breaks_and_bom():
    rows = ["case,activity,time"] + [f'c{i},"{a}",2024-01-0{1 + i}T00:00:00Z' for i, a in enumerate(["  =HYPERLINK(1)", "\ufeff+SUM(1)", "Plain"])]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv", mapping=MAPPING)["log_id"]
    gl.set_note(log, {"activity": "Plain", "description": "-1 day", "owner_team": " @team", "system": "SAP"})
    out = {r[0]: r for r in csv.reader(io.StringIO(gl.glossary_csv(log)))}
    assert "'  =HYPERLINK(1)" in out and "'\ufeff+SUM(1)" in out
    plain = out["Plain"]
    assert plain[3] == "'-1 day" and plain[5] == "SAP"
    assert gl._cell("\n=1") == "'\n=1" and gl._cell("\t1") == "'\t1" and gl._cell("a=1") == "a=1"
    imported = gl.import_csv(log, gl.glossary_csv(log))
    row = {a["activity"]: a for a in imported["activities"]}
    assert row["Plain"]["description"] == "-1 day" and row["Plain"]["owner_team"] == "@team"


def test_deleting_the_log_deletes_the_glossary():
    log = _log()
    gl.set_note(log, {"activity": "Open", "description": "Request arrives"})
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM activity_notes WHERE log_id = ?", [log]).fetchone()[0] == 0
        finally:
            conn.close()


def test_agent_view_lists_described_common_activities():
    log = _log()
    gl.set_note(log, {"activity": "Approve", "description": "Manager approves", "automation": "manual"})
    gl.set_note(log, {"activity": "Escalate", "description": "Rare escalation"})
    with pytest.raises(sharing.NotShared):
        sharing.activity_glossary(log)
    sharing.set_share(log, "aggregates")
    r = sharing.activity_glossary(log)
    assert [a["activity"] for a in r["activities"]] == ["Approve"]
    assert r["activities"][0] == {"activity": "Approve", "events": 8, "cases": 8, "description": "Manager approves",
                                  "owner_team": "", "system": "", "automation": "manual"}
    assert r["withheld"] == {"activities_under_min_cases": 1, "min_cases": 5}


def test_agent_view_counts_distinct_cases_not_events():
    rows = ["case,activity,time"] + [f"c{i},Open,2024-01-0{1 + i}T08:00:00Z" for i in range(6)]
    rows += [f"c0,Retry,2024-01-01T{9 + k:02d}:00:00Z" for k in range(7)]     # 7 events, one case
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="r", source_format="csv", mapping=MAPPING)["log_id"]
    gl.set_note(log, {"activity": "Retry", "description": "Retried by one clerk"})
    gl.set_note(log, {"activity": "Open", "description": "Request arrives"})
    sharing.set_share(log, "aggregates")
    r = sharing.activity_glossary(log)
    assert [a["activity"] for a in r["activities"]] == ["Open"] and "Retry" not in repr(r["activities"])


def test_owner_and_agent_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_agent_sharing as sharing
doc = "case,activity,time\n" + "".join(f"c{i},A,2024-01-0{i+1}T00:00:00Z\nc{i},B,2024-01-0{i+1}T01:00:00Z\n" for i in range(6))
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    log=made.json()['log_id']; url='/v1/event-logs/'+log+'/activity-glossary'
    assert client.get(url).status_code in (401,403) and client.get(url,headers=bearer).status_code in (401,403)
    assert client.post(url,json={"activity":"A","description":"x"},headers=bearer).status_code in (401,403)
    r=client.post(url,json={"activity":"A","description":"Start","automation":"automated"},headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['coverage']['described']==1
    assert client.post(url,json={"activity":"Z","description":"x"},headers=owner).status_code==422
    c=client.get(url+'.csv',headers=owner)
    assert c.status_code==200 and c.headers['content-type'].startswith('text/csv') and 'Start' in c.text
    assert client.post(url+'/delete',json={"activity":"A","x":1},headers=owner).status_code==422
    agent='/v1/process-intelligence/'+log+'/activity-glossary'
    assert client.get(agent,headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    a=client.get(agent,headers=bearer)
    assert a.status_code==200 and a.json()['activities'][0]['description']=='Start', a.text
    assert client.post(url+'/delete',json={"activity":"A"},headers=owner).json()['removed'] is True
    imp=client.post(url+'/import',content='activity,description\nB,Finish\n'.encode(),headers={**owner,'content-type':'text/csv'})
    assert imp.status_code==200 and imp.json()['imported']==1, imp.text
    assert client.post(url+'/import',content=b'\xff\xfe',headers=owner).status_code==422
    assert client.post(url+'/import',content=b'activity\nB\n',headers=bearer).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_csv_import_round_trips_and_skips_unknown():
    log = _log()
    gl.set_note(log, {"activity": "Close", "description": "=Done", "system": "@sys", "automation": "automated"})
    text = gl.glossary_csv(log)
    gl.delete_note(log, "Close")
    r = gl.import_csv(log, "﻿" + text)
    close = {a["activity"]: a for a in r["activities"]}["Close"]
    assert (close["description"], close["system"], close["automation"]) == ("=Done", "@sys", "automated")
    assert r["imported"] == 4 and r["coverage"]["described"] == 1   # empty rows clear, not create, entries
    r = gl.import_csv(log, "Activity,Description,Automation\nOpen,Request arrives,Automation candidate\nGhost,x,\n")
    assert r["imported"] == 1 and r["skipped_not_in_log"] == ["Ghost"] and r["automation"]["candidate"] == 1
    for bad, match in (("description\nx\n", "activity column"), ("activity,automation\nOpen,robot\n", "line 2"),
                       ("", "CSV text"), ("activity,system\nOpen,a\x01b\n", "line 2")):
        with pytest.raises(EventLogWorkspaceError, match=match):
            gl.import_csv(log, bad)


def test_agent_view_finds_activities_beyond_the_500_most_frequent():
    rows = ["case,activity,time"]
    for k in range(500):
        rows += [f"c{j},Label {k:03d},2024-01-0{1 + j}T08:{k % 60:02d}:00Z" for j in range(7)]
    rows += [f"c{j},Late step,2024-01-0{1 + j}T09:00:00Z" for j in range(5)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv", mapping=MAPPING)["log_id"]
    assert "Late step" not in {a["activity"] for a in workspace.activities(log)["activities"]}
    gl.set_note(log, {"activity": "Late step", "description": "Rarely needed, still in 5 cases"})
    sharing.set_share(log, "aggregates")
    listed = sharing.activity_glossary(log)["activities"]
    assert [(a["activity"], a["cases"]) for a in listed] == [("Late step", 5)]
