"""Token-based replay of an event log on an imported PNML Petri net."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_cases as cases
from server import event_log_petri_replay as pr
from server import event_log_pnml as pnml
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(traces):
    rows = ["case,activity,time"]
    for i, trace in enumerate(traces):
        for j, activity in enumerate(trace):
            rows.append(f"c{i},{activity},2024-01-01T{j // 60:02d}:{j % 60:02d}:{i % 60:02d}Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _sequence_net(labels, final=True, silent_skip=None):
    """source → a → p1 → b → … → sink; ``silent_skip`` adds a silent transition around that step."""
    places = [f'<place id="p{i}"><name><text>p{i}</text></name>'
              + ('<initialMarking><text>1</text></initialMarking>' if i == 0 else '') + '</place>'
              for i in range(len(labels) + 1)]
    transitions, arcs = [], []
    for i, label in enumerate(labels):
        transitions.append(f'<transition id="t{i}"><name><text>{label}</text></name></transition>')
        arcs += [f'<arc id="a{i}i" source="p{i}" target="t{i}"/>', f'<arc id="a{i}o" source="t{i}" target="p{i + 1}"/>']
    if silent_skip is not None:
        i = silent_skip
        transitions.append('<transition id="skip"><name><text>tau</text></name>'
                           '<toolspecific tool="ProM" version="6.4" activity="$invisible$" localNodeID="x"/></transition>')
        arcs += [f'<arc id="s1" source="p{i}" target="skip"/>', f'<arc id="s2" source="skip" target="p{i + 1}"/>']
    marking = (f'<finalmarkings><marking><place idref="p{len(labels)}"><text>1</text></place></marking></finalmarkings>'
               if final else '')
    return (f'<?xml version="1.0"?><pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml"><net id="n" '
            f'type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel"><page id="pg">{"".join(places)}'
            f'{"".join(transitions)}{"".join(arcs)}</page>{marking}</net></pnml>').encode()


def test_discovered_net_round_trip_fits():
    traces = [["Register", "Check", "Pay", "Close"]] * 10 + [["Register", "Pay", "Check", "Close"]] * 6 \
        + [["Register", "Check", "Close"]] * 4
    log = _import(traces)
    xml, _ = pnml.discovered_pnml(log, noise=0.0)
    result = pr.replay(log, xml.encode())
    assert result["cases"] == 20 and result["variants"] == 3
    assert result["fitness"]["log"] == 1.0 and result["fitness"]["fitting_case_share"] == 1.0
    assert result["net"]["silent_transitions"] >= 1 and result["non_fitting_variants"] == []


def test_skipped_step_and_unknown_activity():
    log = _import([["A", "B", "C"]] * 8 + [["A", "C"]] * 2 + [["A", "B", "X", "C"]])
    result = pr.replay(log, _sequence_net(["A", "B", "C"]))
    assert result["fitness"]["fitting_cases"] == 8
    assert 0 < result["fitness"]["log"] < 1
    assert result["activities_not_in_net"] == [{"activity": "X", "cases": 1}]
    by_place = {p["place"]: p for p in result["places"]}
    assert by_place["p2"]["missing"] == 2 and by_place["p1"]["remaining"] == 2
    assert result["transitions_fired_without_tokens"] == [{"transition": "C", "times": 2}]
    worst = result["non_fitting_variants"][0]
    assert worst["path"] == ["A", "C"] and worst["cases"] == 2 and worst["missing_tokens"] == 1


def test_silent_transition_enables_skip_and_final_marking_is_guessed():
    log = _import([["A", "B", "C"]] * 5 + [["A", "C"]] * 5)
    result = pr.replay(log, _sequence_net(["A", "B", "C"], final=False, silent_skip=1))
    assert result["fitness"]["log"] == 1.0 and result["net"]["final_marking_declared"] is False


def test_long_variant_resolves_by_identifier_without_uploading_its_labels():
    long_activity = "雪" * 180
    path = ["A"] + [long_activity] * 498 + ["C"]
    log = _import([path, ["A", "B", "C"]])
    result = pr.replay(log, _sequence_net(["A", "B", "C"]))
    variant = result["non_fitting_variants"][0]
    assert variant["path"] == path
    evidence = cases.cases(log, {"variant_id": variant["variant_id"]})
    assert evidence["selected_cases"] == variant["cases"] == 1
    assert evidence["cases"][0]["case_id"] == "c0"


def test_rejects_bad_files():
    log = _import([["A"]] * 2)
    with pytest.raises(EventLogWorkspaceError, match="DOCTYPE"):
        pr.replay(log, b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')
    with pytest.raises(EventLogWorkspaceError, match="well-formed"):
        pr.replay(log, b"<pnml><net>")
    with pytest.raises(EventLogWorkspaceError, match="no <net>"):
        pr.replay(log, b"<pnml/>")
    with pytest.raises(EventLogWorkspaceError, match="initial marking"):
        pr.replay(log, _sequence_net(["A"]).replace(b"<initialMarking><text>1</text></initialMarking>", b""))


def _flower(labels):
    """One place with every activity as a self-loop: allows anything, so it fits everything imprecisely."""
    transitions = "".join(f'<transition id="t{i}"><name><text>{a}</text></name></transition>' for i, a in enumerate(labels))
    arcs = "".join(f'<arc id="i{i}" source="p" target="t{i}"/><arc id="o{i}" source="t{i}" target="p"/>'
                   for i in range(len(labels)))
    return (f'<pnml><net id="n"><place id="p"><initialMarking><text>1</text></initialMarking></place>{transitions}'
            f'{arcs}<finalmarkings><marking><place idref="p"><text>1</text></place></marking></finalmarkings>'
            '</net></pnml>').encode()


def test_precision_separates_a_flower_from_the_sequence():
    log = _import([["A", "B", "C"]] * 10)
    exact = pr.replay(log, _sequence_net(["A", "B", "C"]))
    flower = pr.replay(log, _flower(["A", "B", "C"]))
    assert exact["fitness"]["log"] == 1.0 and flower["fitness"]["log"] == 1.0
    assert exact["precision"]["precision"] == 1.0 and exact["precision"]["escaping_edges"] == []
    assert flower["precision"]["precision"] == round(1 - 6 / 9, 4)
    assert {"after": None, "net_also_allows": "B", "cases": 10} in flower["precision"]["escaping_edges"]
