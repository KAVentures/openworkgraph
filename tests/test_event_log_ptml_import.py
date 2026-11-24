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
    (_doc([("or", "n0", ""), ("manualTask", "n1", "A")], [("n0", "n1")]), "<or> is not supported"),
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
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
