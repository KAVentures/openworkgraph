"""Held-out validation of what-if simulation (#370 R14)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import math
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(gap_hours=2.0, cases=1000, change_at=None, factor=3.0, exponential=False, seed=1):
    """Cases arrive about once an hour; each is A then B after ``gap_hours``, done by its own resource (no queueing)."""
    rng, rows, t = random.Random(seed), ["case,activity,time,who"], START
    for c in range(cases):
        t += timedelta(hours=rng.expovariate(1.0))
        gap = gap_hours * (factor if change_at is not None and c >= change_at else 1)
        if exponential:
            gap = rng.expovariate(1 / gap)
        rows += [f"c{c},A,{t:%Y-%m-%dT%H:%M:%SZ},r{c}", f"c{c},B,{t + timedelta(hours=gap):%Y-%m-%dT%H:%M:%SZ},r{c}"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="v", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})["log_id"]


def test_a_stable_process_reproduces_the_held_out_period():
    result = sim.validate(_log(), {"distribution": "fixed"})
    cases = result["cases"]
    assert cases["held_out"] == 300 and cases["finished_before_cutoff"] + cases["spanning_cutoff_left_out"] == 700
    assert cases["calibration"] == cases["finished_before_cutoff"]          # nothing after the cutoff is learned
    errors = result["cycle_time_seconds"]["relative_error"]
    assert all(abs(errors[k]) < 1e-6 for k in ("p50", "p90", "mean"))
    distance = result["distribution_distance"]
    assert distance["kolmogorov_smirnov"] < distance["critical_value_5pct"] and not distance["distributions_differ_at_5pct"]
    n, m = 1800, 300                                                         # 2,000 simulated minus the 10% warm-up
    assert distance["critical_value_5pct"] == pytest.approx(1.358 * math.sqrt((n + m) / (n * m)), rel=1e-4)
    assert result["arrivals_per_hour"]["calibrated"] == pytest.approx(1.0, rel=0.15)
    assert result["forecast_or_guarantee"] is False and any("not a forecast" in a for a in result["assumptions"])


def test_a_process_that_changed_after_the_cutoff_is_flagged():
    # From case 700 on, B takes three times as long; the calibration cannot know.
    result = sim.validate(_log(change_at=700), {"distribution": "fixed"})
    assert result["cycle_time_seconds"]["relative_error"]["p50"] == pytest.approx(-2 / 3, abs=1e-6)
    assert result["distribution_distance"]["kolmogorov_smirnov"] == 1
    assert result["distribution_distance"]["distributions_differ_at_5pct"] is True


def test_validation_exposes_the_median_as_mean_calibration_bias():
    # Calibration takes the median gap as the exponential mean; an exponential's median is ln 2 of its mean,
    # so simulated cycle times come out short by about 1 - ln 2. The held-out comparison makes that visible.
    errors = [sim.validate(_log(exponential=True, seed=s), {})["cycle_time_seconds"]["relative_error"]["mean"]
              for s in (1, 2, 3)]
    assert sum(errors) / len(errors) == pytest.approx(math.log(2) - 1, abs=0.08)


def test_cases_spanning_the_cutoff_are_left_out_of_the_calibration():
    log = _log(gap_hours=30)                      # long cases: many are still running at the cutoff
    result = sim.validate(log, {"distribution": "fixed"})
    assert result["cases"]["spanning_cutoff_left_out"] > 20
    assert result["cases"]["calibration"] == result["cases"]["finished_before_cutoff"]


@pytest.mark.parametrize("spec, message", [
    ([], "validation takes"), ({"holdout": 0.3}, "validation takes"), ({"holdout_share": 0.9}, "holdout_share"),
    ({"holdout_share": True}, "holdout_share"), ({"arrival_pattern": {"from_log": True}}, "held-out period"),
    ({"cases": 5}, "cases"),
])
def test_invalid_settings_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        sim.validate(_log(cases=100), spec)


def test_too_few_cases_on_either_side_are_refused():
    with pytest.raises(workspace.EventLogWorkspaceError, match="at least 10 cases"):
        sim.validate(_log(cases=20), {"holdout_share": 0.2})


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-{1 + i // 20:02d}T{i % 20:02d}:{n * 30:02d}:00Z"
                                 for i in range(200) for n, a in enumerate(["A", "B"])]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/simulation/validation"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json={}, headers=bearer).status_code in (401, 403)
    ok = client.post(path, json={"distribution": "fixed", "cases": 500}, headers=headers)
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store", ok.text
    assert ok.json()["cycle_time_seconds"]["observed_held_out"]["p50"] == 1800
    assert client.post(path, json={"holdout_share": 2}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
