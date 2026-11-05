"""R19 case-aware comparison between prediction risks and historical base rates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_prediction_monitoring as monitoring
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(*, reversed_late: bool = True, cases: int = 800):
    """Balanced A/B categories. Flip the meaning only in the last quarter.

    The overall bad-outcome base rate remains exactly 50%, but the conditional
    prediction learned from earlier Mark A/B cases becomes wrong after the flip.
    Cases do not overlap, so the windows' completion order is unambiguous.
    """
    begin = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = ["case,activity,time"]
    for i in range(cases):
        t = begin + timedelta(hours=i * 48)
        state = "Mark A" if i % 2 == 0 else "Mark B"
        bad = (i % 2 == 0) != (reversed_late and i >= 3 * cases // 4)
        hours = 30 if bad else 2
        rows.extend((f"c{i},Open,{t.isoformat()}",
                     f"c{i},{state},{(t + timedelta(hours=1)).isoformat()}",
                     f"c{i},Close,{(t + timedelta(hours=hours)).isoformat()}"))
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="outcome quality",
                                source_format="csv", mapping={
                                    "case_column": "case", "activity_column": "activity",
                                    "timestamp_column": "time",
                                })["log_id"]


SPEC = {"end_activities": ["Close"], "windows": 3, "min_support": 5,
        "outcome": {"kind": "duration_over", "seconds": 12 * 3600}}


def test_later_reversed_outcome_labels_flag_worse_case_aware_brier_skill():
    result = monitoring.monitor(_log(), SPEC)
    rows = [w for w in result["windows"] if w["scored"]]
    assert len(rows) == 3
    first, latest = rows[0], rows[-1]
    assert first["outcome_quality"]["worse_than_base_rate"] is False
    assert latest["outcome_quality"]["worse_than_base_rate"] is True
    assert latest["brier_skill_vs_base_rate"] < 0
    paired = latest["outcome_quality"]
    assert paired["paired_cases"] >= 10
    assert paired["case_averaged_brier_excess"] > paired["material_excess_threshold"]
    assert paired["case_averaged_brier_excess_ci95"][0] > 0
    assert paired["unit_of_uncertainty"] == "completed_case_not_correlated_prefix"
    flags = [f for f in result["flags"] if "outcome risk model" in f["flag"]]
    assert len(flags) == 1 and flags[0]["window"] == latest["window"]
    assert flags[0]["case_averaged_brier_excess"] == paired["case_averaged_brier_excess"]
    assert flags[0]["review_only"] is True
    assert result["resources_used"] is False


def test_steady_conditional_risk_has_no_outcome_warning():
    result = monitoring.monitor(_log(reversed_late=False), SPEC)
    scored = [w for w in result["windows"] if w["scored"]]
    assert len(scored) == 3
    assert all(not w["outcome_quality"]["worse_than_base_rate"] for w in scored)
    assert all(f["flag"] != "outcome risk model performs worse than historical base rate"
               for f in result["flags"])


def test_no_outcome_spec_preserves_existing_monitoring_fields_and_flags():
    log = _log()
    plain = monitoring.monitor(log, {"end_activities": ["Close"], "windows": 3, "min_support": 5})
    for w in plain["windows"]:
        assert "outcome_quality" not in w and "brier_skill_vs_base_rate" not in w
    assert not any("outcome risk" in f["flag"] for f in plain["flags"])


def test_too_few_cases_never_produce_spurious_outcome_warnings():
    log = _log(cases=40)
    result = monitoring.monitor(log, {**SPEC, "windows": 12})
    assert not any(f["flag"] == "outcome risk model performs worse than historical base rate"
                   for f in result["flags"])
    assert all("outcome_quality" not in w for w in result["windows"] if not w["scored"])


def test_paired_loss_is_case_level_not_a_false_prefix_independent_confidence():
    """The summary averages within cases before deriving its normal interval."""
    import math

    class FakeCursor:
        def fetchall(self):
            return [(1.0,), (-1.0,)] * 5

    class FakeConn:
        def execute(self, sql):
            assert "GROUP BY case_id" in sql and "avg(" in sql
            return FakeCursor()

    outcome = monitoring._paired_outcome_quality(FakeConn())
    assert outcome["paired_cases"] == 10
    assert outcome["case_averaged_brier_excess"] == pytest.approx(0.0)
    assert outcome["worse_than_base_rate"] is False
    assert outcome["case_averaged_brier_excess_ci95"][0] < 0
    assert outcome["case_averaged_brier_excess_ci95"][1] > 0


def test_risk_warning_requires_material_and_positive_ci_lower_bound():
    class FakeConn:
        def __init__(self, values):
            self.values = values

        def execute(self, sql):
            class Cursor:
                def __init__(self, values):
                    self.values = values

                def fetchall(self):
                    return [(value,) for value in self.values]
            return Cursor(self.values)

    assert monitoring._paired_outcome_quality(FakeConn([0.01] * 10))["worse_than_base_rate"] is False
    assert monitoring._paired_outcome_quality(FakeConn([0.03] * 10))["worse_than_base_rate"] is True
    assert monitoring._paired_outcome_quality(FakeConn([0.8] * 5 + [-0.8] * 5))["worse_than_base_rate"] is False
    assert monitoring._paired_outcome_quality(FakeConn([0.3] * 9)) is None
