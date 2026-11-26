"""PTML import: ProM/PM4Py process trees become workflow nets that replay like any PNML net."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_petri_replay as pr
from server import event_log_ptml_import as ptml
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _doc(nodes: list[tuple[str, str, str]], edges: list[tuple[str, str]], root: str = "n0", name: str = "t") -> bytes:
    body = "".join(f'<{tag} id="{i}" name="{label}"/>' for tag, i, label in nodes)
    body += "".join(f'<parentsNode id="e{k}" sourceId="{a}" targetId="{b}"/>' for k, (a, b) in enumerate(edges))
    return f'<?xml version="1.0" encoding="UTF-8"?><ptml><processTree id="t" name="{name}" root="{root}">{body}</processTree></ptml>'.encode()


# ->( 'Receive', X( tau, 'Check' ), +( 'Pay', 'Ship' ), *( 'Fix', tau, 'Close' ) )  (ProM three-child loop)
PROM = _doc([("sequence", "n0", ""), ("manualTask", "n1", "Receive"), ("xor", "n2", ""), ("automaticTask", "n3", "tau"),
             ("manualTask", "n4", "Check"), ("and", "n5", ""), ("manualTask", "n6", "Pay"), ("manualTask", "n7", "Ship"),
             ("xorLoop", "n8", ""), ("manualTask", "n9", "Fix"), ("automaticTask", "n10", "tau"), ("manualTask", "n11", "Close")],
            [("n0", "n1"), ("n0", "n2"), ("n2", "n3"), ("n2", "n4"), ("n0", "n5"), ("n5", "n6"), ("n5", "n7"),
             ("n0", "n8"), ("n8", "n9"), ("n8", "n10"), ("n8", "n11")], name="claims tree")


def test_prom_tree_becomes_the_expected_tree():
    tree, name = ptml.read_tree(PROM)
    assert name == "claims tree"
    assert tree == {"op": "seq", "children": [
        {"op": "activity", "activity": "Receive"},
        {"op": "xor", "children": [{"op": "tau"}, {"op": "activity", "activity": "Check"}]},
        {"op": "and", "children": [{"op": "activity", "activity": "Pay"}, {"op": "activity", "activity": "Ship"}]},
        {"op": "seq", "children": [{"op": "loop", "children": [{"op": "activity", "activity": "Fix"}, {"op": "tau"}]},
                                   {"op": "activity", "activity": "Close"}]}]}
    out = ptml.to_pnml(PROM)
    assert out["activities"] == ["Check", "Close", "Fix", "Pay", "Receive", "Ship"] and out["silent_transitions"] >= 3
    assert out["pnml"].startswith('<?xml version="1.0" encoding="UTF-8"?>') and "claims tree" in out["pnml"]


def test_pm4py_two_child_loop_and_silent_exit():
    doc = _doc([("xorLoop", "n0", ""), ("manualTask", "n1", "A"), ("automaticTask", "n2", "tau")], [("n0", "n1"), ("n0", "n2")])
    assert ptml.read_tree(doc)[0] == {"op": "loop", "children": [{"op": "activity", "activity": "A"}, {"op": "tau"}]}
    doc3 = _doc([("xorLoop", "n0", ""), ("manualTask", "n1", "A"), ("automaticTask", "n2", "tau"), ("automaticTask", "n3", "tau")],
                [("n0", "n1"), ("n0", "n2"), ("n0", "n3")])
    assert ptml.read_tree(doc3)[0] == ptml.read_tree(doc)[0]


def test_converted_net_replays_recorded_cases():
    rows = ["case,activity,time"]
    traces = [["Receive", "Check", "Pay", "Ship", "Fix", "Close"], ["Receive", "Ship", "Pay", "Fix", "Fix", "Close"],
              ["Receive", "Pay", "Ship", "Fix", "Close"]]
    for i in range(12):
        rows += [f"c{i},{a},2024-03-01T{8 + k:02d}:00:00Z" for k, a in enumerate(traces[i % 3])]
    rows += [f"bad,{a},2024-03-02T{8 + k:02d}:00:00Z" for k, a in enumerate(["Receive", "Close"])]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                               mapping=MAPPING)["log_id"]
    result = pr.replay(log, ptml.to_pnml(PROM)["pnml"].encode())
    # The skip, both parallel orders and the repeated loop body fit; only the case that skips work does not.
    assert result["cases"] == 13 and result["fitness"]["fitting_cases"] == 12
    assert [v["path"] for v in result["non_fitting_variants"]] == [["Receive", "Close"]]
    assert result["activities_not_in_net"] == [] and result["net_labels_never_seen"] == []


@pytest.mark.parametrize("doc,message", [
    (b'<!DOCTYPE x [<!ENTITY e "x">]><ptml/>', "DOCTYPE"),
    (b"<ptml><processTree", "well-formed"),
    (b"<pnml/>", "no <processTree>"),
    (_doc([("def", "n0", ""), ("manualTask", "n1", "A")], [("n0", "n1")]), "<def> is not supported"),
    (_doc([("sequence", "n0", ""), ("manualTask", "n1", "A")], [("n0", "n1"), ("n0", "n1")]), "more than one parent"),
    (_doc([("sequence", "n0", ""), ("manualTask", "n1", "A")], [("n0", "n9")]), "not in the tree"),
    (_doc([("sequence", "n0", ""), ("sequence", "n1", "")], [("n1", "n0"), ("n0", "n1")], root="n0"), "has a parent"),
    (_doc([("sequence", "n0", ""), ("manualTask", "n1", "A"), ("manualTask", "n2", "B")], [("n0", "n1")]), "not connected"),
    (_doc([("xorLoop", "n0", ""), ("manualTask", "n1", "A")], [("n0", "n1")]), "a loop needs"),
    (_doc([("sequence", "n0", "")], []), "has no children"),
    (_doc([("manualTask", "n0", "")], []), "needs a name"),
])
def test_unsafe_or_unsupported_trees_are_refused(doc, message):
    with pytest.raises(EventLogWorkspaceError, match=message.replace("<", "<").replace("(", r"\(")):
        ptml.read_tree(doc)


def test_size_and_node_bounds(monkeypatch):
    with pytest.raises(EventLogWorkspaceError, match="larger than"):
        ptml.read_tree(b" " * (ptml.MAX_BYTES + 1))
    monkeypatch.setattr(ptml, "MAX_NODES", 3)
    with pytest.raises(EventLogWorkspaceError, match="more than 3 nodes"):
        ptml.read_tree(PROM)


@pytest.mark.skipif(not os.environ.get("OWG_PM4PY_PYTHON"), reason="set OWG_PM4PY_PYTHON to a Python with pm4py")
def test_reads_a_tree_written_by_pm4py(tmp_path):
    path = tmp_path / "t.ptml"
    code = ("import pm4py,sys; from pm4py.objects.process_tree.utils.generic import parse; "
            "pm4py.write_ptml(parse(\"->( 'A', X( 'B', tau ), +( 'C', 'D' ), *( 'E', tau ) )\"), sys.argv[1])")
    done = subprocess.run([os.environ["OWG_PM4PY_PYTHON"], "-c", code, str(path)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    tree, _ = ptml.read_tree(path.read_bytes())
    assert tree["op"] == "seq" and [c["op"] for c in tree["children"]] == ["activity", "xor", "and", "loop"]
    assert ptml.to_pnml(path.read_bytes())["activities"] == ["A", "B", "C", "D", "E"]


def test_pm4py_notation_matches_the_ptml_tree():
    text = "->( 'Receive', X( tau, 'Check' ), +( 'Pay', 'Ship' ), *( 'Fix', tau, 'Close' ) )"
    assert ptml.read_notation(text) == ptml.read_tree(PROM)[0]
    assert ptml.read_notation("*( 'A', tau )") == {"op": "loop", "children": [{"op": "activity", "activity": "A"}, {"op": "tau"}]}
    assert ptml.read_notation("X( 'it's done', 'a,b' )") == {"op": "xor", "children": [
        {"op": "activity", "activity": "it's done"}, {"op": "activity", "activity": "a,b"}]}   # quotes and commas inside labels
    assert ptml.read_notation("  'Single'  ") == {"op": "activity", "activity": "Single"}
    assert ptml.notation_to_pnml("->( 'A', 'B' )")["activities"] == ["A", "B"]


@pytest.mark.parametrize("text,message", [
    ("", "paste a process tree"),
    ("->( 'A', 'B'", "expected , or \\)"),
    ("->( 'A )", "not closed"),
    ("->( 'A' ) extra", "after the tree"),
    ("*( 'A' )", "a loop needs"),
    ("->( '' )", "needs a name"),
    ("Y( 'A' )", "unexpected text"),
    ("->->( 'A' )", "expected \\( after ->"),
])
def test_malformed_or_unsupported_notation_is_refused(text, message):
    with pytest.raises(EventLogWorkspaceError, match=message):
        ptml.read_notation(text)


def test_notation_depth_and_size_bounds():
    deep = "->( " * 501 + "'A'" + " )" * 501
    with pytest.raises(EventLogWorkspaceError, match="nested too deeply"):
        ptml.read_notation(deep)
    with pytest.raises(EventLogWorkspaceError, match="longer than"):
        ptml.read_notation("'" + "A" * ptml.MAX_NOTATION_CHARS + "'")


@pytest.mark.skipif(not os.environ.get("OWG_PM4PY_PYTHON"), reason="set OWG_PM4PY_PYTHON to a Python with pm4py")
def test_notation_agrees_with_pm4py_parser():
    trees = ["->( 'A', X( 'B', tau ), +( 'C', 'D' ), *( 'E', tau ) )", "X( ->( 'a', 'b' ), +( 'c', *( 'd', 'e' ) ) )"]
    code = ("import sys,json; from pm4py.objects.process_tree.utils.generic import parse\n"
            "def walk(t):\n"
            "    if not t.children: return {'op':'tau'} if t.label is None else {'op':'activity','activity':t.label}\n"
            "    op={'->':'seq','X':'xor','+':'and','*':'loop'}[str(t.operator)]\n"
            "    return {'op':op,'children':[walk(c) for c in t.children]}\n"
            "print(json.dumps([walk(parse(s)) for s in json.loads(sys.argv[1])]))")
    import json
    done = subprocess.run([os.environ["OWG_PM4PY_PYTHON"], "-c", code, json.dumps(trees)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [ptml.read_notation(t) for t in trees]


def test_route_is_dashboard_only(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b'<ptml><processTree id="t" name="x" root="a"><sequence id="a" name=""/><manualTask id="b" name="A"/><manualTask id="c" name="B"/><parentsNode id="1" sourceId="a" targetId="b"/><parentsNode id="2" sourceId="a" targetId="c"/></processTree></ptml>'
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/event-logs/ptml-to-pnml"
    assert client.post(path, content=doc, headers=bearer).status_code == 403
    r = client.post(path, content=doc, headers=owner)
    assert r.status_code == 200 and r.json()["activities"] == ["A", "B"] and "<pnml>" in r.json()["pnml"], r.text
    assert client.post(path, content=b"<ptml/>", headers=owner).status_code == 422
    assert client.post(path, content=b" " * (2 * 1024 * 1024 + 1), headers=owner).status_code == 413
    text_path = "/v1/event-logs/process-tree-to-pnml"
    assert client.post(text_path, json={"notation": "->( 'A', 'B' )"}, headers=bearer).status_code == 403
    r = client.post(text_path, json={"notation": "->( 'A', 'B' )"}, headers=owner)
    assert r.status_code == 200 and r.json()["activities"] == ["A", "B"], r.text
    assert client.post(text_path, json={"notation": "O( 'A' )"}, headers=owner).status_code == 200
    assert client.post(text_path, json={"notation": "<>( 'A', 'B' )"}, headers=owner).status_code == 200
    assert client.post(text_path, json={"notation": "O()"}, headers=owner).status_code == 422
    assert client.post(text_path, json={"tree": "x"}, headers=owner).status_code == 422
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_ptml_and_notation_extended_operators_agree():
    for tag, notation, op in [("or", "O( 'A', 'B' )", "or"),
                              ("interleaved", "<>( 'A', 'B' )", "interleaved")]:
        doc = _doc([(tag, "n0", ""), ("manualTask", "n1", "A"), ("manualTask", "n2", "B")],
                   [("n0", "n1"), ("n0", "n2")])
        tree = ptml.read_tree(doc)[0]
        assert tree["op"] == op
        assert tree == ptml.read_notation(notation)
        assert ptml.to_pnml(doc)["activities"] == ["A", "B"]
        assert ptml.notation_to_pnml(notation)["activities"] == ["A", "B"]


def test_ptml_deep_and_non_ascii_compatible_xml_refused():
    nodes = [("sequence", f"n{i}", "") for i in range(ptml.MAX_DEPTH + 1)]
    nodes.append(("manualTask", f"n{ptml.MAX_DEPTH + 1}", "A"))
    edges = [(f"n{i}", f"n{i+1}") for i in range(len(nodes) - 1)]
    with pytest.raises(EventLogWorkspaceError, match="nested too deeply"):
        ptml.read_tree(_doc(nodes, edges))
    with pytest.raises(EventLogWorkspaceError, match="ASCII-compatible"):
        ptml.read_tree('<!DOCTYPE x [<!ENTITY e "x">]><ptml/>'.encode("utf-16"))


def _complete_language(notation):
    """Exhaustive independent firing oracle for small, acyclic test nets.

    Check one-safeness, proper completion, and no reachable deadlock. Unlike
    greedy token replay this explores every enabled transition and marking.
    """
    from collections import deque
    from server import event_log_pnml as pnml

    raw = pnml.tree_to_net(ptml.read_notation(notation))
    parsed = pr.parse_pnml(pnml.to_pnml(raw, "oracle").encode())
    initial = frozenset(parsed["initial"])
    final = frozenset(parsed["final"])
    todo = deque([(initial, ())])
    seen = {(initial, ())}
    accepted = set()
    while todo:
        marking, trace = todo.popleft()
        if marking == final:
            accepted.add(trace)
        else:
            assert raw["sink"] not in marking
        enabled = []
        for tid, label in parsed["transitions"].items():
            pre, post = parsed["pre"][tid], parsed["post"][tid]
            assert all(v == 1 for v in pre.values())
            assert all(v == 1 for v in post.values())
            if not set(pre) <= marking:
                continue
            enabled.append(tid)
            remaining = marking - set(pre)
            assert not remaining.intersection(post), "unsafe duplicate token"
            nxt = (frozenset(remaining.union(post)), trace + ((label,) if label is not None else ()))
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
        assert bool(enabled) == (marking != final), "deadlock or enabled transition at final"
        assert len(seen) < 100_000, "unexpected state explosion"
    return accepted


def test_inclusive_choice_exact_nonempty_subsets_and_parallel_orders():
    from itertools import permutations
    expected = {p for n in range(1, 4) for p in permutations(("A", "B", "C"), n)}
    assert _complete_language("O( 'A', 'B', 'C' )") == expected
    assert _complete_language("O( tau, 'A' )") == {(), ("A",)}
    assert _complete_language("O( 'A' )") == {("A",)}


def test_interleaving_preserves_whole_child_blocks():
    assert _complete_language("<>( ->( 'A', 'B' ), ->( 'C', 'D' ) )") == {
        ("A", "B", "C", "D"), ("C", "D", "A", "B")}
    assert _complete_language("<>( tau, 'A' )") == {("A",)}
    assert _complete_language("<>( 'A' )") == {("A",)}


def test_nested_extended_operators_are_compositional():
    assert _complete_language("->( 'S', O( <>( 'A', 'B' ), 'C' ), 'E' )") == {
        ("S", *middle, "E") for middle in [
            ("C",), ("A", "B"), ("B", "A"),
            ("A", "B", "C"), ("A", "C", "B"), ("C", "A", "B"),
            ("B", "A", "C"), ("B", "C", "A"), ("C", "B", "A"),
        ]
    }


def test_extended_net_size_linear_in_branch_count():
    from server import event_log_pnml as pnml
    for op in ("O", "<>"):
        tree = ptml.read_notation(op + "(" + ",".join(f"'A{i}'" for i in range(100)) + ")")
        net = pnml.tree_to_net(tree)
        assert len(net["places"]) + len(net["transitions"]) < 1000
        assert len(net["arcs"]) < 2000
