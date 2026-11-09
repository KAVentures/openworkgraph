"""Prediction snapshots scored later against what happened (#370 R19)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_prediction_snapshots as snapshots
from server import event_log_workspace as workspace

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
SPEC = {"end_activities": ["Close"], "min_support": 5}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv(rows):
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return BytesIO(out.getvalue().encode())


def _history():
    """20 finished cases taking 10 h from Check to Close, and 6 open cases waiting after Check."""
    rows = []
    for i in range(20):
        t = START + timedelta(days=i)
        rows += [(f"c{i}", "Receive", t.isoformat()), (f"c{i}", "Check", (t + timedelta(hours=1)).isoformat()),
                 (f"c{i}", "Close", (t + timedelta(hours=11)).isoformat())]
    late = START + timedelta(days=30)
    for i in range(6):
        rows += [(f"o{i}", "Receive", late.isoformat()), (f"o{i}", "Check", (late + timedelta(hours=1)).isoformat())]
    return rows


def test_snapshot_is_scored_after_newer_events_arrive():
    log = workspace.import_log(_csv(_history()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]
    snap = snapshots.take(log, SPEC)
    assert snap["predicted_cases"] == 6 and snap["open_cases"] == 6 and snap["as_of"].startswith("2024-01-31T01:00")
    assert [s["snapshot_id"] for s in snapshots.listing(log)["snapshots"]] == [snap["snapshot_id"]]
    # Nothing has happened yet: every case is still open.
    first = snapshots.evaluate(log, snap["snapshot_id"])
    assert first["finished_cases"] == 0 and first["still_open_cases"] == 6 and first["remaining_time"] is None
    # Later: four cases close after 10 h (as predicted), one after 40 h, one stays open for 50 h.
    later = START + timedelta(days=30, hours=1)
    newer = [(f"o{i}", "Close", (later + timedelta(hours=10)).isoformat()) for i in range(4)]
    newer += [("o4", "Close", (later + timedelta(hours=40)).isoformat()),
              ("x1", "Receive", (later + timedelta(hours=50)).isoformat())]
    workspace.append_log(log, _csv(newer), source_format="csv", mapping=MAPPING)
    r = snapshots.evaluate(log, snap["snapshot_id"])
    assert r["finished_cases"] == 5 and r["still_open_cases"] == 1 and r["open_share"] == round(1 / 6, 4)
    t = r["remaining_time"]
    assert t["mean_absolute_error_seconds"] == round(30 * 3600 / 5, 3) and t["median_absolute_error_seconds"] == 0
    assert t["coverage_10_90"] == 0.8 and len(t["coverage_10_90_ci95"]) == 2
    # The open case has waited 50 h, past its 10 h 90th percentile: a known miss.
    assert t["still_open_known_misses"] == 1 and r["seconds_since_snapshot"] == 50 * 3600
    assert "lean towards short ones" in r["interpretation"]


def test_outcome_brier_and_validation():
    log = workspace.import_log(_csv(_history()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]
    spec = {**SPEC, "outcome": {"kind": "duration_over", "seconds": 20 * 3600}}
    snap = snapshots.take(log, spec)
    later = START + timedelta(days=30, hours=1)
    workspace.append_log(log, _csv([(f"o{i}", "Close", (later + timedelta(hours=10 if i else 30)).isoformat())
                                    for i in range(6)]), source_format="csv", mapping=MAPPING)
    r = snapshots.evaluate(log, snap["snapshot_id"])
    o = r["outcome"]
    # History never ran over 20 h, so every frozen risk is 0; one case did run over.
    assert o["cases"] == 6 and o["observed_rate"] == round(1 / 6, 4) and o["mean_predicted"] == 0
    assert o["brier_score"] == round(1 / 6, 5)
    with pytest.raises(workspace.EventLogWorkspaceError, match="completed_cases"):
        snapshots.take(log, {**SPEC, "estimator": "survival"})
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown prediction snapshot"):
        snapshots.evaluate(log, "psnap_nope")
    snapshots.delete(log, snap["snapshot_id"])
    assert snapshots.listing(log)["snapshots"] == []
    kept = snapshots.take(log, SPEC)
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        snapshots.evaluate(log, kept["snapshot_id"])


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(20):
    rows += [f"c{i},Receive,2024-01-{i+1:02d}T00:00:00Z", f"c{i},Close,2024-01-{i+1:02d}T05:00:00Z"]
rows += ["o1,Receive,2024-02-01T00:00:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}/prediction-snapshots"
    spec = {"end_activities": ["Close"], "min_support": 5}
    assert client.post(base, json=spec, headers=bearer).status_code == 403
    taken = client.post(base, json=spec, headers=owner)
    assert taken.status_code == 200, taken.text
    sid = taken.json()["snapshot_id"]
    assert client.post(base, json={**spec, "estimator": "survival"}, headers=owner).status_code == 422
    assert client.get(base, headers=bearer).status_code == 403
    assert [s["snapshot_id"] for s in client.get(base, headers=owner).json()["snapshots"]] == [sid]
    scored = client.get(f"{base}/{sid}/evaluation", headers=owner)
    assert scored.status_code == 200 and scored.json()["still_open_cases"] == 1, scored.text
    assert client.get(f"{base}/psnap_x/evaluation", headers=owner).status_code == 404
    assert client.post(f"{base}/{sid}/delete", headers=owner).status_code == 200
    assert client.post(f"{base}/{sid}/delete", headers=owner).status_code == 404
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
