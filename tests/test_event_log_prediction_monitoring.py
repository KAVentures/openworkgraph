"""Calibration and drift monitoring for predictions (#370 R19)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from server import event_log_prediction_monitoring as monitoring
from server import event_log_predictions as predictions
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
SPEC = {"end_activities": ["Close"], "windows": 6}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(seed: int, hours, cases: int = 1200) -> str:
    """Open → Work → Close every 4 hours; ``hours(i, rng)`` gives the two step durations."""
    rng, rows = random.Random(seed), ["case,activity,time"]
    for i in range(cases):
        t = START + timedelta(hours=4 * i)
        a, b = hours(i, rng)
        rows += [f"c{i},Open,{t.isoformat()}", f"c{i},Work,{(t + timedelta(hours=a)).isoformat()}",
                 f"c{i},Close,{(t + timedelta(hours=a + b)).isoformat()}"]
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="p", source_format="csv", mapping=MAPPING)["log_id"]


def _steady(i, rng):
    return rng.expovariate(1 / 10), rng.expovariate(1 / 10)


def _slowing(i, rng):
    mean = 30 if i >= 800 else 10
    return rng.expovariate(1 / mean), rng.expovariate(1 / mean)


def test_a_process_that_slows_down_is_flagged_and_a_wider_range_proposed():
    result = monitoring.monitor(_log(3, _slowing), SPEC)
    windows = result["windows"]
    assert [w["window"] for w in windows] == [1, 2, 3, 4, 5, 6] and all(w["scored"] for w in windows)
    latest = windows[-1]
    assert latest["p10_p90_coverage_ci95"][1] < 0.8 and latest["coverage_flag"] == "ranges_too_narrow"
    flags = {f["flag"]: f for f in result["flags"]}
    assert set(flags) == {"ranges_too_narrow", "error_rising"} and flags["error_rising"]["ratio_to_first_window"] > 2
    proposal = result["recalibration_proposal"]
    assert proposal["proposed_not_applied"] is True and proposal["range_scaling_factor"] > 1.2
    assert proposal["latest_window_coverage_before"] < 0.7 <= 0.8 <= proposal["latest_window_coverage_after"]
    # Windows train only on cases that ended before them: each window's start is the previous one's end.
    assert all(a["to"] == b["from"] for a, b in zip(windows, windows[1:]))


@pytest.mark.parametrize("seed", [3, 4, 5])
def test_a_steady_process_raises_no_flags(seed):
    result = monitoring.monitor(_log(seed, _steady), SPEC)
    assert result["flags"] == []
    coverage = [w["p10_p90_coverage"] for w in result["windows"]]
    assert 0.75 < sum(coverage) / len(coverage) < 0.87
    assert 0.7 < result["recalibration_proposal"]["range_scaling_factor"] < 1.3


def test_ranges_that_become_too_wide_get_a_narrowing_proposal():
    # Durations vary widely early on and hardly at all later.
    log = _log(6, lambda i, rng: (rng.expovariate(1 / 10), rng.expovariate(1 / 10)) if i < 800
               else (10 + rng.uniform(-0.5, 0.5), 10 + rng.uniform(-0.5, 0.5)))
    result = monitoring.monitor(log, SPEC)
    assert {f["flag"] for f in result["flags"]} == {"ranges_too_wide"}
    assert result["recalibration_proposal"]["range_scaling_factor"] < 0.5


def test_outcome_risk_is_scored_with_a_reliability_table():
    result = monitoring.monitor(_log(3, _slowing), {**SPEC, "outcome": {"kind": "duration_over", "seconds": 3 * 86400}})
    first, latest = result["windows"][0], result["windows"][-1]
    assert latest["brier_score"] > first["brier_score"]
    table = latest["reliability"]
    assert sum(b["prefixes"] for b in table) == latest["scored_prefixes"]
    # Risks learned before the slowdown are far too low once the process is slower.
    assert table[0]["observed_share"] > table[0]["mean_predicted"] + 0.1


def test_live_predictions_are_unchanged_by_monitoring():
    log = _log(3, _steady)
    before = predictions.predict(log, {"end_activities": ["Close"]})
    monitoring.monitor(log, SPEC)
    after = predictions.predict(log, {"end_activities": ["Close"]})
    # Aggregates may differ in their last bits between runs (summation order), never in substance.
    assert after["predictions"] == before["predictions"]
    assert after["backtest"] == pytest.approx(before["backtest"], rel=1e-9)


def test_windows_with_too_few_cases_are_not_scored():
    result = monitoring.monitor(_log(3, _steady, cases=60), {**SPEC, "windows": 12, "min_support": 5})
    assert any(not w["scored"] for w in result["windows"])
    assert all("reason" in w for w in result["windows"] if not w["scored"])


@pytest.mark.parametrize("spec, message", [
    ({**SPEC, "backtest_share": 0.3}, "backtest_share does not apply"),
    ({**SPEC, "estimator": "survival"}, "completed-cases estimator"),
    ({**SPEC, "windows": 1}, "windows"),
    ({**SPEC, "windows": 13}, "windows"),
    ({**SPEC, "outcome": {"kind": "duration_over"}}, "needs seconds"),
    ({"end_activities": ["Never"]}, "needs completed cases"),
    ({**SPEC, "extra": 1}, "extra"),
])
def test_invalid_settings_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        monitoring.monitor(_log(3, _steady, cases=200), spec)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
for i in range(300):
    d, h = 1 + (i // 12) % 28, i % 12
    rows += [f"c{i},Open,2024-{1 + i // 336:02d}-{d:02d}T{h:02d}:00:00Z", f"c{i},Close,2024-{1 + i // 336:02d}-{d:02d}T{h + 1 + i % 5:02d}:00:00Z"]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=m&case_column=case&activity_column=activity&timestamp_column=time",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    path = "/v1/event-logs/" + made.json()["log_id"] + "/predictions/monitoring"
    spec = {"end_activities": ["Close"], "windows": 3, "min_support": 5}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json=spec, headers=bearer).status_code in (401, 403)
    result = client.post(path, json=spec, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert len(result.json()["windows"]) == 3
    assert client.post(path, content='{"windows": 2, "windows": 3}', headers={**headers, "Content-Type": "application/json"}).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
