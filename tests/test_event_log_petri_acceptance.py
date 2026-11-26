from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from server import event_log_petri_acceptance as acceptance
from server import event_log_petri_soundness as soundness
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError


def activity(name):
    return {"op": "activity", "activity": name}


def node(op, *children):
    return {"op": op, "children": list(children)}


def model(tree):
    return pnml.to_pnml(pnml.tree_to_net(tree), "test").encode()


A, B, C = map(activity, "ABC")
TAU = {"op": "tau"}


def suite(*rows):
    return {"format": acceptance.SUITE_FORMAT,
            "cases": [{"id": str(i), "trace": trace, "expect": expect}
                      for i, (trace, expect) in enumerate(rows)]}


def check(net, trace, **expect):
    return acceptance.review(net, suite((trace, expect)))["baseline"]["cases"][0]


def test_exact_completed_and_prefix_are_distinct():
    net = model(node("seq", A, TAU, B))
    r = check(net, ["A"], completed=False, fireable=True, finishable=True, exact_next=["B"])
    assert r["status"] == "passed"
    assert r["observed"]["completion_suffix"]["activities"] == ["B"]
    assert check(net, ["A", "B"], completed=True, finishable=True, exact_next=[])["status"] == "passed"
    assert check(net, ["A", "C"], completed=False, fireable=False, finishable=False)["status"] == "passed"
    assert check(net, ["A", "C"], fireable=True)["observed"]["matched_prefix_length"] == 1


def test_nondeterministic_duplicate_labels_and_parallel_next_sets():
    net = model(node("xor", node("seq", A, B), node("seq", A, C)))
    assert check(net, ["A"], exact_next=["B", "C"])["status"] == "passed"
    net = model(node("and", A, B))
    assert check(net, [], exact_next=["A", "B"])["status"] == "passed"
    assert check(net, ["B"], exact_next=["A"])["status"] == "passed"


def test_empty_trace_and_loop():
    net = model(node("xor", TAU, A))
    assert check(net, [], completed=True, exact_next=["A"])["status"] == "passed"
    net = model(node("loop", A, B))
    assert check(net, ["A"], completed=True, required_next=["B"], forbidden_next=["C"])["status"] == "passed"
    assert check(net, ["A", "B"], completed=False, finishable=True, exact_next=["A"])["status"] == "passed"


def test_fireable_dead_end_is_not_viable_next():
    # Change one branch's final destination so B is fireable but cannot finish.
    root = ET.fromstring(model(node("xor", A, B)))
    page = root.find(".//page")
    assert page is not None
    ET.SubElement(page, "place", id="dead")
    transition = next(t for t in page.findall("transition") if t.findtext("name/text") == "B")
    arc = next(a for a in page.findall("arc") if a.get("source") == transition.get("id"))
    arc.set("target", "dead")
    net = ET.tostring(root)
    assert check(net, [], exact_next=["A"], forbidden_next=["B"])["status"] == "passed"
    assert check(net, ["B"], fireable=True, completed=False, finishable=False, exact_next=[])["status"] == "passed"


def test_exact_next_refuses_additional_activities():
    r = check(model(node("xor", A, B)), [], exact_next=["A"])
    assert r["status"] == "failed"
    assert r["assertions"][0]["unexpected_observed"] == ["B"]


def test_shortest_suffix_is_by_visible_length_not_total_firings():
    net = model(node("xor", node("seq", A, B), node("seq", TAU, TAU, TAU, C)))
    r = check(net, [], finishable=True)
    assert r["observed"]["completion_suffix"]["activities"] == ["C"]
    assert r["observed"]["completion_suffix"]["shortest_proven"] is True


def test_revision_regression_and_improvement():
    spec = suite((["A"], {"completed": True}), (["B"], {"completed": True}))
    r = acceptance.review(model(A), spec, model(B))
    assert r["changes"]["regressions"] == r["changes"]["improvements"] == 1
    assert [x["change"] for x in r["changes"]["cases"]] == ["regression", "improvement"]
    assert r == acceptance.review(model(A), spec, model(B))


def test_incomplete_graph_never_certifies_absence(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    r = check(model(A), [], fireable=True, completed=False, forbidden_next=["A"])
    assert r["status"] == "inconclusive"
    assert r["observed"]["fireable"] is True
    assert r["observed"]["completed"] is None
    assert r["observed"]["next_activities_complete"] is False


def test_work_limit_is_unknown_not_failure(monkeypatch):
    monkeypatch.setattr(acceptance, "MAX_WORK", 0)
    r = acceptance.review(model(A), suite((["A"], {"completed": True})))["baseline"]
    assert r["suite_passed"] is None
    assert r["work_limit_reached"] is True
    assert r["counts"]["inconclusive"] == 1


@pytest.mark.parametrize("spec", [
    {},
    suite(),
    suite(([], {"completed": 1})),
    suite(([""], {"completed": True})),
    suite((["A"], {"unknown": True})),
    suite((["A"], {"required_next": ["B"], "forbidden_next": ["B"]})),
    suite((["A"], {"required_next": ["B"], "exact_next": []})),
    suite((["A"], {"exact_next": ["B", "B"]})),
])
def test_invalid_suite_refused(spec):
    with pytest.raises(EventLogWorkspaceError):
        acceptance.parse_suite(spec)


def test_cli_exit_codes_and_strict_input(tmp_path):
    baseline = tmp_path / "baseline.pnml"
    candidate = tmp_path / "candidate.pnml"
    scenario = tmp_path / "suite.json"
    baseline.write_bytes(model(A))
    candidate.write_bytes(model(B))
    scenario.write_text(json.dumps(suite((["A"], {"completed": True}))), encoding="utf-8")
    args = [sys.executable, "-m", "server.event_log_petri_acceptance",
            "--baseline", str(baseline), "--suite", str(scenario)]
    done = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["baseline"]["suite_passed"] is True
    done = subprocess.run(args + ["--candidate", str(candidate)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["changes"]["regressions"] == 1
    scenario.write_text('{"format":"x","format":"y"}', encoding="utf-8")
    done = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
    assert not done.stdout


def test_http_owner_only_and_no_retention(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_pnml as pnml
def model(label):
    return pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":label}),"model").encode()
a,b = model("A"),model("B")
suite = json.dumps({"format":"openworkgraph.petri_acceptance_suite.v1",
                   "cases":[{"id":"expected","trace":["A"],"expect":{"completed":True}}]}).encode()
def frame(s,a,b=b""):
    return len(s).to_bytes(4,"big")+s+len(a).to_bytes(4,"big")+a+b
path="/v1/event-logs/petri-acceptance"
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),"Content-Type":"application/octet-stream"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"Content-Type":"application/octet-stream"}
    body=frame(suite,a,b)
    assert client.post(path,content=body,headers=bearer).status_code==403
    response=client.post(path,content=body,headers=owner)
    assert response.status_code==200,response.text
    assert response.json()["changes"]["regressions"]==1
    assert response.headers["cache-control"]=="no-store"
    assert client.post(path,content=frame(b'{"format":"a","format":"b"}',a),headers=owner).status_code==422
    assert client.post(path,content=b"x",headers=owner).status_code==422
    assert client.post(path,content=(262145).to_bytes(4,"big")+b"xxxx",headers=owner).status_code==413
    assert client.post(path,content=body,headers={**owner,"Content-Type":"text/plain"}).status_code==415
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout


def test_ambiguous_prefix_can_finish_but_not_from_every_matching_marking():
    root = ET.fromstring(model(node("xor", A, A)))
    page = root.find(".//page")
    assert page is not None
    ET.SubElement(page, "place", id="dead")
    transitions = [t for t in page.findall("transition") if t.findtext("name/text") == "A"]
    assert len(transitions) == 2
    arc = next(a for a in page.findall("arc") if a.get("source") == transitions[0].get("id"))
    arc.set("target", "dead")
    result = check(ET.tostring(root), ["A"], fireable=True, finishable=True,
                   all_prefix_states_finishable=False, completed=True)
    assert result["status"] == "passed"
    observed = result["observed"]
    assert observed["matched_markings"] == 2
    assert observed["known_completable_markings"] == 1
    assert observed["noncompletable_markings"] == 1


def test_all_prefix_states_finishable_is_nonvacuous_and_requires_complete_graph(monkeypatch):
    net = model(node("seq", A, B))
    assert check(net, ["A"], all_prefix_states_finishable=True)["status"] == "passed"
    assert check(net, ["C"], all_prefix_states_finishable=False)["status"] == "passed"
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    result = check(net, [], all_prefix_states_finishable=False)
    assert result["status"] == "inconclusive"
    assert result["observed"]["noncompletable_markings"] is None
