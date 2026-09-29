from __future__ import annotations

"""While the one-time v0.108 title migration runs, Full AI context and
unredacted exports must not return names still held by older rows."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_full_context_and_unredacted_exports_wait_for_the_title_migration(tmp_path):
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"), "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-01-01T00:00:00+00:00",
    })
    code = r'''
import re
from datetime import datetime, timezone
import server.db as db
# Keep the migration "in progress": startup must not complete it behind our back.
db.protect_existing_titles_in_background = lambda: None
from fastapi.testclient import TestClient
import server.enterprise_app, server.history_routes
from server.secure_app import app
from server.local_auth import ensure_api_token
H = {"Authorization": f"Bearer {ensure_api_token()}"}
AI = {**H, "X-OpenWorkGraph-Context": "ai"}
now = datetime.now(timezone.utc).isoformat()

with TestClient(app) as c:
    # 1. A row stored by v0.107, before titles were protected.
    with db.connect() as conn:
        conn.execute("INSERT INTO events(event_id, observed_at, device_id, session_id, app, window_title, event_type, duration_seconds, metadata_json)"
                     " VALUES ('old', ?, 'd', 's', 'Microsoft Teams', 'Chat with Anna Svensson about Q4 renewal', 'focus_span', 30, '{}')", (now,))
    assert not db.title_protection_complete()

    # 2. The person had chosen Full AI context.
    assert c.post("/v1/ai-context", headers=H, json={"detail": "full"}).status_code == 200
    state = c.get("/v1/ai-context", headers=H).json()
    assert state["user_setting"] == "full" and state["detail_level"] == "redacted" and state["migration_in_progress"] is True

    # 3-4. Neither the AI nor an unredacted export gets the name while migrating.
    trace = c.get("/v1/workflow-trace", headers=AI, params={"scope": "all"})
    assert trace.status_code == 200 and "Anna" not in trace.text and "Svensson" not in trace.text, trace.text[:400]
    assert "Q4 renewal" in trace.text
    ticket = c.post("/v1/export-ticket", headers=H, json={"format": "json", "scope": "all", "include_raw": True, "redact_names": False}).json()
    export = c.get(ticket["url"])
    assert export.status_code == 200 and "Anna" not in export.text and "Svensson" not in export.text
    history = c.get("/v1/history/export-json", headers=H, params={"include_raw": "true", "redact_names": "false"})
    assert history.status_code == 200 and "Anna" not in history.text and "Svensson" not in history.text

    # 5. The migration finishes.
    assert db.protect_existing_titles() == 1
    assert db.title_protection_complete()

    # 6. Full is restored and returns the privacy-hardened stored text.
    state = c.get("/v1/ai-context", headers=H).json()
    assert state["detail_level"] == "full" and state["migration_in_progress"] is False
    full = c.get("/v1/workflow-trace", headers=AI, params={"scope": "all"})
    assert full.status_code == 200 and "Anna" not in full.text
    assert re.search(r"Chat with PERSON_[0-9A-F]{6} about Q4 renewal", full.text), full.text[:400]
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_small_histories_are_protected_before_startup_returns(tmp_path):
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    code = r'''
from datetime import datetime, timezone
import server.db as db
db.init_db()
with db.connect() as conn:
    conn.execute("INSERT INTO events(event_id, observed_at, device_id, session_id, app, window_title, event_type, duration_seconds, metadata_json)"
                 " VALUES ('old', ?, 'd', 's', 'Slack', 'Erik Lund (DM) - Slack', 'focus_span', 30, '{}')", (datetime.now(timezone.utc).isoformat(),))
db.protect_existing_titles_in_background()
assert db.title_protection_complete()  # synchronous: nothing left for a background thread
with db.connect() as conn:
    assert "Erik" not in conn.execute("SELECT window_title FROM events").fetchone()[0]
db.protect_existing_titles_in_background()  # already done: returns at once
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
