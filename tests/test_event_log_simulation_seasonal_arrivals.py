"""Seasonal arrival patterns in what-if simulation (#370 R14)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
import random
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv

ROOT = Path(__file__).resolve().parents[1]
OFFICE = [0.0] * 9 + [1.0] * 8 + [0.0] * 7          # 09:00–17:00 local


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _run(pattern=None, rate=2.0, minutes=5.0, cases=20000, seed=3, distribution="exponential"):
    return sim.simulate({"label": "", "arrivals_per_hour": rate, "paths": [(["A"], 1.0)],
                         "activities": {"A": {"minutes": minutes, "resources": 1}}, "distribution": distribution,
                         "cases": cases, "seed": seed, "pools": {},
                         "arrival_pattern": sim.parse_arrival_pattern(pattern) if pattern else None})


def test_weights_are_normalized_to_the_long_run_rate_and_arrivals_land_only_in_open_hours():
    result = _run({"timezone": "Europe/Amsterdam", "hourly": OFFICE, "daily": [1, 1, 1, 1, 1, 0, 0]})
    hours, days = result["arrivals_by_local_hour"], result["arrivals_by_local_weekday"]
    assert sum(hours[:9]) == sum(hours[17:]) == 0 and days["sat"] == days["sun"] == 0
    assert all(abs(n - 2500) < 250 for n in hours[9:17])
    pattern = result["arrival_pattern"]
    assert pattern["peak_factor"] == pytest.approx(168 / 40)
    assert pattern["daily_factors"]["mon"] == pytest.approx(7 / 5) and pattern["hourly_factors"][9] == pytest.approx(3)
    # The stated rate stays the long-run average: 20,000 cases at 2/h take about 10,000 hours.
    pattern, rng, t = sim.parse_arrival_pattern({"hourly": OFFICE, "daily": [1, 1, 1, 1, 1, 0, 0]}), random.Random(5), 0.0
    for _ in range(20000):
        t = pattern.next_arrival(t, 2 / 3600, rng.expovariate(1.0))
    assert t / 3600 == pytest.approx(10000, rel=0.03)


def test_flat_pattern_reproduces_the_homogeneous_run():
    plain, flat = _run(), _run({"timezone": "UTC", "hourly": [1] * 24})
    assert flat["cycle_time_seconds"]["mean"] == pytest.approx(plain["cycle_time_seconds"]["mean"], rel=1e-9)
    assert "arrival_pattern" not in plain and flat["arrival_pattern"]["peak_factor"] == pytest.approx(1)


def test_peak_hours_wait_matches_m_m_1_at_the_peak_rate():
    # Average 15/h concentrated into 12 open hours is 30/h while open; 1-minute service gives rho 0.5,
    # so the M/M/1 wait while open is rho / (mu - lambda) = 0.5 / 30 h = 60 s. Unpatterned it is 1/3 of that.
    half = [1.0] * 12 + [0.0] * 12
    peak = _run({"hourly": half}, rate=15, minutes=1, cases=20000)
    flat = _run(rate=15, minutes=1, cases=20000)
    assert peak["activities"][0]["mean_wait_seconds"] == pytest.approx(60, rel=0.12)
    assert flat["activities"][0]["mean_wait_seconds"] == pytest.approx(0.25 / 45 * 3600, rel=0.12)
    assert peak["activities"][0]["offered_load"] == flat["activities"][0]["offered_load"] == pytest.approx(0.25)


def test_monthly_weights_follow_local_months():
    months = [0.0] * 12
    months[11] = 1.0                                   # December only
    result = _run({"timezone": "UTC", "monthly": months, "start": "2025-01-01T00:00:00"}, rate=50, cases=5000)
    first = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert result["arrival_pattern"]["monthly_factors"][11] == pytest.approx(365.0 / 31, rel=0.01)
    pattern = sim.parse_arrival_pattern({"timezone": "UTC", "monthly": months, "start": "2025-01-01T00:00:00"})
    assert pattern.next_arrival(0, 50 / 3600, 1e-9) >= (datetime(2025, 12, 1, tzinfo=timezone.utc) - first).total_seconds()


def test_local_hours_follow_daylight_saving_changes():
    nine = [0.0] * 24
    nine[9] = 1.0
    pattern = sim.parse_arrival_pattern({"timezone": "America/New_York", "hourly": nine, "start": "2025-03-01T00:00:00"})
    t = 0.0
    seen = set()
    for _ in range(400):
        t = pattern.next_arrival(t, 1 / 3600, 5.0)
        local = pattern.local(t)
        assert local.hour == 9
        seen.add(local.utcoffset())
    assert seen == {timedelta(hours=-5), timedelta(hours=-4)}      # before and after 9 March 2025


def test_patterns_keep_runs_paired_and_the_calendar_zone_is_the_default():
    pattern = sim.parse_arrival_pattern({"hourly": OFFICE}, calendar=sim.parse_calendar(
        {"timezone": "Asia/Kolkata", "hours": {"mon": [["09:00", "17:00"]]}}))
    assert pattern.zone == "Asia/Kolkata" and pattern.start.isoformat().startswith("2025-01-06T00:00:00+05:30")
    a = _run({"timezone": "Asia/Kolkata", "hourly": OFFICE}, cases=500)
    b = _run({"timezone": "Asia/Kolkata", "hourly": OFFICE}, cases=500)
    assert a == b


def test_a_calendar_and_a_pattern_share_one_clock():
    calendar = sim.parse_calendar({"timezone": "UTC", "start": "2025-01-06T00:00:00", "hours": {"mon": [["09:00", "17:00"]]}})
    monday_office = {"timezone": "UTC", "hourly": OFFICE, "daily": [1, 0, 0, 0, 0, 0, 0]}
    # A different instant would give the same case two different dates (peer-review reproduction).
    with pytest.raises(workspace.EventLogWorkspaceError, match="same instant as the calendar start"):
        sim.parse_arrival_pattern({**monday_office, "start": "2025-01-05T00:00:00"}, calendar=calendar)
    # The same instant written with another offset or zone is the same clock.
    for start in ("2025-01-06T01:00:00+01:00", "2025-01-06T00:00:00Z"):
        assert sim.parse_arrival_pattern({**monday_office, "start": start}, calendar=calendar).origin == calendar.origin
    berlin = sim.parse_arrival_pattern({**monday_office, "timezone": "Europe/Berlin", "start": "2025-01-06T01:00:00"},
                                       calendar=calendar)
    assert berlin.origin == calendar.origin
    # Arrivals sent during Monday working hours start work then, not a week later.
    pattern = sim.parse_arrival_pattern(monday_office, calendar=calendar)
    result = sim.simulate({"label": "", "arrivals_per_hour": 0.1, "paths": [(["A"], 1.0)], "pools": {},
                           "activities": {"A": {"minutes": 1, "resources": 5}}, "distribution": "fixed",
                           "cases": 200, "seed": 4, "calendar": calendar, "arrival_pattern": pattern})
    assert result["activities"][0]["p90_wait_seconds"] == 0
    with pytest.raises(workspace.EventLogWorkspaceError, match="same instant"):
        sim.what_if(_log(), {"cases": 100, "calendar": {"timezone": "UTC", "hours": {"mon": [["09:00", "17:00"]]}},
                             "arrival_pattern": {**monday_office, "start": "2025-01-05T00:00:00"}})


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_what_if_shares_the_pattern_and_scenario_pattern_changes_only_the_scenario():
    log = _log()
    shared = sim.what_if(log, {"cases": 500, "arrival_pattern": {"timezone": "Europe/Berlin", "hourly": OFFICE}})
    assert shared["baseline_arrival_pattern"] == shared["scenario"]["arrival_pattern"]
    assert shared["baseline"]["arrival_pattern"]["timezone"] == "Europe/Berlin"
    assert any("seasonal pattern" in line for line in shared["assumptions"])
    only = sim.what_if(log, {"cases": 500, "scenario_arrival_pattern": {"daily": [1, 1, 1, 1, 1, 0, 0]}})
    assert only["baseline_arrival_pattern"] is None and "arrival_pattern" not in only["baseline"]
    assert only["result"]["arrivals_by_local_weekday"]["sun"] == 0
    plain = sim.what_if(log, {"cases": 500})
    assert plain["scenario"]["arrival_pattern"] is None and not any("seasonal" in a for a in plain["assumptions"])


def _weekday_office_log(weeks=6):
    zone, rows, n = ZoneInfo("Europe/Amsterdam"), ["case,activity,time"], 0
    day = datetime(2025, 3, 3, tzinfo=zone)                   # Monday; includes the 30 March clock change
    for d in range(weeks * 7):
        local = day + timedelta(days=d)
        if local.weekday() >= 5:
            continue
        for hour in range(9, 17):
            for k in range(3):
                begin = datetime(local.year, local.month, local.day, hour, 10 + 15 * k, tzinfo=zone).astimezone(timezone.utc)
                for step, activity in enumerate(("Open", "Close")):
                    rows.append(f"c{n},{activity},{(begin + timedelta(minutes=step * 5)).strftime('%Y-%m-%dT%H:%M:%SZ')}")
                n += 1
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="office", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def test_pattern_measured_from_case_starts_recovers_local_office_hours_across_dst():
    log = _weekday_office_log()
    result = sim.what_if(log, {"cases": 2000, "arrival_pattern": {"from_log": True, "timezone": "Europe/Amsterdam"}})
    pattern = result["scenario"]["arrival_pattern"]
    assert pattern["source"] == "measured_case_starts"
    assert pattern["daily_factors"]["sat"] == pattern["daily_factors"]["sun"] == 0
    assert pattern["hourly_factors"][8] == pattern["hourly_factors"][17] == 0
    assert all(f == pytest.approx(3.0) for f in pattern["hourly_factors"][9:17])
    assert pattern["peak_factor"] == pytest.approx(168 / 40) and pattern["basis"]["measured_days"] == pytest.approx(40 - 1 / 24, abs=0.001)  # one 23-hour day
    assert pattern["basis"]["case_starts"] == 30 * 8 * 3 and pattern["basis"]["monthly_measured"] is False
    assert sum(result["result"]["arrivals_by_local_hour"][9:17]) == 2000
    # Read in UTC, the same starts shift by the offset in force: 08:00–16:00 in winter, 07:00–15:00 in summer.
    utc = sim.what_if(log, {"cases": 200, "arrival_pattern": {"from_log": True}})["scenario"]["arrival_pattern"]
    assert utc["timezone"] == "UTC" and all(f > 0 for f in utc["hourly_factors"][7:16]) and utc["hourly_factors"][6] == utc["hourly_factors"][16] == 0


def test_too_short_a_span_is_refused_for_measurement():
    rows = ["case,activity,time"] + [f"c{i},{a},2025-01-0{1 + i % 9}T09:{n:02d}:00Z" for i in range(20) for n, a in enumerate("AB")]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="short", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="at least 14 days"):
        sim.what_if(log, {"cases": 100, "arrival_pattern": {"from_log": True}})
    # A user-weighted pattern needs no measurement.
    assert sim.what_if(log, {"cases": 100, "arrival_pattern": {"hourly": OFFICE}})["scenario"]["arrival_pattern"]


@pytest.mark.parametrize("pattern", [
    [], {}, {"timezone": "UTC"}, {"hourly": [1] * 23}, {"hourly": [0] * 24}, {"hourly": [-1] + [1] * 23},
    {"hourly": ["1"] * 24}, {"daily": [1] * 8}, {"monthly": [1] * 11}, {"hourly": [1] * 24, "timezone": "Mars/Base"},
    {"hourly": [1] * 24, "timezone": "../etc"}, {"hourly": [1] * 24, "start": "soon"}, {"hourly": [1] * 24, "weekly": []},
    {"from_log": True, "hourly": [1] * 24}, {"from_log": "yes"}, {"hourly": [True] * 24}, {"hourly": [float("nan")] * 24},
])
def test_invalid_patterns_are_refused(pattern):
    with pytest.raises(workspace.EventLogWorkspaceError):
        sim.what_if(_log(), {"cases": 100, "arrival_pattern": pattern})


def test_patterns_bound_the_simulated_span():
    with pytest.raises(workspace.EventLogWorkspaceError, match="within 10 years"):
        sim.what_if(_log(), {"cases": 20000, "arrivals_per_hour": 0.1, "arrival_pattern": {"hourly": OFFICE}})


@pytest.mark.parametrize("key, value", [
    ("calendar", {"timezone": "UTC", "hours": {"mon": [["09:00", "17:00"]]}, "start": "9999-12-31T00:00:00"}),
    ("calendar", {"timezone": "UTC", "hours": {"mon": [["09:00", "17:00"]]}, "start": "0001-01-01T00:00:00"}),
    ("arrival_pattern", {"hourly": OFFICE, "start": "9999-12-31T00:00:00"}),
    ("arrival_pattern", {"hourly": OFFICE, "start": "1969-12-31T00:00:00"}),
])
def test_out_of_range_start_dates_are_refused_not_crashing(key, value):
    with pytest.raises(workspace.EventLogWorkspaceError, match="between the years"):
        sim.what_if(_log(), {"cases": 100, key: value})


def test_a_calendar_run_past_year_9999_is_refused():
    with pytest.raises(workspace.EventLogWorkspaceError, match="supported date range"):
        sim.what_if(_log(), {"cases": 100, "arrivals_per_hour": 1e-6, "distribution": "fixed",  # ~11,000 years
                             "calendar": {"timezone": "UTC", "hours": {"mon": [["09:00", "17:00"]]},
                                          "start": "2200-01-01T00:00:00"}})


def test_no_platform_clock_function_is_used(monkeypatch):
    # Windows' fromtimestamp raises OSError outside about 1970–3000; the engine must not depend on it.
    class WindowsLikeDatetime(datetime):
        @classmethod
        def fromtimestamp(cls, t, tz=None):
            raise OSError(22, "Invalid argument")
    monkeypatch.setattr(sim, "datetime", WindowsLikeDatetime)
    calendar = sim.parse_calendar({"timezone": "Europe/Berlin", "hours": {"mon": [["09:00", "17:00"]]}})
    pattern = sim.parse_arrival_pattern({"hourly": OFFICE}, calendar=calendar)
    result = sim.simulate({"label": "", "arrivals_per_hour": 2.0, "paths": [(["A"], 1.0)], "pools": {},
                           "activities": {"A": {"minutes": 5, "resources": 1}}, "distribution": "fixed",
                           "cases": 100, "seed": 1, "calendar": calendar, "arrival_pattern": pattern})
    assert sum(result["arrivals_by_local_hour"][9:17]) == 100
    with pytest.raises(workspace.EventLogWorkspaceError, match="supported date range"):
        sim.what_if(_log(), {"cases": 100, "arrivals_per_hour": 1e-6, "distribution": "fixed",
                             "calendar": {"timezone": "UTC", "hours": {"mon": [["09:00", "17:00"]]},
                                          "start": "2200-01-01T00:00:00"}})


def test_measured_patterns_handle_case_starts_before_1970():
    zone, rows = ZoneInfo("UTC"), ["case,activity,time"]
    for d in range(20):
        start = datetime(1969, 12, 20, 10, tzinfo=zone) + timedelta(days=d)
        rows += [f"c{d},A,{start:%Y-%m-%dT%H:%M:%SZ}", f"c{d},B,{start + timedelta(minutes=5):%Y-%m-%dT%H:%M:%SZ}"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="old", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    pattern = sim.what_if(log, {"cases": 100, "arrival_pattern": {"from_log": True}})["scenario"]["arrival_pattern"]
    assert pattern["hourly_factors"][10] == pytest.approx(24) and pattern["basis"]["case_starts"] == 20


def test_real_http_route_accepts_an_arrival_pattern(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-01T{8 + i // 6:02d}:{(i % 6) * 10 + n * 2:02d}:00Z"
                                 for i in range(30) for n, a in enumerate(["A", "B"])]
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                       content="\n".join(rows).encode(), headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    ok = client.post(base + "/simulate", json={"cases": 200, "arrival_pattern": {"hourly": [0] * 9 + [1] * 8 + [0] * 7}},
                     headers=headers)
    assert ok.status_code == 200, ok.text
    assert sum(ok.json()["result"]["arrivals_by_local_hour"][9:17]) == 200
    assert client.post(base + "/simulate", json={"cases": 200, "arrival_pattern": {"hourly": [1]}},
                       headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
