"""Focus blocks and app switches from focus spans."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SEED = r'''
from server import db, focus_report
db.init_db()
def span(i, at, seconds, app):
    return {"event_id": f"f{i}", "session_id": "s", "observed_at": at, "schema_version": "1.0", "actor_id": "human",
            "device_id": "d", "sensor_id": "collector", "source": "collector", "app": app, "window_title": "",
            "event_type": "focus_span", "duration_seconds": seconds, "metadata": {}}
db.insert_events([
    span(1, "2024-03-04T09:00:00+00:00", 1200, "Editor"),
    span(2, "2024-03-04T09:20:00+00:00", 10, "Chat"),       # 10 s detour: folded back into Editor
    span(3, "2024-03-04T09:20:10+00:00", 1200, "Editor"),
    span(4, "2024-03-04T09:40:10+00:00", 300, "Mail"),       # a real switch
    span(5, "2024-03-04T09:45:10+00:00", 300, "Editor"),
    span(6, "2024-03-04T13:00:00+00:00", 600, "Mail"),       # after a long gap: a new block, not a switch
])
'''


def _run(code, tmp_path):
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_blocks_detours_switches_and_gaps(tmp_path):
    _run(SEED + r'''
r = focus_report.report(start="2024-03-04", end="2024-03-04")
assert r["focus_blocks"] == 4, r["longest_blocks"]
assert r["app_switches"] == 2 and r["top_switch_pairs"] == [{"apps": ["Editor", "Mail"], "switches": 2}], r
top = r["longest_blocks"][0]
assert top["app"] == "Editor" and top["seconds"] == 2410.0 and top["short_detours"] == 1
assert r["blocks_over_25_minutes"] == 1
day = r["days"][0]
assert day["app_switches"] == 2 and day["longest_block_seconds"] == 2410.0 and day["switches_per_active_hour"] > 0
strict = focus_report.report(start="2024-03-04", end="2024-03-04", ignore_detours_seconds=0)
assert strict["app_switches"] == 4 and strict["focus_blocks"] == 6
assert "not a measure of attention" in r["interpretation"]
for bad in ({"start": "2024-03-05", "end": "2024-03-04"}, {"start": "2024-03-04", "end": "2024-03-04", "tz": "No/Zone"},
            {"start": "2024-03-04", "end": "2024-03-04", "max_gap_seconds": -1}):
    try:
        focus_report.report(**bad); raise SystemExit(f"accepted {bad}")
    except focus_report.FocusReportError:
        pass
''', tmp_path)


def test_route_requires_the_dashboard(tmp_path):
    _run(SEED + r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/focus-report?start=2024-03-04&end=2024-03-04"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and r.json()["app_switches"] == 2, r.text
    assert client.get(path + "&tz=No/Zone", headers=owner).status_code == 422
''', tmp_path)
