"""Agents can run a milestone funnel for one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs  # noqa: F401  (autouse fixture)


def _funnel_log():
    rows = ["case,activity,time,who"]
    for i in range(30):
        who = "eu" if i % 3 else "us"
        rows.append(f"c{i},Receive,2024-03-{1 + i % 25:02d}T08:00:00Z,{who}")
        if who == "eu" or i % 2:
            rows.append(f"c{i},Pay,2024-03-{1 + i % 25:02d}T10:00:00Z,{who}")
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    return log_id


def _reached(result):
    return [s["cases"] for s in result["steps"]]


def test_funnel_within_one_group():
    log_id = _funnel_log()
    everyone = sharing.funnel(log_id, ["Receive", "Pay"])
    eu = sharing.funnel(log_id, ["Receive", "Pay"], case_filter={"resource": "eu"})
    assert _reached(everyone) == [30, 25]
    assert _reached(eu) == [20, 20]
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.funnel(log_id, ["Receive", "Pay"], case_filter={"case_ids": ["c0"]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.funnel(log_id, ["Receive", "Pay"], case_filter={})
