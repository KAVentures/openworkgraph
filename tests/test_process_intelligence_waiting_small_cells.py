"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_waiting_times_withhold_timings_resting_on_few_instances():
    rows = ["case,activity,time,lifecycle:transition"]
    for i in range(8):
        d = f"2024-03-{4 + i % 5:02d}"
        rows += [f"c{i},Review,{d}T09:00:00Z,start", f"c{i},Review,{d}T10:00:00Z,complete",
                 f"c{i},Approve,{d}T12:00:00Z,start", f"c{i},Approve,{d}T13:00:00Z,complete"]
        if i < 2:
            rows += [f"c{i},Escalate,{d}T15:00:00Z,start", f"c{i},Escalate,{d}T16:00:00Z,complete"]
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
               "attribute_columns": ["lifecycle:transition"]}
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv",
                                  mapping=mapping)["log_id"]
    sharing.set_share(log_id, "aggregates")
    cal = {"timezone": "UTC", "hours": {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}}
    for result in (sharing.waiting_times(log_id), sharing.waiting_times(log_id, working_calendar=cal)):
        by = {a["activity"]: a for a in result["activities"]}
        assert by["Escalate"]["processing"]["median_seconds"] is None
        assert by["Approve"]["processing"]["median_seconds"] == 3600
        assert all(h["count"] >= 5 for h in result["waiting_hotspots"])
        assert result["withheld"]["waiting_hotspots"] >= 1
