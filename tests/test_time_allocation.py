"""Local time-allocation report from focus spans."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, tmp_path) -> None:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


SEED = r'''
from server import db, time_allocation
db.init_db()
def span(i, at, seconds, app, title="", engaged=None, idle=None):
    meta = {"activity": {"engaged_seconds": engaged, "idle_seconds": idle}} if engaged is not None else {}
    return {"event_id": f"f{i}", "session_id": "s", "observed_at": at, "schema_version": "1.0", "actor_id": "human",
            "device_id": "d", "sensor_id": "collector", "source": "collector", "app": app, "window_title": title,
            "event_type": "focus_span", "duration_seconds": seconds, "metadata": meta}
db.insert_events([
    span(1, "2024-03-04T08:00:00+00:00", 3600, "Mail", "Inbox - Ada", engaged=3000, idle=600),
    span(2, "2024-03-04T22:30:00+00:00", 3600, "Editor", "Q3 plan"),     # 23:30-00:30 Stockholm: crosses midnight
    span(3, "2024-03-05T09:00:00+00:00", 1800, "Mail", "Inbox - Ada"),
    span(4, "2024-03-07T09:00:00+00:00", 600, "Browser"),                  # outside the window
])
'''


def test_days_apps_hours_and_midnight_split(tmp_path):
    _run(SEED + r'''
r = time_allocation.report(start="2024-03-04", end="2024-03-05", tz="Europe/Stockholm")
days = {d["date"]: d for d in r["days"]}
assert days["2024-03-04"]["apps"] == {"Mail": 3600.0, "Editor": 1800.0}, days
assert days["2024-03-05"]["apps"] == {"Mail": 1800.0, "Editor": 1800.0}, days
assert r["recorded_seconds"] == 9000.0 and r["spans"] == 3
mail = next(a for a in r["apps"] if a["app"] == "Mail")
assert mail["recorded_seconds"] == 5400.0 and mail["engaged_seconds"] == 3000.0 and mail["idle_seconds"] == 600.0
assert r["hours_of_day_seconds"][9] == 3600.0 and r["hours_of_day_seconds"][23] == 1800.0 and r["hours_of_day_seconds"][0] == 1800.0
assert r["titles_included"] is False and "Ada" not in str(r)
t = time_allocation.report(start="2024-03-04", end="2024-03-04", tz="UTC", include_titles=True)
top = next(x for x in t["top_window_titles"] if x["app"] == "Mail")
assert top["app"] == "Mail" and top["title"].startswith("Inbox - ") and top["recorded_seconds"] == 3600.0
assert "Ada" not in str(t), "names in titles stay pseudonymized as stored"
# In UTC the Editor span (22:30-23:30) falls entirely on the 4th; the time zone decides the day.
utc = time_allocation.report(start="2024-03-05", end="2024-03-05", tz="UTC")
assert utc["days"][0]["apps"] == {"Mail": 1800.0}
csv_text = time_allocation.to_csv(r)
assert csv_text.splitlines()[0] == "date,app,recorded_hours" and "2024-03-04,Mail,1.0" in csv_text
for bad in ({"start": "2024-03-05", "end": "2024-03-04"}, {"start": "x", "end": "2024-03-04"},
            {"start": "2024-01-01", "end": "2024-06-01"}, {"start": "2024-03-04", "end": "2024-03-04", "tz": "Mars/Base"}):
    try:
        time_allocation.report(**bad); raise SystemExit(f"accepted {bad}")
    except time_allocation.TimeAllocationError:
        pass
''', tmp_path)


def test_routes_require_the_dashboard(tmp_path):
    _run(SEED + r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/time-allocation?start=2024-03-04&end=2024-03-05&tz=Europe/Stockholm"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and r.json()["recorded_seconds"] == 9000.0, r.text
    c = client.get(path + "&format=csv&titles=true", headers=owner)
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv") and "Ada" not in c.text
    assert 'time-allocation-2024-03-04-to-2024-03-05.csv' in c.headers["content-disposition"]
    assert client.get("/v1/time-allocation?start=2024-03-05&end=2024-03-04", headers=owner).status_code == 422
''', tmp_path)
