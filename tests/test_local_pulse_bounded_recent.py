"""Agent Pulse is bounded and never returns the oldest slice as recent work."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import subprocess
import sys
from pathlib import Path

from server import context_pulse


def test_source_events_use_newest_window_in_chronological_order(tmp_path, monkeypatch):
    filename = tmp_path / "events.db"
    con = sqlite3.connect(filename)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT, metadata_json TEXT)")
    base = datetime(2026, 10, 10, tzinfo=timezone.utc)
    con.executemany(
        "INSERT INTO events VALUES (?,?,?,?)",
        [(i+1, f"event-{i:04d}", (base+timedelta(seconds=i)).isoformat(), "{}")
         for i in range(1_000)]
    )
    con.commit()
    con.close()
    def connect():
        c=sqlite3.connect(filename)
        c.row_factory=sqlite3.Row
        return c
    monkeypatch.setattr(context_pulse,"connect",connect)
    rows, truncated=context_pulse._source_rows(
        snapshot_max_id=1000,
        snapshot_at=(base+timedelta(days=1)).isoformat(),
        lookback_days=7,
        source_event_budget=80,
    )
    assert truncated
    assert [r["event_id"] for r in rows] == [
        f"event-{i:04d}" for i in range(920, 1000)
    ]


def test_local_mcp_pulse_requests_8000_by_default_and_retains_full_optin():
    script=r'''
from mcp_server import compact
from mcp_server.compact_hardening import apply_compact_hardening
apply_compact_hardening(compact)
calls=[]
compact.core._begin=lambda *_:None
compact.core._finish=lambda name,x:x
def secure_get(path, params):
    assert path == "/v1/context-pulse"
    calls.append(params)
    return {"findings_source_truncated":True,
            "findings_source_event_budget":params["source_event_budget"]}
compact.secure_runtime.secure_get=secure_get
bounded=compact.get_context_pulse()
assert bounded["findings_source_event_budget"]==8000
assert bounded["findings_source_truncated"] is True
complete=compact.get_context_pulse(full_findings=True)
assert complete["findings_source_event_budget"]==100000
assert len(calls)==2
'''
    run=subprocess.run([sys.executable,"-c",script],
                       cwd=Path(__file__).resolve().parents[1],
                       capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
