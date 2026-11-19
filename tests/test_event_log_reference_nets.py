"""Saved reference nets."""
from __future__ import annotations

import pytest

from server import event_log_reference_nets as rn
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _dirs, _import, _sequence_net  # noqa: F401  (fixture reuse)


def test_save_list_get_delete_and_erase_with_log():
    log = _import([["A", "B", "C"]] * 3)
    saved = rn.save(log, "Target A-B-C", _sequence_net(["A", "B", "C"]).decode())
    assert saved["places"] == 4 and saved["transitions"] == 3
    listed = rn.listing(log)["nets"]
    assert [n["name"] for n in listed] == ["Target A-B-C"] and "pnml" not in listed[0]
    name, xml = rn.get(log, saved["net_id"])
    assert name == "Target A-B-C" and "<pnml" in xml
    with pytest.raises(EventLogWorkspaceError, match="already exists"):
        rn.save(log, "Target A-B-C", _sequence_net(["A", "C"]).decode())
    rn.delete(log, saved["net_id"])
    assert rn.listing(log)["nets"] == []
    rn.save(log, "Again", _sequence_net(["A", "C"]).decode())
    workspace.delete_log(log)
    with pytest.raises(EventLogWorkspaceError):
        rn.listing(log)


def test_bad_nets_and_limits(monkeypatch):
    log = _import([["A", "B"]])
    with pytest.raises(EventLogWorkspaceError, match="DOCTYPE"):
        rn.save(log, "Bad", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')
    with pytest.raises(EventLogWorkspaceError, match="name"):
        rn.save(log, " ", _sequence_net(["A", "B"]).decode())
    monkeypatch.setattr(rn, "MAX_NETS", 1)
    rn.save(log, "One", _sequence_net(["A", "B"]).decode())
    with pytest.raises(EventLogWorkspaceError, match="at most 1"):
        rn.save(log, "Two", _sequence_net(["A", "B"]).decode())
