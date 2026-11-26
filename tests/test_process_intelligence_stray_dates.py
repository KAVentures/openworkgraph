"""Agent data quality counts events dated far outside the bulk of the log and suggests a filter."""
from __future__ import annotations

from io import BytesIO

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_stray_dates_are_counted_and_filtered_without_dropping_real_cases():
    rows = ["case,activity,time,who"]
    for i in range(200):
        day = 1 + i % 28
        rows += [f"c{i},A,2024-03-{day:02d}T08:00:00Z,x", f"c{i},B,2024-03-{day:02d}T09:00:00Z,x"]
    rows += ["old,A,1948-01-05T08:00:00Z,x", "old,B,1948-01-06T08:00:00Z,x"]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="stray", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    found = sharing.data_quality(log_id)["stray_dates"]
    assert (found["events_far_before"], found["cases_far_before"], found["events_far_after"]) == (2, 1, 0)
    assert "1948" not in str(found)
    kept = workspace.summary(log_id, None, found["suggested_case_filter"])["cases"]
    assert kept == 200


def test_clean_logs_report_no_stray_dates():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    found = sharing.data_quality(log_id)["stray_dates"]
    assert found["events_far_before"] == found["events_far_after"] == 0 and "suggested_case_filter" not in found
