"""Agents can look for process changes within one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import _dirs  # noqa: F401  (autouse fixture)


def test_drift_within_one_group_finds_that_groups_change():
    rows = ["case,activity,time,region"]
    for month in (1, 2, 3, 4):
        for i in range(80):
            region = "EU" if i % 2 else "US"
            changed = region == "EU" and month >= 3
            path = ("A", "X", "C") if changed else ("A", "B", "C")
            rows += [f"m{month}c{i},{act},2024-{month:02d}-{1 + i % 25:02d}T{8 + n:02d}:00:00Z,{region}"
                     for n, act in enumerate(path)]
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
               "attribute_columns": ["region"]}
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="d", source_format="csv",
                                  mapping=mapping)["log_id"]
    sharing.set_share(log_id, "aggregates")
    eu = sharing.drift(log_id, case_filter={"event_attribute": "region=EU"})
    us = sharing.drift(log_id, case_filter={"event_attribute": "region=US"})
    assert eu["change_points"] and not us["change_points"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="fewer than 5 cases"):
        sharing.drift(log_id, case_filter={"started_before": "2024-01-02T00:00:00Z"})
