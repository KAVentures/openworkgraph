"""Agents can look for outliers within one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs  # noqa: F401  (autouse fixture)


def test_unusual_cases_within_one_group():
    rows = ["case,activity,time,who"]
    for i in range(60):
        who = "eu" if i % 3 else "us"
        rows += [f"c{i},Receive,2024-03-{1 + i % 25:02d}T08:00:00Z,{who}",
                 f"c{i},Pay,2024-03-{1 + i % 25:02d}T{9 + (5 if i == 7 else 0):02d}:00:00Z,{who}"]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="u", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    group = sharing.unusual_cases(log_id, case_filter={"resource": "eu"})
    assert group["cases"] == 40 and all("case_id" not in c for c in group["top"])
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.unusual_cases(log_id, case_filter={"case_ids": ["c0"]})
