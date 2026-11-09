"""R19: Activity-state prediction diagnostics with case-level uncertainty and bounded disclosure."""
from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path
import random

import duckdb
import pytest

from server import event_log_prediction_monitoring as monitoring
from server import event_log_workspace as workspace
from test_event_log_prediction_monitoring import MAPPING, SPEC, _log, _steady


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _rows_conn():
    conn = duckdb.connect(":memory:")
    conn.execute("""CREATE TABLE pt_est (
      case_id VARCHAR, activity VARCHAR, basis VARCHAR,
      remaining DOUBLE, q10 DOUBLE, q50 DOUBLE, q90 DOUBLE,
      naive_q50 DOUBLE, rate DOUBLE, outcome BOOLEAN, base_rate DOUBLE
    )""")
    return conn


def _add(conn, case_id, activity, *, bad=False):
    conn.execute("INSERT INTO pt_est VALUES (?, ?, 'same_activity', ?, 0, 5, 10, 6, 0.6, TRUE, 0.5)",
                 [case_id, activity, 20 if bad else 5])


def test_repeated_prefixes_have_one_case_of_weight_per_activity():
    conn = _rows_conn()
    try:
        for i in range(20):
            for _ in range(100 if i == 0 else 1):
                _add(conn, f"case_{i}", "Review", bad=i == 0)
        # Two cases, far below the required 20, must never expose their activity label.
        _add(conn, "hidden_1", "Low volume A")
        _add(conn, "hidden_2", "Low volume A")
        report = monitoring._activity_quality(conn, True)
        assert report["total_scored_activity_states"] == 2
        assert report["withheld_low_support_states"] == 1
        assert report["withheld_over_limit_states"] == 0
        assert len(report["activities"]) == 1
        row = report["activities"][0]
        assert row["activity"] == "Review"
        assert row["scored_cases"] == 20
        assert row["scored_prefixes"] == 119
        assert row["p10_p90_case_averaged_coverage"] == pytest.approx(19 / 20)
        assert row["mean_absolute_error_seconds"] == pytest.approx(0.75)
        assert row["naive_mean_absolute_error_seconds"] == pytest.approx(1.65)
        assert row["case_averaged_brier_score"] == pytest.approx(0.16)
        assert row["case_averaged_base_rate_brier_score"] == pytest.approx(0.25)
        assert 0 <= row["p10_p90_coverage_ci95"][0] < 1
        assert row["p10_p90_coverage_ci95"][1] <= 1
        assert "Low volume" not in str(report)
        assert report["no_case_or_resource_identifiers"] is True
        assert "case_0" not in str(report)
    finally:
        conn.close()


def test_maximum_eight_states_are_deterministic_and_remainder_is_disclosed():
    conn = _rows_conn()
    try:
        for i in range(11):
            for j in range(20):
                _add(conn, f"id-{i}-{j}", f"Stage {i:02d}")
        rows = monitoring._activity_quality(conn, False)
        assert rows["total_scored_activity_states"] == 11
        assert rows["withheld_low_support_states"] == 0
        assert rows["withheld_over_limit_states"] == 3
        assert [a["activity"] for a in rows["activities"]] == [
            f"Stage {i:02d}" for i in range(8)]
        assert all("case_averaged_brier_score" not in r for r in rows["activities"])
    finally:
        conn.close()


def test_sparse_window_never_exposes_small_activity_state():
    # 1200 completed cases supply enough history to score several windows;
    # add a rare state to just two cases in the latest period.
    from datetime import datetime, timedelta, timezone
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rng = random.Random(11)
    lines = ["case,activity,time"]
    for i in range(1200):
        t = start + timedelta(hours=4*i)
        a = rng.uniform(1, 1.2)
        lines.extend([
            f"c{i},Open,{t.isoformat()}",
            f"c{i},Work,{(t + timedelta(hours=a)).isoformat()}",
        ])
        if i in (1198,1199):
            lines.append(f"c{i},Sensitive Rare Stage,{(t + timedelta(hours=a+0.2)).isoformat()}")
        lines.append(f"c{i},Close,{(t + timedelta(hours=a+0.4)).isoformat()}")
    log_id = workspace.import_log(BytesIO(("\n".join(lines)+"\n").encode()), name="rare",
                                   source_format="csv", mapping=MAPPING)["log_id"]
    result = monitoring.monitor(log_id, SPEC)
    report = result["activity_quality"]
    assert report is not None and report["window"] == result["windows"][-1]["window"]
    assert report["withheld_low_support_states"] >= 1
    assert all(x["activity"] != "Sensitive Rare Stage" for x in report["activities"])
    assert len(report["activities"]) >= 1
    assert result["resources_used"] is False


def test_monitoring_activity_quality_is_review_only_and_live_predictions_unchanged():
    from server import event_log_predictions as predictions
    log_id = _log(3, _steady, cases=300)
    original = predictions.predict(log_id, {"end_activities": ["Close"]})
    report = monitoring.monitor(log_id, {"end_activities": ["Close"], "windows": 3,
                                         "min_support": 5})
    state = report["activity_quality"]
    assert state and state["window"] >= 1 and state["activities"]
    assert state["minimum_distinct_completed_cases"] == 20
    assert state["maximum_displayed_states"] == 8
    assert state["unit_of_uncertainty"] == "completed_case_within_activity_not_correlated_prefix"
    assert all(x["scored_cases"] >= 20 for x in state["activities"])
    for x in state["activities"]:
        lo, hi = x["p10_p90_coverage_ci95"]
        assert math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= hi <= 1
    assert predictions.predict(log_id, {"end_activities": ["Close"]})["predictions"] == original["predictions"]
    dashboard = (Path(__file__).resolve().parents[1] / "dashboard" / "event_log_workspace.js").read_text(encoding="utf-8")
    assert "Where prediction errors occur" in dashboard
    assert "withheld_low_support_states" in dashboard


def test_no_scored_window_yields_no_activity_quality():
    log_id = _log(9, _steady, cases=25)
    report = monitoring.monitor(log_id, {"end_activities": ["Close"], "windows": 12,
                                         "min_support": 5})
    assert report["activity_quality"] is None
