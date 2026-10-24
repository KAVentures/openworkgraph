"""Disco-style abstracted process map (roadmap #326 G3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_process_map as maps
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """90 cases A→B→D (B after 1 h, D after 3 h) and 10 cases A→C→D with a rare C."""
    rows = ["case,activity,time"]
    for c in range(100):
        steps = [("A", 0), ("B", 1), ("D", 3)] if c < 90 else [("A", 0), ("C", 2), ("D", 6)]
        rows += [f"c{c},{a},{(START + timedelta(days=c, hours=h)).isoformat()}" for a, h in steps]
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="map", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def _edges(result):
    return {(t["from"], t["to"]): t for t in result["transitions"]}


def test_full_map_has_frequency_and_performance_per_transition():
    result = maps.process_map(_log())
    edges = _edges(result)
    assert set(edges) == {("A", "B"), ("B", "D"), ("A", "C"), ("C", "D")}
    assert edges[("A", "B")]["transition_count"] == 90 and edges[("A", "B")]["median_elapsed_gap_seconds"] == 3600
    assert edges[("C", "D")]["mean_elapsed_gap_seconds"] == 4 * 3600
    assert result["start_activities"] == {"A": 100} and result["end_activities"] == {"D": 100}
    assert result["coverage"]["activities_hidden"] == 0 and result["coverage"]["transition_share_shown"] == 1


def test_hiding_a_rare_activity_reconnects_cases_through_the_remaining_steps():
    result = maps.process_map(_log(), activities=0.95)
    assert [a["activity"] for a in result["activities"]] == ["A", "D", "B"]
    edges = _edges(result)
    assert set(edges) == {("A", "B"), ("B", "D"), ("A", "D")}
    assert edges[("A", "D")]["transition_count"] == 10 and edges[("A", "D")]["median_elapsed_gap_seconds"] == 6 * 3600
    assert result["coverage"]["activities_hidden"] == 1 and result["coverage"]["event_share_shown"] == 290 / 300


def test_path_slider_keeps_frequent_paths_and_never_strands_an_activity():
    result = maps.process_map(_log(), paths=0.5)
    edges = _edges(result)
    assert ("A", "B") in edges and ("B", "D") in edges
    # C would be stranded by the threshold; its strongest in and out paths are added back.
    assert ("A", "C") in edges and ("C", "D") in edges
    tight = maps.process_map(_log(), activities=0.95, paths=0.5)
    assert set(_edges(tight)) == {("A", "B"), ("B", "D")}   # A→D is not needed for connectivity
    assert tight["coverage"]["transition_types_shown"] == 2 and tight["coverage"]["transition_types_total"] == 3


def test_activity_cap_cohorts_and_bad_settings(monkeypatch):
    log = _log()
    monkeypatch.setattr(maps, "MAX_MAP_ACTIVITIES", 2)
    capped = maps.process_map(log)
    assert len(capped["activities"]) == 2 and capped["coverage"]["activity_limit_reached"] is True
    monkeypatch.setattr(maps, "MAX_MAP_ACTIVITIES", 64)
    rare = maps.process_map(log, cohort={"activity": "C"})
    assert set(_edges(rare)) == {("A", "C"), ("C", "D")}
    for bad in ({"activities": 0}, {"paths": 1.5}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            maps.process_map(log, **bad)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/process-map"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path + "?activities=1&paths=0.5", headers=headers)
    assert result.status_code == 200 and result.json()["transitions"][0]["from"] == "A", result.text
    assert client.get(path + "?paths=0", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_redraw_interactively():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        for s in range(5):
            rows.append(f"c{c},S{(s + c % 7) % 30},{(START + timedelta(seconds=c * 10 + s)).isoformat()}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    started = time.perf_counter()
    result = maps.process_map(log, activities=0.8, paths=0.6)
    assert time.perf_counter() - started < 20 and result["coverage"]["activities_shown"] > 1
