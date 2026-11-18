"""Performance spectrum: segment durations over calendar time, with overtaking and batching indicators."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_performance_spectrum as spectrum
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    # 30 orders: "Pick" happens hourly, "Ship" releases them in batches at 18:00 each day (10 a day).
    # "Invoice" follows "Ship" after a delay that shrinks with each order, so later orders overtake earlier ones.
    rows = ["case,activity,time"]
    for i in range(30):
        day, hour = 1 + i // 10, 8 + i % 10
        rows += [f"o{i},Pick,2024-03-{day:02d}T{hour:02d}:00:00Z",
                 f"o{i},Ship,2024-03-{day:02d}T18:00:00Z",
                 f"o{i},Invoice,2024-03-{day + 1:02d}T{20 - i % 10:02d}:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="orders", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_batching_overtaking_and_classes():
    result = spectrum.spectrum(_log(), bins=3)
    by = {(s["from"], s["to"]): s for s in result["segments"]}
    assert set(by) == {("Pick", "Ship"), ("Ship", "Invoice")} and result["segments_total"] == 2
    ship = by[("Pick", "Ship")]
    # Released together: every occurrence ends at one of three instants with ten others.
    assert ship["batched_share"] == 1.0 and ship["occurrences"] == 30
    assert ship["duration_seconds"]["median"] == pytest.approx(5.5 * 3600)
    invoice = by[("Ship", "Invoice")]
    assert invoice["batched_share"] == 0.0
    # Each day's orders all start together, so only the three day boundaries say anything about order.
    assert invoice["fifo_share"] == 1.0
    assert sum(sum(b["counts"].values()) for b in ship["bins"]) == 30 and len(ship["bins"]) == 3
    assert {line["class"] for line in ship["lines"]} <= set(spectrum.CLASSES)
    assert all(line["end"] >= line["start"] for line in ship["lines"])
    assert result["time_origin"].startswith("2024-03-01T08:00:00")


def test_chosen_segments_and_validation():
    log = _log()
    result = spectrum.spectrum(log, segments=spectrum.parse_segments(["Ship->Invoice"]))
    assert [(s["from"], s["to"]) for s in result["segments"]] == [("Ship", "Invoice")]
    with pytest.raises(EventLogWorkspaceError, match="Pick->Invoice"):
        spectrum.spectrum(log, segments=[["Pick", "Invoice"]])
    for kwargs in ({"top": 0}, {"bins": 1}, {"bins": 101}, {"segments": [["a"]]}, {"segments": []}):
        with pytest.raises(EventLogWorkspaceError):
            spectrum.spectrum(log, **kwargs)
    with pytest.raises(EventLogWorkspaceError):
        spectrum.parse_segments(["no arrow"])


def test_cohort_filter():
    result = spectrum.spectrum(_log(), cohort={"started_before": "2024-03-02T00:00:00Z"})
    assert result["cases_before_filter"] == 30
    assert {s["occurrences"] for s in result["segments"]} == {10}


def test_overtaking():
    # Later cases are handled first: each case's "Review" takes less time than the previous one's.
    rows = ["case,activity,time"]
    for i in range(12):
        rows += [f"r{i},Submit,2024-04-01T{8 + i:02d}:00:00Z", f"r{i},Review,2024-04-02T{20 - i:02d}:00:00Z"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="reviews", source_format="csv",
                               mapping=MAPPING)["log_id"]
    seg = spectrum.spectrum(log)["segments"][0]
    assert seg["fifo_share"] == 0.0 and seg["batched_share"] == 0.0
    assert seg["duration_seconds"]["p25"] < seg["duration_seconds"]["p75"]


def test_route_is_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/performance-spectrum"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?segment=A->B&bins=4", headers=owner)
    assert r.status_code == 200 and r.json()["segments"][0]["occurrences"] == 6 and len(r.json()["segments"][0]["bins"]) == 4, r.text
    assert client.get(path + "?segment=B->A", headers=owner).status_code == 422
    assert client.get(path + "?bins=1", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
