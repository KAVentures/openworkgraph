"""Agents can break down and profile an activity within one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs  # noqa: F401  (autouse fixture)


def _log():
    rows = ["case,activity,time,who"]
    for i in range(30):
        who = "eu" if i % 3 else "us"
        day = 1 + i % 20
        rows.append(f"c{i},Receive,2024-03-{day:02d}T08:00:00Z,{who}")
        if who == "us":
            rows.append(f"c{i},Remind,2024-03-{day:02d}T09:00:00Z,{who}")
        rows.append(f"c{i},Pay,2024-03-{day:02d}T10:00:00Z,{who}")
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="b", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    return log_id


def test_breakdown_within_one_group():
    log_id = _log()
    everyone = sharing.breakdown(log_id, "start_activity", with_activity="Remind")
    assert sum(t["cases"] for t in everyone["row_totals"]) == 30
    eu = sharing.breakdown(log_id, "start_activity", case_filter={"resource": "eu"})
    assert sum(t["cases"] for t in eu["row_totals"]) == 20
    with pytest.raises(workspace.EventLogWorkspaceError, match="Remind"):
        sharing.breakdown(log_id, "start_activity", with_activity="Remind", case_filter={"resource": "eu"})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.breakdown(log_id, "end_activity", case_filter={"case_ids": ["c0"]})


def test_activity_profile_within_one_group():
    log_id = _log()
    everyone = sharing.activity_profile(log_id, "Pay")
    eu = sharing.activity_profile(log_id, "Pay", case_filter={"resource": "eu"})
    assert everyone["cases"] == 30 and {a["activity"] for a in everyone["comes_after"]} == {"Receive", "Remind"}
    assert eu["cases"] == 20 and [a["activity"] for a in eu["comes_after"]] == ["Receive"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.activity_profile(log_id, "Pay", case_filter={})
