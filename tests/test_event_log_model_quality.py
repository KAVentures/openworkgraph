"""Fitness, precision, generalization and simplicity of the discovered net across noise levels."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event

import pytest

from server import event_log_model_quality as mq
from server import event_log_petri_replay as petri
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError
from tests.test_event_log_petri_replay import _dirs, _import, _sequence_net  # noqa: F401  (fixture reuse)


def test_generalization_and_simplicity_follow_pm4py_definitions():
    net = petri.parse_pnml(_sequence_net(["A", "B", "C"]))
    # A plain sequence: 6 arcs over 7 nodes, mean degree 12/7, below 2, so simplicity is 1.
    simplicity, mean = mq._simplicity(net)
    assert mean == pytest.approx(12 / 7) and simplicity == 1
    assert mq._generalization(net, Counter({"t0": 4, "t1": 4, "t2": 4})) == pytest.approx(0.5)
    # A transition never fired counts as 1.
    assert mq._generalization(net, Counter({"t0": 4, "t1": 4})) == pytest.approx(1 - (0.5 + 0.5 + 1) / 3)


def test_noise_trades_fitness_for_precision():
    common = [["Register", "Check", "Pay", "Close"]] * 40
    rare = [["Register", "Pay", "Check", "Close"]] * 2 + [["Register", "Check", "Pay", "Pay", "Close"]]
    log = _import(common + rare)
    result = mq.model_quality(log, levels="0,0.5")
    low, high = result["levels"]
    assert (low["noise"], high["noise"]) == (0.0, 0.5)
    assert result["cases"] == 43 and result["variants"] == 3
    assert low["fitness"] == 1.0 and low["fitting_case_share"] == 1.0
    assert high["fitness"] < 1.0 and high["precision"] >= low["precision"]
    for level in result["levels"]:
        assert 0 < level["generalization"] < 1 and 0 < level["simplicity"] <= 1
        assert level["f_score"] == round(2 * level["fitness"] * level["precision"]
                                         / (level["fitness"] + level["precision"]), 4)
    best = max(result["levels"], key=lambda r: (r["f_score"], r["simplicity"], -r["noise"]))
    assert result["best_f_score_noise"] == best["noise"]


def test_default_levels_and_bad_input():
    log = _import([["A", "B"]] * 5)
    result = mq.model_quality(log)
    assert [r["noise"] for r in result["levels"]] == list(mq.DEFAULT_LEVELS)
    assert all(r["fitness"] == 1.0 and r["precision"] == 1.0 for r in result["levels"])
    for bad in ("1", "x", "", ",".join(["0.1"] * 2 + [str(i / 10) for i in range(9)])):
        with pytest.raises(EventLogWorkspaceError):
            mq.model_quality(log, levels=bad)
    with pytest.raises(EventLogWorkspaceError):
        mq.model_quality(log, activities=0)


def test_returns_exact_scored_nets_only_when_requested():
    log = _import([["A", "B", "C"]] * 12 + [["A", "C"]])
    args = {"levels": "0,0.5", "cohort": {"activity": "B"}}
    report = mq.model_quality(log, **args, include_nets=True)
    plain = mq.model_quality(log, **args)
    assert report["cohort"] == {"activity": "B"}
    assert report["cases"] == 12
    for row, other in zip(report["levels"], plain["levels"], strict=True):
        pnml = row["pnml"].encode()
        assert row["pnml_sha256"] == sha256(pnml).hexdigest()
        assert {key: value for key, value in row.items() if key not in {"pnml", "pnml_sha256"}} == other
        assert petri.replay(log, pnml, cohort=args["cohort"])["fitness"]["log"] == row["fitness"]


def test_model_discovery_reads_hold_the_trace_snapshot(monkeypatch):
    log = _import([["A", "B"]] * 10)
    entered, attempting, acquired = Event(), Event(), Event()
    original = mq.pnml.discovered_pnml

    def discover(*args, **kwargs):
        entered.set()
        assert attempting.wait(5)
        assert not acquired.wait(0.1)
        return original(*args, **kwargs)

    def competing_write():
        assert entered.wait(5)
        attempting.set()
        with workspace._LOCK:
            acquired.set()

    monkeypatch.setattr(mq.pnml, "discovered_pnml", discover)
    with ThreadPoolExecutor(2) as pool:
        score = pool.submit(mq.model_quality, log, levels="0,0.2")
        writer = pool.submit(competing_write)
        assert score.result(timeout=30)["cases"] == 10
        writer.result(timeout=30)
    assert acquired.is_set()
