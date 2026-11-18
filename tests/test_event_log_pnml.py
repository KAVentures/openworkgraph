"""PNML export: the workflow net of a discovered process tree accepts what the tree accepts."""
from __future__ import annotations

import itertools
from collections import Counter
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from server import event_log_model_discovery as discovery
from server import event_log_pnml as pnml
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _replays(net, trace) -> bool:
    """Token game with silent moves: is the final marking reachable while firing exactly ``trace``?"""
    pre, post = {}, {}
    for a, b in net["arcs"]:
        if a.startswith("t"):
            post.setdefault(a, Counter())[b] += 1
        else:
            pre.setdefault(b, Counter())[a] += 1
    labels = dict(net["transitions"])

    def fire(marking, t):
        if any(marking[p] < n for p, n in pre.get(t, {}).items()):
            return None
        out = marking.copy()
        out.subtract(pre.get(t, {}))
        out.update(post.get(t, {}))
        return +out

    def key(m):
        return tuple(sorted(m.items()))

    start = Counter({net["source"]: 1})
    frontier = {key(start): start}
    for step in range(len(trace) + 1):
        # Close under silent transitions (bounded: nets here are safe, so markings are finite).
        seen = dict(frontier)
        pending = list(frontier.values())
        while pending:
            m = pending.pop()
            for t, label in labels.items():
                if label is None and (n := fire(m, t)) is not None and key(n) not in seen:
                    seen[key(n)] = n
                    pending.append(n)
        if step == len(trace):
            return any(m == Counter({net["sink"]: 1}) for m in seen.values())
        frontier = {}
        for m in seen.values():
            for t, label in labels.items():
                if label == trace[step] and (n := fire(m, t)) is not None:
                    frontier[key(n)] = n
        if not frontier:
            return False
    return False


TREES = [
    {"op": "seq", "children": [{"op": "activity", "activity": "A"},
                               {"op": "xor", "children": [{"op": "activity", "activity": "B"}, {"op": "tau"}]},
                               {"op": "and", "children": [{"op": "activity", "activity": "C"},
                                                          {"op": "activity", "activity": "D"}]},
                               {"op": "loop", "children": [{"op": "activity", "activity": "E"},
                                                           {"op": "activity", "activity": "F"}]}]},
    {"op": "loop", "children": [{"op": "xor", "children": [{"op": "activity", "activity": "A"},
                                                           {"op": "activity", "activity": "B"}]}, {"op": "tau"}]},
]


@pytest.mark.parametrize("tree", TREES)
def test_net_language_matches_tree(tree):
    net = pnml.tree_to_net(tree)
    letters = sorted({label for _, label in net["transitions"] if label})
    for length in range(0, 7):
        for trace in itertools.product(letters, repeat=length):
            assert _replays(net, trace) == discovery._accepts(tree, trace), trace


def test_pnml_document_structure():
    net = pnml.tree_to_net(TREES[0])
    doc = ET.fromstring(pnml.to_pnml(net, "orders <&> net").split("\n", 1)[1])
    page = doc.find("net/page")
    assert doc.find("net").get("type") == pnml.PNML_NET_TYPE
    assert doc.find("net/name/text").text == "orders <&> net"
    places = page.findall("place")
    marked = [p for p in places if p.find("initialMarking") is not None]
    assert len(marked) == 1 and marked[0].find("initialMarking/text").text == "1"
    assert doc.find("net/finalmarkings/marking/place").get("idref") == net["sink"]
    transitions = page.findall("transition")
    silent = [t for t in transitions if t.find("toolspecific") is not None]
    visible = sorted(t.find("name/text").text for t in transitions if t.find("toolspecific") is None)
    assert visible == ["A", "B", "C", "D", "E", "F"] and len(silent) == 5
    assert all(t.find("toolspecific").get("activity") == "$invisible$" for t in silent)
    ids = {e.get("id") for e in page}
    assert all(a.get("source") in ids and a.get("target") in ids for a in page.findall("arc"))


def test_discovered_log_round_trip():
    rows = ["case,activity,time"]
    for i in range(20):
        rows += [f"c{i},Receive,2024-03-01T08:00:00Z", f"c{i},{'Approve' if i % 2 else 'Reject'},2024-03-01T09:00:00Z",
                 f"c{i},Close,2024-03-01T10:00:00Z"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                               mapping=MAPPING)["log_id"]
    xml, meta = pnml.discovered_pnml(log)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>') and "claims (case) discovered net" in xml
    assert meta["fitting_case_share"] == 1.0 and meta["transitions"] >= 4
    tree = discovery.discover(log)["tree"]
    net = pnml.tree_to_net(tree)
    assert _replays(net, ("Receive", "Approve", "Close")) and not _replays(net, ("Receive", "Close"))


def test_unknown_operator_is_refused():
    with pytest.raises(workspace.EventLogWorkspaceError):
        pnml.tree_to_net({"op": "or", "children": []})


def test_route_is_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/model-discovery.pnml"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?noise=0.1", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml"), r.text
    assert "<pnml>" in r.text and r.headers["x-owg-petri-net"].startswith("places=") and "attachment" in r.headers["content-disposition"]
    assert client.get(path + "?noise=1", headers=owner).status_code == 422
    assert client.get(path + "?resource=nobody", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
