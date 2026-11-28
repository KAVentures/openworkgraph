"""Agents can measure waiting and processing time within one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import _dirs  # noqa: F401  (autouse fixture)


def test_waiting_times_within_one_group():
    rows = ["case,activity,time,lifecycle:transition,region"]
    for i in range(12):
        hours = 1 if i % 2 else 3
        rows += [f"c{i},Review,2024-03-04T09:00:00Z,start,{'EU' if i % 2 else 'US'}",
                 f"c{i},Review,2024-03-04T{9 + hours:02d}:00:00Z,complete,{'EU' if i % 2 else 'US'}"]
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
               "attribute_columns": ["lifecycle:transition", "region"]}
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv",
                                  mapping=mapping)["log_id"]
    sharing.set_share(log_id, "aggregates")
    eu = sharing.waiting_times(log_id, case_filter={"event_attribute": "region=EU"})
    review = {a["activity"]: a for a in eu["activities"]}["Review"]
    assert review["processing"]["median_seconds"] == 3600
    with pytest.raises(workspace.EventLogWorkspaceError, match="fewer than 5 cases"):
        sharing.waiting_times(log_id, case_filter={"event_attribute": "region=UNKNOWN"})
