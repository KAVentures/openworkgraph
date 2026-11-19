"""Signals on adherence to a frozen reference net."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_signals as signals
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _sequence_net

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()


def _log():
    """January: 10 cases follow A, B, C. March: 5 of 10 cases skip B."""
    rows = ["case,activity,time"]
    for i in range(10):
        for j, a in enumerate(["A", "B", "C"]):
            rows.append(f"j{i},{a},2024-01-{1 + i:02d}T0{j}:00:00Z")
    for i in range(10):
        for j, a in enumerate(["A", "C"] if i % 2 else ["A", "B", "C"]):
            rows.append(f"m{i},{a},2024-03-{1 + i:02d}T0{j}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _rule(**when):
    return {"name": "Adherence", "when": {"kind": "conformance", "net_name": "A-B-C",
                                          "pnml": _sequence_net(["A", "B", "C"]).decode(), **when}}


def test_share_of_cases_not_fitting_and_recent_window():
    log = _log()
    signals.add_rule(log, _rule(metric="non_fitting_share", operator="above", threshold=0.2))
    signals.add_rule(log, _rule(metric="non_fitting_share", operator="above", threshold=0.4, recent_days=20))
    signals.add_rule(log, _rule(metric="alignment_fitness", operator="below", threshold=0.96))
    r = signals.evaluate(log)
    whole, recent, fitness = r["signals"]
    assert whole["value"] == 0.25 and whole["triggered"] and whole["conformance"]["cases"] == 20
    assert recent["conformance"]["cases"] == 10 and recent["value"] == 0.5 and recent["triggered"]
    assert fitness["value"] == round(1 - 5 / (10 * 6 + 5 * 6 + 5 * 5), 4) and fitness["triggered"]
    assert "pnml" not in whole["when"]
    again = signals.evaluate(log)["signals"][0]
    assert again["changed_since_last_evaluation"] is False


def test_minimum_cases_and_bad_nets():
    log = _log()
    signals.add_rule(log, _rule(metric="non_fitting_share", operator="above", threshold=0.1, minimum_cases=50))
    assert signals.evaluate(log)["signals"][0]["triggered"] is False
    with pytest.raises(EventLogWorkspaceError):
        signals.add_rule(log, _rule(metric="non_fitting_share", operator="above", threshold=1.5))
    dead = _sequence_net(["A", "B"]).replace(b'<arc id="a1o" source="t1" target="p2"/>', b"").decode()
    bad = _rule(metric="non_fitting_share", operator="above", threshold=0.1)
    bad["when"]["pnml"] = dead
    with pytest.raises(EventLogWorkspaceError, match="final marking"):
        signals.add_rule(log, bad)
    bad["when"]["pnml"] = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>'
    with pytest.raises(EventLogWorkspaceError, match="DOCTYPE"):
        signals.add_rule(log, bad)


def test_agents_see_the_rule_without_the_net():
    from server import event_log_agent_sharing as sharing
    log = _log()
    signals.add_rule(log, _rule(metric="non_fitting_share", operator="above", threshold=0.2))
    signals.evaluate(log)
    sharing.set_share(log, "aggregates")
    listed = sharing.signals(log)
    assert "pnml" not in str(listed) and "non_fitting_share" in str(listed)
