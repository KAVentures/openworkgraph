"""Before/after intervention impact with control cohorts and a placebo check (#370 R16)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_impact as impact
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
AT = "2024-03-01T00:00:00Z"
HOUR = 3600
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "team"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases, hours, *, teams=("A", "B"), extra=None) -> str:
    """One case every 6 hours from 1 January; ``hours(i, started, team)`` gives its cycle time."""
    rows = ["case,activity,time,team"]
    for i in range(cases):
        t = START + timedelta(hours=6 * i)
        team = teams[i % len(teams)]
        end = t + timedelta(hours=hours(i, t, team))
        rows += [f"c{i},Open,{t.isoformat()},{team}"]
        rows += [f"c{i},{a},{(t + (end - t) * (k + 1) / (len(extra(i, t)) + 1)).isoformat()},{team}"
                 for k, a in enumerate(extra(i, t))] if extra else []
        rows += [f"c{i},Close,{end.isoformat()},{team}"]
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="impact", source_format="csv", mapping=MAPPING)["log_id"]


def _after(t) -> bool:
    return t >= datetime(2024, 3, 1, tzinfo=timezone.utc)


def test_a_step_change_is_measured_exactly_with_an_interval_around_it():
    # 48 h before the intervention, 24 h after, with a deterministic ±2 h wobble.
    log = _log(480, lambda i, t, team: (24 if _after(t) else 48) + (i % 5 - 2))
    result = impact.analyze(log, {"intervention_at": AT})
    change = result["change"]
    assert change["estimated"] and change["median_change"] == -24 * HOUR
    assert abs(change["mean_change"] + 24 * HOUR) < 0.2 * HOUR
    low, high = change["mean_change_ci95"]
    assert low < -24 * HOUR < high and high < 0
    assert change["relative_mean_change"] == pytest.approx(-0.5, abs=0.01)
    assert result["placebo"]["estimated"] and result["placebo"]["pre_trend_detected"] is False
    assert result["reading"].startswith("The mean cycle time went down after the intervention")
    assert result["causal_effect_verified"] is False and result["realized_savings_verified"] is False
    groups = result["groups"]["all"]
    # 240 cases start in January and February; baseline cases still open on 1 March are partly exposed.
    assert groups["baseline"]["cases"] == 240 and groups["follow_up"]["cases"] == 240
    assert groups["left_out"]["baseline_partly_after_intervention"] == 8


def test_a_control_cohort_removes_a_shared_trend():
    # Both teams speed up by 6 h (a shared change); only team A also gains the intervention's 18 h.
    log = _log(960, lambda i, t, team: 48 - ((6 + (18 if team == "A" else 0)) if _after(t) else 0) + (i % 3 - 1))
    plain = impact.analyze(log, {"intervention_at": AT, "treated": {"resource": "A"}})
    did = plain["control_adjusted"]
    assert did["estimated"] and abs(did["difference_in_differences"] + 18 * HOUR) < 0.2 * HOUR
    assert did["ci95"][0] < -18 * HOUR < did["ci95"][1]
    # Without the control the treated change also includes the shared 6 h.
    assert abs(plain["change"]["mean_change"] + 24 * HOUR) < 0.2 * HOUR
    assert plain["cohorts"]["control"] == "every case not in the treated cohort"
    assert "the difference remains" in plain["reading"]
    explicit = impact.analyze(log, {"intervention_at": AT, "treated": {"resource": "A"}, "control": {"resource": "B"}})
    assert explicit["control_adjusted"] == did


def test_the_placebo_flags_a_metric_that_was_already_falling():
    # Cycle time falls steadily all year: the intervention date is not where the change started.
    log = _log(480, lambda i, t, team: 72 - 0.12 * i)
    result = impact.analyze(log, {"intervention_at": AT})
    assert result["placebo"]["pre_trend_detected"] is True
    assert "already changing before the intervention" in result["reading"]


def test_windows_recent_cases_and_spanning_cases_are_counted():
    log = _log(480, lambda i, t, team: (24 if _after(t) else 48))
    windowed = impact.analyze(log, {"intervention_at": AT, "window_days": 14, "exclude_recent_days": 10})
    left = windowed["groups"]["all"]["left_out"]
    # 14 days on each side at 4 cases a day; 56 baseline cases, follow-up minus nothing recent inside the window.
    assert windowed["groups"]["all"]["baseline"]["cases"] == 56 and windowed["groups"]["all"]["follow_up"]["cases"] == 56
    assert left["outside_window"] == 480 - 112 and left["recent_possibly_running"] == 0
    recent = impact.analyze(log, {"intervention_at": AT, "exclude_recent_days": 10})["groups"]["all"]["left_out"]
    # The log ends with the last case, a day after it starts: 37 cases start in its final 10 days.
    assert recent["recent_possibly_running"] == 37
    dropped = impact.analyze(log, {"intervention_at": AT, "exclude_spanning": True})["groups"]["all"]
    assert dropped["left_out"]["spanning_intervention"] == 8 and dropped["baseline"]["cases"] == 232


def test_shares_and_counts_are_measured_too():
    log = _log(480, lambda i, t, team: 40 if _after(t) else 48,
               extra=lambda i, t: ["Check", "Check"] if (not _after(t) and i % 2) else ["Check"])
    rework = impact.analyze(log, {"intervention_at": AT, "metric": "rework"})
    assert rework["unit"] == "share of cases" and rework["groups"]["all"]["baseline"]["mean"] == 0.5
    assert rework["groups"]["all"]["follow_up"]["mean"] == 0 and rework["change"]["mean_change"] == -0.5
    checks = impact.analyze(log, {"intervention_at": AT, "metric": "activity_count", "activity": "Check"})
    assert checks["change"]["mean_change"] == -0.5 and checks["unit"] == "events per case"
    sla = impact.analyze(log, {"intervention_at": AT, "metric": "sla_breach", "sla_seconds": 44 * HOUR})
    assert sla["groups"]["all"]["baseline"]["mean"] == 1 and sla["groups"]["all"]["follow_up"]["mean"] == 0


def test_too_few_cases_withhold_the_estimate():
    log = _log(40, lambda i, t, team: 10)
    result = impact.analyze(log, {"intervention_at": "2024-01-08T00:00:00Z", "treated": {"resource": "A"}})
    assert result["change"]["estimated"] is False and result["control_adjusted"]["estimated"] is False
    assert result["reading"] == "Too few cases on one side of the intervention to compare."


def test_results_are_reproducible_for_a_seed():
    log = _log(480, lambda i, t, team: (24 if _after(t) else 48) + (i % 7))
    first = impact.analyze(log, {"intervention_at": AT, "seed": 5})
    assert impact.analyze(log, {"intervention_at": AT, "seed": 5}) == first
    assert impact.analyze(log, {"intervention_at": AT, "seed": 6})["change"]["mean_change_ci95"] != \
        first["change"]["mean_change_ci95"]


@pytest.mark.parametrize("spec, message", [
    ({"intervention_at": "2024-03-01T00:00:00"}, "UTC offset"),
    ({"intervention_at": "the first of March"}, "ISO 8601"),
    ({"intervention_at": "2030-01-01T00:00:00Z"}, "inside the log"),
    ({"intervention_at": AT, "metric": "activity_count"}, "activity to count"),
    ({"intervention_at": AT, "activity": "Close"}, "only to activity_count"),
    ({"intervention_at": AT, "metric": "sla_breach"}, "needs sla_seconds"),
    ({"intervention_at": AT, "sla_seconds": 10}, "only to sla_breach"),
    ({"intervention_at": AT, "control": {"resource": "B"}}, "needs a treated cohort"),
    ({"intervention_at": AT, "treated": {"resource": "A"}, "control": {"activity": "Open"}}, "must not share"),
    ({"intervention_at": AT, "treated": {"bogus": 1}}, "cohort accepts"),
    ({"intervention_at": AT, "rounds": 5}, "rounds"),
    ({"intervention_at": AT, "other": 1}, "other"),
])
def test_invalid_settings_are_refused(spec, message):
    log = _log(480, lambda i, t, team: 10)
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        impact.analyze(log, spec)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-0{1 + i // 40}-{1 + i % 28:02d}T{h:02d}:00:00Z"
                                 for i in range(80) for a, h in (("Open", 1), ("Close", 9 if i < 40 else 5))]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=i&case_column=case&activity_column=activity&timestamp_column=time",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    path = "/v1/event-logs/" + made.json()["log_id"] + "/impact"
    spec = {"intervention_at": "2024-02-01T00:00:00Z"}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json=spec, headers=bearer).status_code in (401, 403)
    result = client.post(path, json=spec, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["change"]["median_change"] == -4 * 3600
    assert client.post(path, json={"intervention_at": "nope"}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_keeping_spanning_cases_avoids_a_baseline_biased_short():
    """Cases still open at the intervention are the long ones; dropping them makes the baseline look faster."""
    log = _log(480, lambda i, t, team: 200 if i % 4 == 0 else 12)     # mean 59 h in both periods
    kept = impact.analyze(log, {"intervention_at": AT})
    dropped = impact.analyze(log, {"intervention_at": AT, "exclude_spanning": True})
    assert abs(kept["groups"]["all"]["baseline"]["mean"] - 59 * HOUR) < 1 * HOUR
    assert dropped["groups"]["all"]["baseline"]["mean"] < kept["groups"]["all"]["baseline"]["mean"] - 2 * HOUR
    assert kept["change"]["mean_change_ci95"][0] < 0 < kept["change"]["mean_change_ci95"][1]
