"""Working calendars for what-if simulation: hours, holidays and daylight saving (#370 R14)."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv

ROOT = Path(__file__).resolve().parents[1]
WEEKDAYS = {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
H = 3600


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _calendar(start, zone="UTC", hours=WEEKDAYS, holidays=()):
    return sim.parse_calendar({"timezone": zone, "hours": hours, "holidays": list(holidays), "start": start})


def test_work_pauses_overnight_and_over_the_weekend():
    cal = _calendar("2025-01-10T16:30:00")                       # a Friday
    assert cal.finish(0, 1 * H) == pytest.approx((24 * 2 + 17) * H)       # 30 min Friday + 30 min Monday → Mon 09:30
    assert cal.open_at(0) == 0 and cal.open_at(H) == pytest.approx((24 * 2 + 16.5) * H)   # Fri 17:30 → Mon 09:00
    assert cal.working_seconds(0, 3 * 24 * H) == pytest.approx(0.5 * H + 7.5 * H)   # to Mon 16:30
    assert cal.weekly_share == pytest.approx(40 / 168)


def test_daylight_saving_follows_the_time_zone_and_holidays_close_whole_days():
    berlin = _calendar("2025-03-28T16:00:00", zone="Europe/Berlin")     # Friday before clocks go forward
    assert berlin.finish(0, 2 * H) == pytest.approx(65 * H)    # Mon 10:00 CEST is 65 h later, not 66
    utc = _calendar("2025-03-28T16:00:00")
    assert utc.finish(0, 2 * H) == pytest.approx(66 * H)
    holiday = _calendar("2025-01-10T16:30:00", holidays=["2025-01-13"])
    assert holiday.finish(0, 1 * H) == pytest.approx((24 * 3 + 17) * H)   # Monday closed → Tue 09:30


def _single(calendar, rate=0.5, minutes=60, cases=20000, distribution="exponential"):
    return sim.simulate({"label": "", "arrivals_per_hour": rate, "paths": [(["A"], 1.0)],
                         "activities": {"A": {"minutes": minutes, "resources": 1}},
                         "distribution": distribution, "cases": cases, "seed": 3, "calendar": calendar})


def test_a_round_the_clock_calendar_matches_the_uncalendared_engine():
    always = _calendar("2025-01-06T00:00:00", hours={d: [["00:00", "24:00"]] for d in sim.DAYS})
    plain, calendared = _single(None), _single(always)
    assert calendared["cycle_time_seconds"]["mean"] == pytest.approx(plain["cycle_time_seconds"]["mean"], rel=1e-6)
    assert calendared["activities"][0]["utilization"] == pytest.approx(plain["activities"][0]["utilization"], rel=1e-6)
    assert "calendar" not in plain and calendared["utilization_basis"] == "working_time"


def test_utilization_and_offered_load_are_measured_against_working_time():
    half = _calendar("2025-01-06T00:00:00", hours={d: [["00:00", "12:00"]] for d in sim.DAYS})
    result = _single(half, rate=0.25, distribution="fixed")
    a = result["activities"][0]
    assert a["offered_load"] == pytest.approx(0.5) and a["utilization"] == pytest.approx(0.5, abs=0.04)
    assert result["unstable"] is False
    overloaded = _single(half, rate=0.6, distribution="fixed", cases=500)
    assert overloaded["unstable"] is True and overloaded["activities"][0]["offered_load"] == pytest.approx(1.2)


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_what_if_shares_the_calendar_and_a_scenario_can_add_a_shift():
    log = _log()
    calendar = {"timezone": "Europe/Berlin", "hours": WEEKDAYS}
    saturday = {**calendar, "hours": {**WEEKDAYS, "sat": [["09:00", "17:00"]]}}
    result = sim.what_if(log, {"label": "Saturday shift", "cases": 2000, "calendar": calendar,
                               "scenario_calendar": saturday})
    assert result["baseline_calendar"]["hours"] == {d: [["09:00", "17:00"]] for d in WEEKDAYS}
    assert "sat" in result["scenario"]["calendar"]["hours"]
    assert result["median_cycle_time_change_seconds"] < 0
    assert any("pauses outside them" in line for line in result["assumptions"])
    assert not any(line.startswith("Resources work around the clock") for line in result["assumptions"])
    plain = sim.what_if(log, {"cases": 500})
    assert plain["baseline_calendar"] is None and plain["scenario"]["calendar"] is None
    assert any(line.startswith("Resources work around the clock") for line in plain["assumptions"])


@pytest.mark.parametrize("calendar", [
    "x", {"hours": WEEKDAYS}, {"timezone": "Mars/Base", "hours": WEEKDAYS}, {"timezone": "../etc", "hours": WEEKDAYS},
    {"timezone": "UTC", "hours": {}}, {"timezone": "UTC", "hours": {"monday": [["09:00", "17:00"]]}},
    {"timezone": "UTC", "hours": {"mon": [["17:00", "09:00"]]}}, {"timezone": "UTC", "hours": {"mon": [["9:00", "17:00"]]}},
    {"timezone": "UTC", "hours": {"mon": [["09:00", "13:00"], ["12:00", "17:00"]]}},
    {"timezone": "UTC", "hours": {"mon": []}}, {"timezone": "UTC", "hours": WEEKDAYS, "holidays": ["25/12/2025"]},
    {"timezone": "UTC", "hours": WEEKDAYS, "start": "soon"}, {"timezone": "UTC", "hours": WEEKDAYS, "shift": 1},
    {"timezone": "UTC", "hours": WEEKDAYS, "holidays": [[1]]}, {"timezone": "UTC", "hours": WEEKDAYS, "holidays": [20250101]},
    {"timezone": "UTC", "hours": {"mon": [["02:00", "03:00"]]}, "start": 5}, {"timezone": 5, "hours": WEEKDAYS},
])
def test_invalid_calendars_are_refused(calendar):
    with pytest.raises(workspace.EventLogWorkspaceError):
        sim.parse_calendar(calendar)


def test_real_http_route_accepts_a_calendar(tmp_path):
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
    calendar = {"timezone": "America/New_York", "hours": {"mon": [["09:00", "17:00"]]}, "holidays": ["2025-01-13"]}
    ok = client.post(base + "/simulate", json={"cases": 200, "calendar": calendar}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["result"]["calendar"]["timezone"] == "America/New_York"
    bad = client.post(base + "/simulate", json={"cases": 200, "calendar": {**calendar, "timezone": "Nowhere"}}, headers=headers)
    assert bad.status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_periods_inside_a_daylight_saving_gap_never_stall():
    gap = _calendar("2025-03-29T00:00:00", zone="Europe/Berlin", hours={d: [["02:00", "03:00"]] for d in sim.DAYS})
    done = gap.finish(0, 2 * H)          # the 02:00–03:00 period does not exist on 30 March
    assert done == pytest.approx(50 * H)   # Sat 02:00–03:00 CET, then Mon 02:00–03:00 CEST (01:00 UTC)


SPRING = "2025-03-30T00:00:00"      # Berlin: 02:00 CET jumps to 03:00 CEST
AUTUMN = "2025-10-26T00:00:00"      # Berlin: 03:00 CEST falls back to 02:00 CET


def _sunday(start, span):
    return _calendar(start, zone="Europe/Berlin", hours={"sun": [span]})


@pytest.mark.parametrize("span,real_seconds,first_local", [
    (["02:10", "02:20"], 0, None),                    # wholly inside the skipped hour
    (["02:00", "03:00"], 0, None),                    # exactly the skipped hour
    (["02:30", "03:00"], 0, None),                    # ends where the skipped hour ends
    (["02:30", "04:00"], 1 * H, "03:00"),             # starts inside it: work begins at the jump
    (["01:00", "04:00"], 2 * H, "01:00"),             # crosses it: two real hours
])
def test_skipped_hour_periods_have_their_real_length_and_a_consistent_start(span, real_seconds, first_local):
    from datetime import datetime
    cal = _sunday(SPRING, span)
    assert cal.working_seconds(0, 23 * H) == pytest.approx(real_seconds)
    opened = cal.open_at(0)
    local = datetime.fromtimestamp(cal.origin + opened, cal.tz)
    if first_local is None:
        assert local.date().isoformat() == "2025-04-06"            # nothing that day: next Sunday
        assert local.strftime("%H:%M") == span[0]
    else:
        assert local.date().isoformat() == "2025-03-30" and local.strftime("%H:%M") == first_local
    # Work started at open_at is done inside that same period, never before it or a week later.
    assert cal.finish(opened, 60) == pytest.approx(opened + 60)


@pytest.mark.parametrize("span,real_seconds", [
    (["02:00", "03:00"], 2 * H),          # the repeated hour counts both times
    (["01:00", "02:30"], 2.5 * H),        # ends at the second 02:30
    (["02:30", "02:45"], 1.25 * H),       # first 02:30 (CEST) to the last 02:45 (CET)
])
def test_repeated_hour_periods_run_from_first_to_last_occurrence(span, real_seconds):
    assert _sunday(AUTUMN, span).working_seconds(0, 23 * H) == pytest.approx(real_seconds)


def test_every_interval_near_both_clock_changes_is_usable():
    import itertools
    clocks = [f"{h:02d}:{m:02d}" for h in range(0, 5) for m in (0, 15, 30, 45)] + ["24:00"]
    for start, (a, b) in itertools.product((SPRING, AUTUMN), itertools.combinations(clocks, 2)):
        cal = _sunday(start, [a, b])
        for t in (0, 1.5 * H, 2.25 * H, 3 * H):
            opened = cal.open_at(t)
            assert opened >= t
            lo, hi = cal._next(opened)
            assert lo <= opened < hi and hi > lo, (start, a, b, t)
            assert cal.finish(opened, 1) <= hi
        assert cal.working_seconds(0, 30 * 24 * H) >= 0


def test_calendars_work_without_a_system_time_zone_database():
    """Windows and slim images have no IANA database; the base install's tzdata must cover it."""
    import tomllib
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.split(">")[0].split("=")[0].strip() == "tzdata" and ";" not in d for d in deps), deps
    code = ("import zoneinfo; assert zoneinfo.TZPATH == (), zoneinfo.TZPATH\n"
            "from server.event_log_simulation import parse_calendar\n"
            "cal = parse_calendar({'timezone': 'Europe/Berlin', 'start': '2025-03-28T16:00:00',"
            " 'hours': {d: [['09:00', '17:00']] for d in ('mon', 'tue', 'wed', 'thu', 'fri')}})\n"
            "assert cal.finish(0, 7200) == 65 * 3600, cal.finish(0, 7200)\n")
    env = {**os.environ, "PYTHONTZPATH": ""}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
