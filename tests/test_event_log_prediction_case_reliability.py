"""Independent completed-case outcome calibration for predictive monitoring (R19)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import random

import duckdb
import pytest

from server import event_log_prediction_monitoring as monitoring
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_cases_are_counted_once_even_when_they_have_many_prediction_points():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE pt_est(case_id VARCHAR, basis VARCHAR, rate DOUBLE, outcome BOOLEAN)")
        rows = []
        for i in range(25):
            rows += [(f"high{i}", "same_activity", 0.9, True)] * 8
        for i in range(30):
            rows += [(f"low{i}", "all_completed", 0.1, False)] * 2
        for i in range(3):
            rows += [(f"small{i}", "same_activity", 0.5, True)] * 100
        # Missing basis or label cannot silently enter the calibration.
        rows += [("unknown", None, 0.9, True), ("label_missing", "same_activity", 0.7, None)]
        # Contradictory outcome labels for one case must not be summarized as a certainty.
        rows += [("inconsistent", "same_activity", 0.5, True),
                 ("inconsistent", "same_activity", 0.5, False)]
        conn.executemany("INSERT INTO pt_est VALUES (?, ?, ?, ?)", rows)
        result = monitoring._case_outcome_reliability(conn)
    finally:
        conn.close()
    assert result["status"] == "ready"
    assert result["total_distinct_scored_cases"] == 58
    assert result["total_scored_prediction_points"] == 560
    assert result["withheld_low_support_bins"] == 1
    assert len(result["bins"]) == 2
    low, high = result["bins"]
    assert low["completed_cases"] == 30 and low["scored_prediction_points"] == 60
    assert low["observed_outcome_share"] == 0
    assert low["mean_case_averaged_predicted_risk"] == 0.1
    assert high["completed_cases"] == 25 and high["scored_prediction_points"] == 200
    assert high["observed_outcome_share"] == 1
    assert high["mean_case_averaged_predicted_risk"] == 0.9
    assert all(0 <= p <= 1 for b in result["bins"] for p in b["observed_outcome_ci95"])
    assert result["unit_of_uncertainty"] == "independent_completed_case_not_correlated_prefix"
    assert result["case_identity_or_resource_labels_included"] is False
    assert result["source_outcomes_independently_verified"] is False
    assert result["recalibration_applied"] is False


def test_no_band_passes_threshold_when_only_a_few_cases_have_many_prefixes():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE pt_est(case_id VARCHAR, basis VARCHAR, rate DOUBLE, outcome BOOLEAN)")
        conn.executemany("INSERT INTO pt_est VALUES (?, ?, ?, ?)",
                         [(f"c{i}", "support", 0.3, i % 2 == 0)
                          for i in range(5) for _ in range(200)])
        result = monitoring._case_outcome_reliability(conn)
    finally:
        conn.close()
    assert result["total_distinct_scored_cases"] == 5
    assert result["total_scored_prediction_points"] == 1000
    assert result["status"] == "insufficient_independent_case_support"
    assert result["bins"] == [] and result["withheld_low_support_bins"] == 1


def test_one_case_is_assigned_to_one_band_after_averaging_different_prefix_risks():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE pt_est(case_id VARCHAR, basis VARCHAR, rate DOUBLE, outcome BOOLEAN)")
        rows = []
        for i in range(25):
            rows += [(f"c{i}", "a", 0.1, False), (f"c{i}", "b", 0.5, False)]
        conn.executemany("INSERT INTO pt_est VALUES (?, ?, ?, ?)", rows)
        result = monitoring._case_outcome_reliability(conn)
    finally:
        conn.close()
    assert result["total_distinct_scored_cases"] == 25
    assert len(result["bins"]) == 1
    assert result["bins"][0]["predicted_from"] == 0.2
    assert result["bins"][0]["mean_case_averaged_predicted_risk"] == 0.3


def test_real_monitoring_includes_case_reliability_only_with_outcome():
    rng = random.Random(13)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = ["case,activity,time"]
    for i in range(650):
        t = start + timedelta(hours=4 * i)
        duration = 8 + rng.random() * 28
        rows.extend([f"c{i},Open,{t.isoformat()}",
                     f"c{i},Work,{(t + timedelta(hours=duration / 2)).isoformat()}",
                     f"c{i},Close,{(t + timedelta(hours=duration)).isoformat()}"])
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="calibration",
                                  source_format="csv", mapping={
                                      "case_column": "case", "activity_column": "activity",
                                      "timestamp_column": "time"})["log_id"]
    baseline = monitoring.monitor(log_id, {"end_activities": ["Close"], "windows": 5})
    assert baseline["outcome_case_reliability"] is None
    result = monitoring.monitor(log_id, {"end_activities": ["Close"], "windows": 5,
                                         "outcome": {"kind": "duration_over", "seconds": 24 * 3600}})
    quality = result["outcome_case_reliability"]
    assert quality is not None and 1 <= quality["window"] <= 5
    assert quality["total_distinct_scored_cases"] > 20
    assert quality["total_scored_prediction_points"] > quality["total_distinct_scored_cases"]
    assert quality["total_nonempty_bins"] >= len(quality["bins"])
    assert quality["minimum_distinct_completed_cases_per_displayed_bin"] == 20
    assert result["method"]["case_outcome_reliability_min_cases_per_bin"] == 20
    assert sum(b["completed_cases"] for b in quality["bins"]) <= quality["total_distinct_scored_cases"]
