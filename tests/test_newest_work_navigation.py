"""Recent-first OWG navigation must not invert the legacy canonical trace.

Synthetic 900-event history deliberately buries an old agreement behind many
newer work events. No real user evidence or saved history is loaded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from server import mcp_trace


def _make_db(tmp_path):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE events (
            id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT,
            app TEXT, window_title TEXT, event_type TEXT, duration_seconds REAL,
            source TEXT, session_id TEXT, metadata_json TEXT
        )""")
        base = datetime(2026, 10, 8, tzinfo=timezone.utc)
        rows = [
            (i + 1, f"event-{i:04d}", (base + timedelta(seconds=30 * i)).isoformat(),
             "Oneflow" if i == 10 else "Gmail",
             "Samarbetsavtal Oneflow" if i == 10 else "Inbox noise",
             "focus_span", 30.0, "desktop", "session-1", "{}")
            for i in range(900)
        ]
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c
    return connect


def test_trace_oldest_default_and_newest_cursor_have_no_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_trace, "connect", _make_db(tmp_path))
    legacy = mcp_trace.workflow_trace(scope="all", limit=5)
    assert legacy["rows"][0]["event_id"] == "event-0000"
    assert legacy["order"] == "oldest"
    assert legacy["rows"][-1]["event_id"] == "event-0004"

    newest = mcp_trace.workflow_trace(scope="all", limit=5, order="newest")
    assert newest["rows"][0]["event_id"] == "event-0899"
    assert newest["rows"][-1]["event_id"] == "event-0895"
    assert newest["next_cursor"]
    next_page = mcp_trace.workflow_trace(scope="all", cursor=newest["next_cursor"],
                                         limit=5, order="newest")
    assert [x["event_id"] for x in next_page["rows"]] == [
        f"event-{n:04d}" for n in range(894, 889, -1)
    ]
    assert set(x["event_id"] for x in newest["rows"]).isdisjoint(
        x["event_id"] for x in next_page["rows"]
    )


def test_newest_search_finds_old_oneflow_goal_even_after_900_events(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_trace, "connect", _make_db(tmp_path))
    oldest = mcp_trace.workflow_trace(scope="all", limit=50, order="newest")
    assert all("Samarbetsavtal" not in row["window_title"] for row in oldest["rows"])
    relevant = mcp_trace.workflow_trace(scope="all", limit=50,
                                        query="Oneflow", order="newest")
    assert [x["event_id"] for x in relevant["rows"]] == ["event-0010"]
    assert relevant["query_applied"] is True


def test_mcp_goal_finds_older_agreement_and_keeps_history_guard():
    code = r'''
from mcp_server import compact
from mcp.server.mcpserver.exceptions import ToolError
calls=[]
compact.core._begin=lambda *_: None
compact.core._finish=lambda _name, data: data
def fake_get(path, params=None):
    assert path == "/v1/workflow-trace"
    calls.append(dict(params or {}))
    if params.get("scope") == "all" and denied:
        raise ToolError("saved history permission denied")
    if params.get("query") in ("Samarbetsavtal", "oneflow"):
        return {"rows":[{"event_id":"old-contract",
          "observed_at":"2026-10-09T06:00:00Z",
          "window_title":"Samarbetsavtal Oneflow", "app":"Oneflow",
          "event_type":"focus_span", "source":"desktop",
          "session_id":"s1"}], "has_more":False}
    return {"rows":[{"event_id":f"noise-{i}",
      "observed_at":"2026-10-14T09:00:00Z",
      "window_title":"Gmail inbox", "app":"Gmail",
      "event_type":"focus_span", "source":"desktop",
      "session_id":"s2"} for i in range(400)],"has_more":True}
compact.secure_runtime.secure_get=fake_get
denied=False
answer=compact.get_goal_context("Samarbetsavtal Oneflow", scope="all")
assert answer["status"] == "possible_matches", answer
assert answer["examples"][0]["anchor_event_id"] == "old-contract"
assert all(call["order"] == "newest" for call in calls)
assert len(calls) <= 3
denied=True
try:
    compact.get_goal_context("Samarbetsavtal Oneflow", scope="all")
    raise AssertionError("history denial was ignored")
except ToolError:
    pass
'''
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-c", code], cwd=root,
                         capture_output=True, text=True, timeout=35)
    assert run.returncode == 0, run.stderr
