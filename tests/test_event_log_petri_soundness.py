from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from server import event_log_petri_soundness as soundness
from server.event_log_workspace import EventLogWorkspaceError

PT = "http://www.pnml.org/version-2009/grammar/ptnet"


def model(places, transitions, arcs, initial=None, final=None):
    root = ET.Element("pnml")
    net = ET.SubElement(root, "net", id="n", type=PT)
    page = ET.SubElement(net, "page", id="page")
    for p in places:
        node = ET.SubElement(page, "place", id=p)
        if p in (initial or {"s": 1}):
            ET.SubElement(ET.SubElement(node, "initialMarking"), "text").text = str((initial or {"s": 1})[p])
    for t in transitions:
        node = ET.SubElement(page, "transition", id=t)
        ET.SubElement(ET.SubElement(node, "name"), "text").text = t
    for i, (a, b, *weight) in enumerate(arcs):
        node = ET.SubElement(page, "arc", id=f"arc{i}", source=a, target=b)
        if weight:
            ET.SubElement(ET.SubElement(node, "inscription"), "text").text = str(weight[0])
    mark = ET.SubElement(ET.SubElement(net, "finalmarkings"), "marking")
    for p, n in (final or {"f": 1}).items():
        ET.SubElement(ET.SubElement(mark, "place", idref=p), "text").text = str(n)
    return ET.tostring(root)


SEQUENCE = model(["s", "p", "f"], ["a", "b"],
                 [("s", "a"), ("a", "p"), ("p", "b"), ("b", "f")])


def test_sound_sequence_and_exact_marking_counts():
    result = soundness.analyze(SEQUENCE)
    assert result["complete"] is True
    assert result["sound"] is True
    assert result["reachable_markings"] == result["expanded_markings"] == 3
    assert result["transition_checks"] == 6
    assert result["one_safe"] is True
    assert result["noncompleting_markings"] == 0
    assert result["dead_transition_ids"] == []
    assert all(w is None for w in result["witnesses"].values())
    assert len(result["pnml_sha256"]) == 64
    assert result == soundness.analyze(SEQUENCE)


def test_sound_parallel_fork_join():
    net = model(["s", "a0", "a1", "b0", "b1", "f"], ["fork", "a", "b", "join"],
                [("s", "fork"), ("fork", "a0"), ("fork", "b0"),
                 ("a0", "a"), ("a", "a1"), ("b0", "b"), ("b", "b1"),
                 ("a1", "join"), ("b1", "join"), ("join", "f")])
    result = soundness.analyze(net)
    assert result["sound"] is True
    assert result["reachable_markings"] == 6


def test_structural_workflow_net_with_deadlock_and_dead_transition():
    net = model(["s", "p", "q", "f"], ["left", "right", "join"],
                [("s", "left"), ("left", "p"), ("s", "right"), ("right", "q"),
                 ("p", "join"), ("q", "join"), ("join", "f")])
    result = soundness.analyze(net)
    assert result["workflow_net_structure"] is True
    assert result["sound"] is False
    assert result["option_to_complete"] is False
    assert result["final_reachable"] is False
    assert result["noncompleting_markings"] == 3
    assert result["dead_transition_ids"] == ["join"]
    assert result["witnesses"]["deadlock"] == {
        "transition_ids": ["left"], "steps": 1, "truncated": False,
        "marking": [{"place_id": "p", "tokens": 1}]}


def test_livelock_without_deadlock_is_not_completable():
    net = model(["s", "p", "q", "f"], ["enter", "alternate", "loop", "join", "finish"],
                [("s", "enter"), ("enter", "p"), ("s", "alternate"), ("alternate", "q"),
                 ("p", "loop"), ("loop", "p"), ("p", "join"), ("q", "join"),
                 ("join", "f"), ("q", "finish"), ("finish", "f")])
    result = soundness.analyze(net)
    assert result["complete"] is True
    assert result["workflow_net_structure"] is True
    assert result["witnesses"]["deadlock"] is None
    assert result["option_to_complete"] is False
    assert result["witnesses"]["cannot_complete"]["transition_ids"] == ["enter"]


def test_improper_completion_and_unsafe_marking_are_distinct():
    net = model(["s", "p", "f"], ["split", "finish"],
                [("s", "split"), ("split", "p", 2), ("split", "f"),
                 ("p", "finish"), ("finish", "f")])
    result = soundness.analyze(net)
    assert result["workflow_net_structure"] is True
    assert result["proper_completion"] is False
    assert result["one_safe"] is False
    assert result["witnesses"]["improper_completion"]["transition_ids"] == ["split"]
    assert result["witnesses"]["unsafe_marking"]["transition_ids"] == ["split"]


def test_weighted_sound_net_need_not_be_one_safe():
    net = model(["s", "p", "f"], ["a", "b"],
                [("s", "a"), ("a", "p", 2), ("p", "b", 2), ("b", "f")])
    result = soundness.analyze(net)
    assert result["sound"] is True
    assert result["one_safe"] is False


def test_cycle_with_exit_can_still_be_sound():
    net = model(["s", "p", "f"], ["enter", "loop", "finish"],
                [("s", "enter"), ("enter", "p"), ("p", "loop"), ("loop", "p"),
                 ("p", "finish"), ("finish", "f")])
    result = soundness.analyze(net)
    assert result["sound"] is True
    assert result["reachable_markings"] == 3


def test_state_limit_never_claims_soundness_or_dead_transition(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_STATES", 2)
    result = soundness.analyze(SEQUENCE)
    assert result["complete"] is False
    assert result["limit"] == "state_limit"
    assert result["sound"] is None
    assert result["option_to_complete"] is None
    assert result["dead_transition_ids"] is None
    assert result["final_reachable"] is None


def test_transition_limit_preserves_proven_structure_but_not_completion(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_CHECKS", 1)
    result = soundness.analyze(SEQUENCE)
    assert result["limit"] == "transition_check_limit"
    assert result["workflow_net_structure"] is True
    assert result["sound"] is None


def test_token_limit_is_not_an_unboundedness_claim(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_TOKENS", 3)
    net = model(["s", "p", "f"], ["enter", "grow", "finish"],
                [("s", "enter"), ("enter", "p"), ("p", "grow"), ("grow", "p", 2),
                 ("p", "finish"), ("finish", "f")])
    result = soundness.analyze(net)
    assert result["complete"] is False
    assert result["limit"] == "token_limit"
    assert result["one_safe"] is False
    assert result["sound"] is not True
    assert result["dead_transition_ids"] is None


@pytest.mark.parametrize("change,match", [
    (lambda b: b.replace(b'id="b"', b'id="a"'), "distinct IDs"),
    (lambda b: b.replace(b'<arc id=', b'<arc type="inhibitor" id=', 1), "unsupported net node"),
    (lambda b: b.replace(b'</net>', b'<finalmarkings><marking/></finalmarkings></net>'), "one explicit final"),
    (lambda b: b.replace(b'</marking>', b'<place idref="f"><text>1</text></place></marking>'), "duplicate final"),
    (lambda b: b.replace(b'<finalmarkings>', b'<unsupported>'), "well-formed"),
    (lambda b: b.replace(b'</page>', b'<transition id="extra"><condition/></transition></page>'), "unsupported PNML"),
    (lambda b: b.replace(b'grammar/ptnet', b'grammar/highlevelnet'), "ordinary PNML"),
    (lambda b: b.replace(b'</net>', b'<toolspecific><place id="hidden"/></toolspecific></net>'), "unsupported tool-specific"),
    (lambda b: b.replace(b'</pnml>', b'<net id="other"/></pnml>'), "exactly one net"),
    (lambda b: b.decode().encode("utf-16"), "ASCII-compatible"),
])
def test_unsupported_or_ambiguous_pnml_refused(change, match):
    with pytest.raises(EventLogWorkspaceError, match=match):
        soundness.analyze(change(SEQUENCE))


def test_zero_weight_refused():
    net = model(["s", "f"], ["a"], [("s", "a", 0), ("a", "f")])
    with pytest.raises(EventLogWorkspaceError, match="positive"):
        soundness.analyze(net)


def test_route_owner_only_and_nonretained(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import event_log_pnml
net = event_log_pnml.to_pnml(event_log_pnml.tree_to_net({"op":"activity","activity":"A"}), "simple")
path = "/v1/event-logs/petri-soundness"
with TestClient(app) as client:
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    assert client.post(path, content=net, headers=bearer).status_code == 403
    response = client.post(path, content=net, headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["sound"] is True
    assert response.headers["cache-control"] == "no-store"
    assert client.post(path, content=b"<pnml/>", headers=owner).status_code == 422
    assert client.post(path, content=b" " * (2*1024*1024+1), headers=owner).status_code == 413
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout
