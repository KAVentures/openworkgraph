"""Performance and correctness for bounded local MCP evidence reads."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from server import context_pulse as pulse_module
from server import procedural_memory as memory


def _sqlite_factory(db: Path):
    def connect():
        connection = sqlite3.connect(db)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def test_zero_findings_skip_expensive_historical_scan_and_preserve_baseline(tmp_path, monkeypatch):
    db = tmp_path / "pulse.db"
    with _sqlite_factory(db)() as con:
        con.executescript("""
            CREATE TABLE events (id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT);
            CREATE TABLE context_events (id INTEGER PRIMARY KEY);
        """)
    monkeypatch.setattr(pulse_module, "connect", _sqlite_factory(db))

    def forbidden(**_kwargs):
        raise AssertionError("zero requested findings must never start a historical scan")
    monkeypatch.setattr(pulse_module, "_findings", forbidden)
    result = pulse_module.context_pulse(finding_limit=0, recent_limit=4)
    assert result["recent_evidence"] == []
    assert result["findings"] == []
    assert result["findings_returned"] == 0
    cursor = pulse_module._decode_cursor(result["next_cursor"])
    assert cursor["baseline_pending"] is True


def test_agent_only_evidence_query_filters_unrelated_desktop_and_browser_rows(tmp_path, monkeypatch):
    db = tmp_path / "events.db"
    con = _sqlite_factory(db)()
    with con as c:
        c.executescript("""
          CREATE TABLE events (
            id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT,
            source TEXT, event_type TEXT, app TEXT, window_title TEXT,
            metadata_json TEXT, device_id TEXT, session_id TEXT
          );
        """)
        c.executemany(
            "INSERT INTO events(id,event_id,observed_at,source,event_type,app,window_title,metadata_json,device_id,session_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
              (1, "desktop", "2026-10-14T08:00:00Z", "desktop", "focus_span", "Excel", "Budget", "{}", "d", "s"),
              (2, "native", "2026-10-14T08:00:01Z", "agent", "agent_run_started", "Claude", "", "{}", "d", "s"),
              (3, "browser", "2026-10-14T08:00:02Z", "browser_extension", "browser_click", "Chrome", "Chat", "{}", "d", "s"),
              (4, "legacy", "2026-10-14T08:00:03Z", "desktop", "agent_run_finished", "Claude", "", "{}", "d", "s"),
            ],
        )
    monkeypatch.setattr(memory, "rows", lambda sql, params=(): [
        dict(row) for row in _sqlite_factory(db)().execute(sql, params).fetchall()
    ])
    selected = memory.load_recent_evidence(limit=100, agent_only=True)
    assert [item["event_id"] for item in selected] == ["native", "legacy"]
    full = memory.load_recent_evidence(limit=100)
    assert len(full) == 4


def test_missing_agent_rows_do_not_trigger_broad_scan(tmp_path, monkeypatch):
    sql_seen = []
    def capture(sql, params=()):
        sql_seen.append(sql)
        return []
    monkeypatch.setattr(memory, "rows", capture)
    assert memory.load_recent_evidence(limit=25_000, agent_only=True) == []
    assert "source = 'agent'" in sql_seen[0]
    assert "LIMIT ?" in sql_seen[0]
