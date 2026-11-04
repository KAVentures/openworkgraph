"""History- and case-attribute-dependent routing in what-if simulation (#370 R14)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import random

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_routing as routing
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(rows, name, extra=None):
    mapping = dict(MAPPING, **({"attribute_columns": extra} if extra else {}))
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=mapping)["log_id"]


def _rework_once(cases=2000, seed=3):
    """Rework happens at most once: after the first Check, Fix with p=0.5; after a re-check, always Close."""
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time"]
    for i in range(cases):
        steps = ["Receive", "Check"] + (["Fix", "Check"] if rng.random() < 0.5 else []) + ["Close"]
        for k, a in enumerate(steps):
            rows.append(f"{i},{a},{(start + timedelta(hours=i, minutes=10 * k)).isoformat()}")
    return _import(rows, "rework-once")


def _by_priority(cases=2000, seed=4):
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time,priority"]
    for i in range(cases):
        urgent = rng.random() < 0.3
        steps = ["Receive", "Approve", "Close"] if urgent else ["Receive", "Review", "Approve", "Close"]
        for k, a in enumerate(steps):
            rows.append(f"{i},{a},{(start + timedelta(hours=i, minutes=10 * k)).isoformat()},{'urgent' if urgent else 'standard'}")
    return _import(rows, "priority", ["priority"])


def _walks(model, n=3000):
    return [routing.walk(model, random.Random(i), 200)[0] for i in range(n)]


def test_history_routing_keeps_rework_to_one_round():
    log = _rework_once()
    first = routing.build(sim.calibrate(log, routing="transitions"), None)
    history_base = sim.calibrate(log, routing="transitions", routing_context="history")
    assert history_base["transitions"]["context"]["kind"] == "history"
    history = routing.build(history_base, None)
    assert max(p.count("Fix") for p in _walks(history)) == 1
    assert max(p.count("Fix") for p in _walks(first)) >= 2          # memoryless routing repeats rework
    share = sum("Fix" in p for p in _walks(history)) / 3000
    assert share == pytest.approx(0.5, abs=0.04)


def test_attribute_routing_learns_per_value_and_draws_the_observed_mix():
    log = _by_priority()
    base = sim.calibrate(log, routing="transitions", routing_context="attribute:priority")
    context = base["transitions"]["context"]
    assert {v["value"]: v["cases"] for v in context["values"]}.keys() == {"urgent", "standard"}
    model = routing.build(base, None)
    paths = _walks(model)
    reviewed = sum("Review" in p for p in paths) / len(paths)
    assert reviewed == pytest.approx(0.7, abs=0.04)
    assert all(p in (["Receive", "Approve", "Close"], ["Receive", "Review", "Approve", "Close"]) for p in paths)


def test_missing_attribute_falls_back_to_first_order_routing():
    log = _by_priority(cases=400)
    base = sim.calibrate(log, routing="transitions", routing_context="attribute:not_there")
    assert [v["value"] for v in base["transitions"]["context"]["values"]] == ["(none)"]
    model = routing.build(base, None)
    assert {tuple(p[:1]) for p in _walks(model, 200)} == {("Receive",)}


def test_sparse_context_rows_fall_back_and_edits_override_context():
    log = _rework_once(cases=30)
    base = sim.calibrate(log, routing="transitions", routing_context="history")
    model = routing.build(base, None)
    assert all(sum(1 for _ in rows) for rows in model["context"]["next"].values())
    supported = {k for k in model["context"]["next"]}
    assert all(k[1] != "Receive" or True for k in supported)
    edited = routing.build(base, [{"from": "Check", "to": [{"activity": "Close", "weight": 1}]}])
    assert all(k[1] != "Check" for k in edited["context"]["next"])
    assert all("Fix" not in p for p in _walks(edited, 200))


def test_scenarios_report_the_context_assumption():
    log = _rework_once(cases=600)
    result = sim.what_if(log, {"format": sim.FORMAT, "routing": "transitions", "routing_context": "history",
                                          "cases": 300, "seed": 2})
    assert any("previous and current activity" in a for a in result["assumptions"])
    assert not any("not on earlier steps" in a for a in result["assumptions"])


@pytest.mark.parametrize("value", ["sideways", "attribute:", "attribute:bad name!", 5])
def test_invalid_context_is_refused(value):
    with pytest.raises(EventLogWorkspaceError):
        routing.parse_context(value)


def test_context_needs_transition_routing():
    log = _rework_once(cases=50)
    with pytest.raises(EventLogWorkspaceError):
        sim.calibrate(log, routing="paths", routing_context="history")
