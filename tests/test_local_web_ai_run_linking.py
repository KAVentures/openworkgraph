"""Local Web composer continuity uses the same vetted B-after-start linking as hosted."""
from __future__ import annotations

import json
import sqlite3

from server.local_browser_ai_runs import local_web_ai_runs

REF_A = "owg:r:" + "a" * 32
REF_B = "owg:r:" + "b" * 32
TAB = "ref_" + "c" * 24
RUNTIME = "browser-runtime-1"
RUN = "web-abcdef1234567890"


def _database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""
    CREATE TABLE events (id INTEGER PRIMARY KEY,
      event_id TEXT, observed_at TEXT, source TEXT, session_id TEXT,
      event_type TEXT, metadata_json TEXT)
    """)
    common = {"tab_id": TAB, "browser_session_id": RUNTIME}
    events = [
        ("old-a", "2026-10-13T08:00:00Z", "browser_resource_reference_observed",
         {**common, "resource_reference": {"provider": "chatgpt", "resource_ref": REF_A}}),
        ("start", "2026-10-13T08:10:00Z", "browser_agent_run_started",
         {**common, "agent_provider": "chatgpt", "agent_run_id": RUN,
          "message_body": "SECRET_MESSAGE_SHOULD_NOT_LEAK"}),
        ("new-b", "2026-10-13T08:10:05Z", "browser_resource_reference_observed",
         {**common, "resource_reference": {"provider": "chatgpt", "resource_ref": REF_B}}),
        ("finish", "2026-10-13T08:10:45Z", "browser_agent_run_finished",
         {**common, "agent_provider": "chatgpt", "agent_run_id": RUN}),
    ]
    connection.executemany(
        "INSERT INTO events(event_id,observed_at,source,session_id,event_type,metadata_json) VALUES (?,?,?,?,?,?)",
        [(name, at, "browser_extension", "work-session-1", kind, json.dumps(meta))
         for name, at, kind, meta in events],
    )
    return connection


def test_new_chat_b_linked_not_stale_a_with_all_saved_grant():
    result = local_web_ai_runs(_database(), lease={"mode": "all_saved"},
                               run_started_at=None)
    assert result["returned"] == 1
    run = result["executions"][0]
    assert run["resource_refs"] == [REF_B]
    assert REF_A not in str(result)
    assert run["outcome_status"] == "unknown"
    assert "SECRET_MESSAGE" not in str(result)
    assert run["source"] == "browser_ai_lifecycle"


def test_consent_off_never_returns_prior_browser_conversation():
    result = local_web_ai_runs(_database(), lease={"mode": "off"},
                               run_started_at="2026-10-14T00:00:00Z")
    assert result["executions"] == []
    assert result["history_access_limited"] is True


def test_selected_range_and_requested_since_must_both_allow_event():
    c = _database()
    lease = {"mode": "selected_range", "since": "2026-10-13T00:00:00Z",
             "until": "2026-10-14T00:00:00Z"}
    ok = local_web_ai_runs(c, lease=lease, run_started_at=None)
    assert ok["returned"] == 1
    narrow = local_web_ai_runs(c, lease=lease, run_started_at=None,
                               since="2026-10-13T08:10:30Z")
    assert narrow["executions"] == []  # no observed start in narrowed window
