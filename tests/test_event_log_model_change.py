"""Model change: discovered models before and after a split date."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_model_change as mc
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """Until March: Order, Check, Approve, Ship. From March: Order, Approve, Check, Ship, and Notify is new."""
    rows = ["case,activity,time"]
    for i in range(20):
        for j, a in enumerate(["Order", "Check", "Approve", "Ship"]):
            rows.append(f"a{i},{a},2024-01-{1 + i:02d}T0{j}:00:00Z")
    for i in range(20):
        for j, a in enumerate(["Order", "Approve", "Check", "Ship", "Notify"]):
            rows.append(f"b{i},{a},2024-03-{1 + i:02d}T0{j}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="c", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_orderings_activities_and_cross_fit_change():
    log = _log()
    r = mc.model_change(log, split="2024-02-15", noise=0)
    assert r["periods"]["before"]["cases"] == 20 and r["periods"]["after"]["cases"] == 20
    assert r["activities"]["only_after"] == ["Notify"] and r["activities"]["only_before"] == []
    changed = {(c["from"], c["to"]): (c["before"], c["after"]) for c in r["changes"]}
    assert changed[("Approve", "Check")] == ("←", "→")
    cross = r["cross_fit"]
    assert cross["before_cases_on_before_model"]["fitness"] == 1.0
    assert cross["after_cases_on_after_model"]["fitness"] == 1.0
    assert cross["after_cases_on_before_model"]["fitness"] < 1.0
    assert r["fitness_drop_on_earlier_model"] > 0


def test_median_split_and_refusals():
    log = _log()
    r = mc.model_change(log)
    assert r["split_chosen"] == "median case start" and r["split"].startswith("2024-03-01")
    with pytest.raises(EventLogWorkspaceError, match="each period needs"):
        mc.model_change(log, split="2024-01-05")
    with pytest.raises(EventLogWorkspaceError, match="split must be"):
        mc.model_change(log, split="soon")
    with pytest.raises(EventLogWorkspaceError, match="start-date filter"):
        mc.model_change(log, split="2024-02-15", cohort={"started_from": "2024-01-01T00:00:00Z"})
