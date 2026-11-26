from __future__ import annotations

from collections import deque
from hashlib import sha256
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import event_log_bpmn_replay_import as importer
from server import event_log_petri_replay as replay
from server.event_log_workspace import EventLogWorkspaceError


def bpmn(nodes, edges, *, extra="", root_extra=""):
    content = "".join(f'<{kind} id="{ident}"{attrs}/>' for ident, kind, attrs in nodes)
    content += "".join(f'<sequenceFlow id="f{i}" sourceRef="{a}" targetRef="{b}"/>'
                       for i, (a, b) in enumerate(edges))
    return (f'<definitions xmlns="{importer.NS}"><process id="p">{content}{extra}'
            f'</process>{root_extra}</definitions>').encode()


def node(ident, kind, label=None):
    return ident, kind, "" if label is None else f' name="{label}"'


START = node("s", "startEvent")
END = node("e", "endEvent")
A = node("a", "task", "A")
B = node("b", "userTask", "B")
C = node("c", "manualTask", "C")
SPLIT = node("x", "parallelGateway")
JOIN = node("j", "parallelGateway")
SEQ = bpmn([START, A, B, END], [("s", "a"), ("a", "b"), ("b", "e")])


def traces(data, max_length=6):
    """Independent bounded visible-language oracle for the emitted PNML."""
    report = importer.to_pnml(data)
    net = replay.parse_pnml(report["pnml"].encode())
    places = sorted(net["places"])
    initial = tuple(net["initial"][p] for p in places)
    final = tuple(net["final"][p] for p in places)
    queue = deque([(initial, ())])
    seen = set(queue)
    accepted = set()
    while queue:
        marking, trace = queue.popleft()
        if marking == final:
            accepted.add(trace)
        for t, label in net["transitions"].items():
            if label is not None and len(trace) >= max_length:
                continue
            if not all(n >= net["pre"][t][p] for p, n in zip(places, marking)):
                continue
            nxt = tuple(n-net["pre"][t][p]+net["post"][t][p] for p, n in zip(places, marking))
            assert all(0 <= n <= 1 for n in nxt), (t, nxt)
            state = nxt, trace if label is None else trace + (label,)
            if state not in seen:
                seen.add(state)
                queue.append(state)
    return accepted


def test_exact_sequence_and_digest_mapping():
    report = importer.to_pnml(SEQ)
    assert traces(SEQ) == {("A", "B")}
    assert report["bpmn_sha256"] == sha256(SEQ).hexdigest()
    assert report["pnml_sha256"] == sha256(report["pnml"].encode()).hexdigest()
    assert set(report["node_transition_ids"]) == {"s", "a", "b", "e"}
    assert set(report["flow_place_ids"]) == {"f0", "f1", "f2"}
    assert report == importer.to_pnml(SEQ)


def test_exclusive_choice_and_loop():
    x, j = node("x", "exclusiveGateway"), node("j", "exclusiveGateway")
    choice = bpmn([START, x, A, B, j, END],
                  [("s", "x"), ("x", "a"), ("x", "b"), ("a", "j"), ("b", "j"), ("j", "e")])
    assert traces(choice) == {("A",), ("B",)}
    loop = bpmn([START, x, A, j, END],
                [("s", "x"), ("x", "a"), ("a", "j"), ("j", "x"), ("j", "e")])
    assert traces(loop, 4) == {("A",) * n for n in range(1, 5)}


def test_parallel_interleaving_and_exact_join():
    data = bpmn([START, SPLIT, A, B, JOIN, C, END],
                [("s", "x"), ("x", "a"), ("x", "b"), ("a", "j"), ("b", "j"), ("j", "c"), ("c", "e")])
    assert traces(data) == {("A", "B", "C"), ("B", "A", "C")}
    assert importer.to_pnml(data)["parallel_pairs"] == {"x": "j"}


def test_nested_parallel_regions():
    y, k = node("y", "parallelGateway"), node("k", "parallelGateway")
    data = bpmn([START, SPLIT, y, A, B, k, C, JOIN, END],
                [("s", "x"), ("x", "y"), ("x", "c"), ("y", "a"), ("y", "b"),
                 ("a", "k"), ("b", "k"), ("k", "j"), ("c", "j"), ("j", "e")])
    from itertools import permutations
    assert traces(data) == set(permutations("ABC"))
    assert importer.to_pnml(data)["parallel_pairs"] == {"x": "j", "y": "k"}


def test_parallel_empty_branch():
    data = bpmn([START, SPLIT, A, JOIN, END],
                [("s", "x"), ("x", "a"), ("x", "j"), ("a", "j"), ("j", "e")])
    assert traces(data) == {("A",)}


@pytest.mark.parametrize("data", [
    SEQ.replace(b"<task ", b"<callActivity "),
    SEQ.replace(b"<task ", b"<inclusiveGateway "),
    SEQ.replace(b'<task id="a" name="A"/>', b'<task id="a" name="A"><multiInstanceLoopCharacteristics/></task>'),
    SEQ.replace(b'<startEvent id="s"/>', b'<startEvent id="s"><timerEventDefinition/></startEvent>'),
    SEQ.replace(b'<sequenceFlow id="f0" sourceRef="s" targetRef="a"/>',
                b'<sequenceFlow id="f0" sourceRef="s" targetRef="a"><conditionExpression>x</conditionExpression></sequenceFlow>'),
    SEQ.replace(b'name="A"', b'name=" A "'),
    SEQ.replace(b'id="b"', b'id="a"'),
    SEQ.replace(b'targetRef="b"', b'targetRef="missing"'),
    SEQ.replace(b'</process>', b'<subProcess id="sub"/></process>'),
    SEQ.replace(b'</definitions>', b'<collaboration id="c"/></definitions>'),
    SEQ.replace(b'</definitions>', b'<process id="second"/></definitions>'),
    b'<!DOCTYPE definitions [<!ENTITY x "x">]>' + SEQ,
    SEQ.decode().encode("utf-16"),
    b"<notBPMN/>",
])
def test_unsupported_and_malformed_inputs_refused(data):
    with pytest.raises(EventLogWorkspaceError):
        importer.to_pnml(data)


def test_unpaired_parallel_and_cross_branch_refused():
    bad = bpmn([START, SPLIT, A, B, node("j", "exclusiveGateway"), END],
               [("s", "x"), ("x", "a"), ("x", "b"), ("a", "j"), ("b", "j"), ("j", "e")])
    with pytest.raises(EventLogWorkspaceError, match="matching join"):
        importer.to_pnml(bad)


def test_owner_only_endpoint(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data = b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p"><startEvent id="s"/><task id="a" name="A"/><endEvent id="e"/><sequenceFlow id="f1" sourceRef="s" targetRef="a"/><sequenceFlow id="f2" sourceRef="a" targetRef="e"/></process></definitions>'
with TestClient(app) as client:
    path="/v1/event-logs/bpmn-to-pnml"
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),"Content-Type":"application/xml"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"Content-Type":"application/xml"}
    assert client.post(path,content=data,headers=bearer).status_code==403
    response=client.post(path,content=data,headers=owner)
    assert response.status_code==200,response.text
    assert response.json()["process_id"]=="p"
    assert response.headers["cache-control"]=="no-store"
    assert client.post(path,content=b"<bad/>",headers=owner).status_code==422
    assert client.post(path,content=b"x"*(2*1024*1024+1),headers=owner).status_code==413
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout


def embedded(ident, body):
    return f'<subProcess id="{ident}">{body}</subProcess>'


def inner(ident, nodes, edges):
    return "".join(f'<{kind} id="{n}"{attrs}/>' for n, kind, attrs in nodes) + "".join(
        f'<sequenceFlow id="{ident}_f{i}" sourceRef="{a}" targetRef="{b}"/>'
        for i, (a, b) in enumerate(edges))


def wrapped(body, wrapper="sub"):
    return bpmn([START, A, END], [("s", "a"), ("a", wrapper), (wrapper, "e")],
                extra=embedded(wrapper, body))


def test_embedded_scope_visible_internal_tasks_and_silent_wrapper():
    body = inner("sub", [node("ss", "startEvent"), B, C, node("ee", "endEvent")],
                 [("ss", "b"), ("b", "c"), ("c", "ee")])
    data = wrapped(body)
    assert traces(data) == {("A", "B", "C")}
    result = importer.to_pnml(data)
    assert result["nodes"] == 8
    assert result["sequence_flows"] == 6
    assert result["scope_by_node"]["b"] == "sub"
    assert result["scope_by_node"]["sub"] == "p"
    assert result["scope_by_flow"]["sub_f0"] == "sub"
    boundary = result["subprocess_boundaries"]["sub"]
    assert result["node_transition_ids"]["sub"] == [
        boundary["entry_transition_id"], boundary["exit_transition_id"]]
    parsed = replay.parse_pnml(result["pnml"].encode())
    assert all(parsed["transitions"][t] is None for t in result["node_transition_ids"]["sub"])
    assert parsed["initial"][boundary["entry_place_id"]] == 0
    assert parsed["final"][boundary["exit_place_id"]] == 0


def test_nested_subprocess_and_parallel_inside_repeatable_outer_scope():
    inner_body = inner("deep", [node("ds", "startEvent"), SPLIT, B, C, JOIN, node("de", "endEvent")],
                      [("ds", "x"), ("x", "b"), ("x", "c"), ("b", "j"), ("c", "j"), ("j", "de")])
    outer_body = inner("sub", [node("ss", "startEvent"), node("se", "endEvent")],
                      [("ss", "deep"), ("deep", "se")]) + embedded("deep", inner_body)
    # The outer XOR loop may run the entire subprocess repeatedly. The child
    # parallel region is acyclic within its own activation.
    data = bpmn([START, node("merge", "exclusiveGateway"), node("choice", "exclusiveGateway"), END],
                [("s", "merge"), ("merge", "sub"), ("sub", "choice"),
                 ("choice", "merge"), ("choice", "e")], extra=embedded("sub", outer_body))
    expected = {("B", "C"), ("C", "B")}
    expected |= {a + b for a in expected for b in expected}
    assert traces(data, 4) == expected
    result = importer.to_pnml(data)
    assert result["parallel_pairs"] == {"x": "j"}
    assert set(result["subprocess_boundaries"]) == {"sub", "deep"}
    assert result["scope_by_node"]["b"] == "deep"


@pytest.mark.parametrize("change", [
    lambda d: d.replace(b'<subProcess id="sub">', b'<subProcess id="sub" triggeredByEvent="true">'),
    lambda d: d.replace(b'<subProcess id="sub">', b'<subProcess id="sub" isForCompensation="true">'),
    lambda d: d.replace(b'<subProcess id="sub">', b'<subProcess id="sub"><multiInstanceLoopCharacteristics/>'),
    lambda d: d.replace(b'targetRef="sub"', b'targetRef="b"'),
    lambda d: d.replace(b'sourceRef="ss"', b'sourceRef="a"'),
    lambda d: d.replace(b'id="b"', b'id="a"'),
    lambda d: d.replace(b'id="sub_f0"', b'id="f0"'),
    lambda d: d.replace(b'<endEvent id="ee"/>', b'<endEvent id="ee"><terminateEventDefinition/></endEvent>'),
])
def test_subprocess_scope_and_semantic_refusals(change):
    body = inner("sub", [node("ss", "startEvent"), B, node("ee", "endEvent")],
                 [("ss", "b"), ("b", "ee")])
    with pytest.raises(EventLogWorkspaceError):
        importer.to_pnml(change(wrapped(body)))


def test_global_depth_and_node_limits(monkeypatch):
    body = inner("sub", [node("ss", "startEvent"), B, node("ee", "endEvent")],
                 [("ss", "b"), ("b", "ee")])
    monkeypatch.setattr(importer, "MAX_SCOPE_DEPTH", 0)
    with pytest.raises(EventLogWorkspaceError, match="nesting"):
        importer.to_pnml(wrapped(body))
    monkeypatch.setattr(importer, "MAX_SCOPE_DEPTH", 12)
    monkeypatch.setattr(importer, "MAX_NODES", 5)
    with pytest.raises(EventLogWorkspaceError, match="across scopes"):
        importer.to_pnml(wrapped(body))
