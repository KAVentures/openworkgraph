"""Agents can list stuck and slowest cases within one group of cases."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs  # noqa: F401  (autouse fixture)


def _log():
    rows = ["case,activity,time,who"]
    for i in range(60):
        who = "eu" if i % 3 else "us"
        day = 1 + i % 20
        rows.append(f"c{i},Receive,2024-03-{day:02d}T08:00:00Z,{who}")
        hours = 9 + (i % 7) + (5 if who == "us" else 0)
        if i not in (4, 5):  # two eu cases never finish
            rows.append(f"c{i},Pay,2024-03-{day:02d}T{hours:02d}:00:00Z,{who}")
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "cases")
    return log_id


def test_slowest_within_one_group():
    log_id = _log()
    everyone = sharing.slowest_cases(log_id, limit=50)
    eu = sharing.slowest_cases(log_id, limit=50, case_filter={"resource": "eu"})
    eu_ids = {f"c{i}" for i in range(60) if i % 3}
    assert {c["case_id"] for c in eu["cases"]} <= eu_ids
    assert len(eu["cases"]) == 40
    assert everyone["cases"][0]["case_id"] not in eu_ids  # the slowest overall are us cases
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.slowest_cases(log_id, case_filter={})


def test_stuck_within_one_group():
    log_id = _log()
    eu = sharing.stuck_cases(log_id, limit=100, case_filter={"resource": "eu"})
    us = sharing.stuck_cases(log_id, limit=100, case_filter={"resource": "us"})
    assert eu["cohort"] == {"resource": "eu"}
    assert {c["case_id"] for c in eu["cases"]} == {"c4", "c5"}
    assert us["open_cases"] == 0 and us["cases"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.stuck_cases(log_id, case_filter=["eu"])
