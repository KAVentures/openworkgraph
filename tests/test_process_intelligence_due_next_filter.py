"""Agents can check due dates and walk paths for one group of cases."""
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
        if who == "eu":
            rows.append(f"c{i},Pay,2024-03-{day:02d}T10:00:00Z,{who}")
        else:
            rows.append(f"c{i},Remind,2024-03-{day:02d}T10:00:00Z,{who}")
            rows.append(f"c{i},Pay,2024-03-{day + 5:02d}T10:00:00Z,{who}")
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="d", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    return log_id


def test_next_steps_within_one_group():
    log_id = _log()
    everyone = sharing.next_steps(log_id, ["Receive"])
    eu = sharing.next_steps(log_id, ["Receive"], case_filter={"resource": "eu"})
    assert everyone["cases"] == 30 and eu["cases"] == 20
    nexts = {n["activity"] for n in eu["next"]}
    assert nexts == {"Pay"}
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.next_steps(log_id, ["Receive"], case_filter={"case_ids": ["c0"]})


def test_due_dates_within_one_group():
    log_id = _log()
    everyone = sharing.due_dates(log_id, "Pay", due_days=2)
    eu = sharing.due_dates(log_id, "Pay", due_days=2, case_filter={"resource": "eu"})
    assert everyone["cases"] == 30 and eu["cases"] == 20
    assert everyone["late_share_of_done"] == pytest.approx(1 / 3, abs=0.001)
    assert eu["late_share_of_done"] == 0
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.due_dates(log_id, "Pay", due_days=2, case_filter={})
