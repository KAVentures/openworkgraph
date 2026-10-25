"""Censoring-aware remaining-time predictions (#370 R19)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import math
import random

import duckdb
import pytest

from server import event_log_predictions as predictions
from server import event_log_survival as survival
from server import event_log_workspace as workspace
from test_event_log_predictions import _by_case, _log

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _reference_km(rows):
    """Textbook Kaplan–Meier: S drops by (1 - d/n) at each distinct death time."""
    out, s = [], 1.0
    for t in sorted({d for d, observed in rows if observed}):
        n = sum(1 for d, _ in rows if d >= t)
        deaths = sum(1 for d, observed in rows if observed and d == t)
        s *= 1 - deaths / n
        out.append((t, s))
        if s == 0:
            break
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_kaplan_meier_curves_match_the_textbook_product_limit(seed):
    rng = random.Random(seed)
    rows = [(float(rng.randint(1, 30)), rng.random() < 0.6) for _ in range(80)]
    if seed == 3:
        rows.append((31.0, True))                       # the last case finishes: the curve reaches zero
    conn = duckdb.connect()
    conn.execute("CREATE TEMP TABLE so (case_id VARCHAR, activity VARCHAR, step INTEGER, duration DOUBLE, observed BOOLEAN)")
    conn.executemany("INSERT INTO so VALUES (?, 'A', 1, ?, ?)", [(f"c{i}", d, o) for i, (d, o) in enumerate(rows)])
    survival.fit(conn)
    curve = conn.execute("SELECT t, s FROM km WHERE g = 0 ORDER BY t").fetchall()
    expected = _reference_km(rows)
    assert [t for t, _ in curve] == [t for t, _ in expected]
    assert [s for _, s in curve] == pytest.approx([s for _, s in expected], abs=1e-12)
    cases, observed, censored = conn.execute("SELECT cases, observed_cases, censored_cases FROM ks WHERE g = 0").fetchone()
    assert (cases, observed + censored) == (len(rows), len(rows))


def _exponential_log(cases=4000, per_hour=4, mean_hours=50.0, seed=7):
    """Cases start ``per_hour`` an hour and last an exponential time; the export ends when the last one starts."""
    rng, rows = random.Random(seed), ["case,activity,time"]
    horizon = START + timedelta(hours=cases / per_hour)
    for c in range(cases):
        begin = START + timedelta(hours=c / per_hour)
        end = begin + timedelta(hours=rng.expovariate(1 / mean_hours))
        rows.append(f"c{c},Receive,{begin:%Y-%m-%dT%H:%M:%SZ}")
        if end <= horizon:
            rows.append(f"c{c},Close,{end:%Y-%m-%dT%H:%M:%SZ}")
    rows.append(f"marker,Receive,{horizon:%Y-%m-%dT%H:%M:%SZ}")   # the export's end, an open case of its own
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="exp", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


@pytest.mark.parametrize("seed", [1, 7])
def test_open_cases_correct_the_bias_toward_short_remaining_times(seed):
    log = _exponential_log(seed=seed)
    true_median = 50 * math.log(2) * 3600                 # memoryless: the same from now for every open case
    spec = {"end_activities": ["Close"], "limit": 200}
    completed = predictions.predict(log, spec)
    censored = predictions.predict(log, {**spec, "estimator": "survival"})
    assert censored["estimator"] == "survival" and censored["censoring"]["open_cases_used_as_censored"] > 30
    assert censored["open_cases"] == completed["open_cases"] > 150
    assert {p["remaining_measured_from"] for p in censored["predictions"]} == {"end_of_log"}
    # Where at least a few comparable cases lasted as long, the estimate is within 15% of the truth.
    settled = [p["median_remaining_seconds"] for p in censored["predictions"] if p["idle_seconds"] < 100 * 3600]
    assert len(settled) > 150 and all(m == pytest.approx(true_median, rel=0.15) for m in settled)
    # Completed cases alone are biased short, and ignoring idle time makes long-open cases look nearly done.
    completed_medians = [max(p["median_remaining_seconds"] - p["idle_seconds"], 0) for p in completed["predictions"]]
    assert sum(completed_medians) / len(completed_medians) < 0.4 * true_median
    backtest = censored["backtest"]
    assert backtest["available"] and backtest["design"] == "as_of_replay" and backtest["scored_cases"] > 150
    assert backtest["scored_cases"] + backtest["still_open_unscored_cases"] + backtest["abstained_cases"] <= \
        backtest["cases_in_progress_at_cutoff"]
    assert backtest["improvement_over_completed_cases_estimator"] > 0.15
    assert 0.72 < backtest["p10_p90_coverage"] < 0.9
    assert backtest["p10_p90_coverage"] > backtest["completed_cases_estimator"]["p10_p90_coverage"] + 0.1


def test_estimates_are_withheld_when_too_many_comparable_cases_are_still_open():
    # 30 finished quickly; 60 later cases are all still open after 100 hours: their median is unknown.
    rows = ["case,activity,time"]
    for c in range(30):
        rows += [f"f{c},Receive,{START + timedelta(hours=c):%Y-%m-%dT%H:%M:%SZ}",
                 f"f{c},Close,{START + timedelta(hours=c, minutes=30):%Y-%m-%dT%H:%M:%SZ}"]
    for c in range(60):
        rows.append(f"o{c},Receive,{START + timedelta(hours=40 + c * 0.01):%Y-%m-%dT%H:%M:%SZ}")
    rows.append(f"end,Other,{START + timedelta(hours=140):%Y-%m-%dT%H:%M:%SZ}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="open", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    result = predictions.predict(log, {"end_activities": ["Close"], "estimator": "survival", "limit": 200})
    first = _by_case(result)["o0"]
    assert first["estimate_withheld"] is True and first["median_remaining_seconds"] is None
    # Each has been idle 100 h, as long as any comparable case is known to have lasted: no more is known.
    assert first["remaining_at_least_seconds"] == pytest.approx(0, abs=40) and first["comparable_cases_still_open"] == 60
    assert result["withheld_estimates"] == 61                    # the 60 and the end-of-export marker
    assert result["predictions"][0]["estimate_withheld"] is True     # withheld (longest) estimates come first
    # The completed-case estimator confidently says 30 minutes after the last event for all of them.
    plain = predictions.predict(log, {"end_activities": ["Close"], "limit": 200})
    assert _by_case(plain)["o0"]["median_remaining_seconds"] == pytest.approx(1800)


def test_idle_time_conditions_the_estimate():
    # Remaining times after Check are 1 h for half the cases and 30 h for the rest: an open case idle 2 h is a long one.
    rows = ["case,activity,time"]
    for c in range(100):
        t = START + timedelta(hours=c * 40)
        rows += [f"c{c},Check,{t:%Y-%m-%dT%H:%M:%SZ}",
                 f"c{c},Close,{t + timedelta(hours=1 if c % 2 else 30):%Y-%m-%dT%H:%M:%SZ}"]
    end = START + timedelta(hours=100 * 40 + 10)
    rows += [f"fresh,Check,{end:%Y-%m-%dT%H:%M:%SZ}", f"idle,Check,{end - timedelta(hours=2):%Y-%m-%dT%H:%M:%SZ}"]
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="idle", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    by_case = _by_case(predictions.predict(log, {"end_activities": ["Close"], "estimator": "survival"}))
    assert by_case["idle"]["median_remaining_seconds"] == pytest.approx(28 * 3600)   # it is a 30-hour case
    assert by_case["fresh"]["remaining_p10_seconds"] == pytest.approx(3600)


def test_survival_keeps_outcome_risk_and_the_default_estimator_is_unchanged():
    log = _log()
    default = predictions.predict(log, {"end_activities": ["Close"]})
    assert "estimator" not in default and default["backtest"].get("design") is None
    result = predictions.predict(log, {"end_activities": ["Close"], "estimator": "survival",
                                       "outcome": {"kind": "duration_over", "seconds": 10 * 3600}})
    fix = _by_case(result)["open-fix"]
    assert fix["outcome_probability"] == 1 and fix["median_remaining_seconds"] == pytest.approx(21 * 3600)
    assert "completed cases only" in result["interpretation"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="invalid"):
        predictions.predict(log, {"end_activities": ["Close"], "estimator": "neural"})
