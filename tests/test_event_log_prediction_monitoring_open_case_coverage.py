"""R19: open-case exclusions alongside completed-case prediction monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_prediction_monitoring as monitoring
from server import event_log_predictions as predictions
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
SPEC = {"end_activities": ["Close"], "windows": 3, "min_support": 20}
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(*, open_work: int, open_review: int):
    rows = ["case,activity,time"]
    for case in range(180):
        t = START + timedelta(hours=4 * case)
        for activity, delta in (("Open", 0), ("Work", 1), ("Close", 2)):
            rows.append(f"closed-{case},{activity},{(t+timedelta(hours=delta)).isoformat()}")
    for kind, total in (("Work", open_work), ("Review", open_review)):
        for case in range(total):
            t = START + timedelta(days=50, hours=case)
            rows.append(f"{kind}-open-{case},Open,{t.isoformat()}")
            rows.append(f"{kind}-open-{case},{kind},{(t+timedelta(minutes=30)).isoformat()}")
    return workspace.import_log(
        BytesIO(("\n".join(rows) + "\n").encode("utf-8")), name="censoring",
        source_format="csv", mapping=MAPPING
    )["log_id"]


def test_open_cases_excluded_and_supported_only_by_completed_case_analogs():
    log = _log(open_work=5, open_review=7)
    result = monitoring.monitor(log, SPEC)
    coverage = result["open_case_coverage"]
    assert result["method"]["open_case_coverage"].startswith("descriptive census")
    assert coverage["status"] == "exact_state_fallback_needed"
    assert coverage["completed_cases_in_selected_cohort"] == 180
    assert coverage["still_open_cases_not_scored"] == 12
    assert coverage["selected_cases_total"] == 192
    assert coverage["still_open_share"] == pytest.approx(12/192, abs=1e-6)
    assert coverage["open_cases_with_exact_state_support"] == 5
    assert coverage["open_cases_without_exact_state_support"] == 7
    assert coverage["open_cases_with_no_exact_completed_prefix"] == 7
    assert coverage["minimum_completed_examples_for_exact_state"] == 20
    assert coverage["distinct_open_states"] == 2
    by_name = {r["latest_activity"]: r for r in coverage["open_states"]}
    assert by_name["Work"] == {"latest_activity": "Work", "step": 2,
                               "open_cases": 5, "exact_completed_prefix_cases": 180,
                               "exact_state_supported": True}
    assert by_name["Review"] == {"latest_activity": "Review", "step": 2,
                                 "open_cases": 7, "exact_completed_prefix_cases": 0,
                                 "exact_state_supported": False}
    assert coverage["open_cases_contributed_outcome_labels"] is False
    assert coverage["censoring_adjustment_applied"] is False
    assert coverage["causal_or_future_accuracy_claim"] is False
    assert all("case_id" not in r and "resource" not in r for r in coverage["open_states"])
    assert "Work-open-0" not in str(coverage)
    assert "closed-0" not in str(coverage)
    assert "Review-open-0" not in str(coverage)
    assert sum(1 for w in result["windows"] if w["scored"]) >= 2


def test_no_open_cases_are_explicit_not_misleadingly_supported():
    log = _log(open_work=0, open_review=0)
    report = monitoring.monitor(log, SPEC)["open_case_coverage"]
    assert report["status"] == "no_open_cases"
    assert report["selected_cases_total"] == report["completed_cases_in_selected_cohort"] == 180
    assert report["still_open_cases_not_scored"] == 0
    assert report["still_open_share"] == 0
    assert report["open_cases_with_exact_state_support"] == 0
    assert report["open_states"] == []
    assert not report["states_truncated"]


def test_open_coverage_is_observation_only_and_does_not_change_live_predictions():
    log = _log(open_work=5, open_review=7)
    before = predictions.predict(log, {"end_activities": ["Close"]})
    observed = monitoring.monitor(log, SPEC)
    after = predictions.predict(log, {"end_activities": ["Close"]})
    assert observed["open_case_coverage"]["still_open_cases_not_scored"] == 12
    assert before["predictions"] == after["predictions"]
    assert before["open_cases"] == after["open_cases"] == 12


def test_dashboard_discloses_unscored_open_cases_and_fallback_limits():
    ui = (ROOT / "dashboard/event_log_workspace.js").read_text(encoding="utf-8")
    assert "Still-open cases excluded from monitoring" in ui
    assert "open_case_coverage" in ui
    assert "open_cases_with_exact_state_support" in ui
    assert "open_cases_without_exact_state_support" in ui
    assert "...openCoverageDetail" in ui
