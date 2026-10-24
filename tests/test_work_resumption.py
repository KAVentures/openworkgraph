"""Cross-session / cross-day resumption candidates (roadmap #326 A3)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from server.work_resumption import resumption_candidates

ROOT = Path(__file__).resolve().parents[1]
REF = {name: "owg:r:" + name * 32 for name in "abcdef"}
FILE = "owg:f:" + "9" * 32
ALL = {"mode": "all_saved"}


def _database(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT, metadata_json TEXT)")
    for index, (at, meta) in enumerate(rows, start=1):
        db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (index, f"e{index}", at, json.dumps(meta)))
    return db


def _ref(name, **extra):
    return {"resource_reference": {"resource_ref": REF[name], "provider": "github",
                                   "resource_kind": "pull_request", **extra}}


def _day(day, hhmm):
    return f"2026-10-{day:02d}T{hhmm}:00Z"


def test_resumed_work_from_a_previous_day_is_found_by_shared_pointers():
    db = _database([
        (_day(12, "09:00"), _ref("a")), (_day(12, "09:10"), _ref("b")),
        (_day(12, "09:20"), {"file_reference": {"file_ref": FILE}}),
        (_day(13, "14:00"), _ref("c")),                              # unrelated day
        (_day(14, "10:00"), _ref("a")), (_day(14, "10:05"), _ref("b")),
    ])
    result = resumption_candidates(db, lease=ALL)
    assert result["current_session"]["started_at"].startswith("2026-10-14T10:00")
    [candidate] = result["candidates"]
    assert candidate["earlier_session"]["started_at"].startswith("2026-10-12T09:00")
    assert [p["resource_ref"] for p in candidate["shared_pointers"]] == [REF["a"], REF["b"]]
    assert candidate["association_status"] == "likely_resumed" and candidate["confidence"] == 0.8
    assert candidate["crosses_calendar_day"] is True and candidate["proves_same_work"] is False
    assert candidate["task_label"] is None
    assert candidate["gap_seconds"] == 2 * 86400 - 20 * 60 + 3600
    assert candidate["trace_window"] == {"since": "2026-10-12T09:00:00+00:00", "until": "2026-10-12T09:20:01+00:00"}
    assert result["association_contract"]["gap_affects_confidence"] is False


def test_time_alone_never_creates_a_candidate():
    # Back-to-back sessions minutes apart but on different resources.
    db = _database([(_day(14, "09:00"), _ref("a")), (_day(14, "09:40"), _ref("b"))])
    result = resumption_candidates(db, lease=ALL)
    assert result["coverage"]["sessions_considered"] == 2
    assert result["candidates"] == []
    assert result["coverage"]["no_candidate_proves_new_work"] is False


def test_confidence_does_not_depend_on_how_long_ago():
    near = resumption_candidates(_database([(_day(14, "08:00"), _ref("a")), (_day(14, "10:00"), _ref("a"))]), lease=ALL)
    far = resumption_candidates(_database([(_day(1, "08:00"), _ref("a")), (_day(14, "10:00"), _ref("a"))]), lease=ALL)
    keys = ("association_status", "confidence", "pointer_overlap", "shared_pointer_count")
    assert [near["candidates"][0][k] for k in keys] == [far["candidates"][0][k] for k in keys]
    assert near["candidates"][0]["crosses_calendar_day"] is False and far["candidates"][0]["crosses_calendar_day"] is True


def test_routine_pointers_present_in_most_sessions_are_only_ambiguous():
    rows = []
    for day in range(1, 7):                         # an always-open dashboard every day
        rows.append((_day(day, "09:00"), _ref("f")))
    rows.append((_day(3, "09:05"), _ref("d")))     # the real piece of work, resumed on day 6
    rows.append((_day(6, "09:05"), _ref("d")))
    result = resumption_candidates(_database(rows), lease=ALL)
    by_start = {c["earlier_session"]["started_at"][:10]: c for c in result["candidates"]}
    assert by_start["2026-10-03"]["association_status"] == "possible"
    assert {p["resource_ref"]: p["routine_context"] for p in by_start["2026-10-03"]["shared_pointers"]} == {
        REF["d"]: False, REF["f"]: True}
    assert by_start["2026-10-05"]["association_status"] == "ambiguous" and by_start["2026-10-05"]["confidence"] == 0.3
    assert result["candidates"][0]["earlier_session"]["started_at"].startswith("2026-10-03")


def test_history_off_never_looks_before_the_current_run():
    db = _database([(_day(12, "09:00"), _ref("a")), (_day(14, "10:00"), _ref("a"))])
    result = resumption_candidates(db, lease={"mode": "off"}, run_started_at=_day(14, "00:00"))
    assert result["coverage"]["sessions_considered"] == 1 and result["candidates"] == []
    assert result["history_access_limited"] is True
    closed = resumption_candidates(db, lease={"mode": "off"}, run_started_at=None)
    assert closed["current_session"] is None and closed["coverage"]["activity_rows_scanned"] == 0
    unknown = resumption_candidates(db, lease={"mode": "everything"}, run_started_at=None)
    assert unknown["saved_history_access"] == "off" and unknown["candidates"] == []


def test_selected_range_exposes_only_sessions_inside_it():
    db = _database([(_day(10, "09:00"), _ref("a")), (_day(12, "09:00"), _ref("a")), (_day(14, "10:00"), _ref("a"))])
    result = resumption_candidates(db, lease={"mode": "selected_range", "since": _day(12, "00:00"),
                                              "until": _day(13, "00:00")}, run_started_at=_day(14, "00:00"))
    assert [c["earlier_session"]["started_at"][:10] for c in result["candidates"]] == ["2026-10-12"]


def test_only_opaque_pointers_leave_the_module():
    db = _database([
        (_day(12, "09:00"), {**_ref("a", host="github.com", resolver_locator="https://x.example/secret?token=bad"),
                             "message_body": "PRIVATE"}),
        (_day(14, "10:00"), {**_ref("a"), "window_title": "PRIVATE TITLE"}),
        (_day(14, "10:01"), {"resource_reference": {"resource_ref": "not-a-ref"}}),
    ])
    text = json.dumps(resumption_candidates(db, lease=ALL))
    assert "PRIVATE" not in text and "secret" not in text and "github.com" not in text and "not-a-ref" not in text


def test_latest_session_without_pointers_says_resumption_is_unknown():
    db = _database([(_day(12, "09:00"), _ref("a")), (_day(14, "10:00"), {"clicks": 3})])
    result = resumption_candidates(db, lease=ALL)
    assert result["candidates"] == [] and "does not mean the work is new" in result["notice"]


def test_idle_gap_setting_controls_session_boundaries():
    db = _database([(_day(14, "09:00"), _ref("a")), (_day(14, "09:20"), _ref("a"))])
    assert resumption_candidates(db, lease=ALL, gap_minutes=30)["candidates"] == []
    split = resumption_candidates(db, lease=ALL, gap_minutes=10)
    assert len(split["candidates"]) == 1 and split["session_gap_minutes"] == 10


def test_scans_stay_bounded_on_large_histories():
    rows = []
    for day in range(1, 29):
        for minute in range(0, 600, 1):
            rows.append((f"2026-09-{day:02d}T{8 + minute // 60:02d}:{minute % 60:02d}:00Z",
                         _ref("abcde"[minute % 5]) if minute % 7 == 0 else {"clicks": 1}))
    db = _database(rows)
    started = time.perf_counter()
    result = resumption_candidates(db, lease=ALL, activity_budget=5_000)
    assert time.perf_counter() - started < 10
    assert result["coverage"]["scan_truncated"] is True and result["coverage"]["activity_rows_scanned"] == 5_000
    assert "older sessions may also share" in result["notice"]
    full = resumption_candidates(db, lease=ALL)
    assert full["coverage"]["sessions_considered"] == 28 and len(full["candidates"]) == 6
    assert full["candidates_total"] == 27


def test_real_http_route_requires_ai_context_and_honours_the_grant(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
import server.context_pulse_routes  # noqa: F401
from server.db import connect
from server.local_auth import ensure_api_token
from shared.history_policy import set_ai_history_access

ref = "owg:r:" + "a" * 32
with TestClient(app) as client:
    with connect() as db:
        for i, at in enumerate(["2026-10-12T09:00:00Z", "2026-10-14T10:00:00Z"]):
            db.execute("INSERT INTO events(event_id, observed_at, device_id, session_id, event_type, metadata_json) "
                       "VALUES (?, ?, 'd', 's', 'focus', ?)", (f"e{i}", at, json.dumps({"resource_reference": {"resource_ref": ref}})))
        db.commit()
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    assert client.get("/v1/work-resumption", headers=bearer).status_code == 403
    ai = {**bearer, "X-OpenWorkGraph-Context": "ai"}
    off = client.get("/v1/work-resumption", headers=ai).json()
    assert off["saved_history_access"] == "off" and off["candidates"] == [], off
    set_ai_history_access(mode="all_saved")
    on = client.get("/v1/work-resumption", headers=ai).json()
    assert on["candidates"][0]["shared_pointers"][0]["resource_ref"] == ref, on
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    env.pop("WORKFLOW_OBSERVER_RUN_STARTED_AT", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


COMPACT = r'''
from mcp_server import compact
fail = False
candidate = {"candidate_id": "resumption_candidate_01", "association_status": "likely_resumed",
             "trace_window": {"since": "2026-10-12T09:00:00+00:00", "until": "2026-10-12T09:20:01+00:00"}}

def fake_get(path, params=None):
    if path == "/v1/workflow-trace":
        return {"rows": []}
    if path == "/v1/history/ai-access":
        return {"access": {"mode": "off"}}
    if path in ("/v1/context-pulse", "/v1/continuity-references"):
        return {"recent_evidence": []}
    if path == "/v1/tasks":
        return {"tasks": [], "patterns": []}
    if path == "/v1/semantic-activity":
        return {"events": []}
    if path == "/v1/work-resumption":
        if fail:
            raise RuntimeError("down")
        return {"candidates": [candidate], "saved_history_access": "all_saved"}
    raise AssertionError((path, params))

compact.secure_runtime.secure_get = fake_get
compact.core._begin = lambda _name: None
compact.core._finish = lambda _name, data: data

result = compact.get_current_work_context()
assert result["continuity_context"]["resumption"]["candidates"] == [candidate]
assert result["orientation"]["earlier_session_resumption_candidates_available"] is True
assert any("earlier session" in hint["when"] for hint in result["navigation_hints"])
fail = True
result = compact.get_current_work_context()
assert result["continuity_context"]["resumption"]["source_status"] == "unavailable"
assert "no absence" in result["continuity_context"]["resumption"]["notice"]
assert result["orientation"]["earlier_session_resumption_candidates_available"] is False
'''


def test_current_work_context_carries_resumption_candidates_and_fails_soft():
    child = subprocess.run([sys.executable, "-c", COMPACT], cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert child.returncode == 0, child.stderr
