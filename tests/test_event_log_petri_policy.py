from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import event_log_petri_policy as policy
from server import event_log_petri_soundness as soundness
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError


def activity(label):
    return {"op": "activity", "activity": label}


def node(op, *children):
    return {"op": op, "children": list(children)}


def model(tree):
    return pnml.to_pnml(pnml.tree_to_net(tree), "model").encode()


A, B, C = map(activity, "ABC")
TAU = {"op": "tau"}
RESPONSE = {"template": "response", "a": "A", "b": "B"}
CHAIN = {"template": "chain_response", "a": "A", "b": "B"}
PRECEDENCE = {"template": "precedence", "a": "A", "b": "B"}
NOT_BOTH = {"template": "not_coexistence", "a": "A", "b": "B"}
EXACT_A = {"template": "existence", "a": "A", "min": 1, "max": 1}


def spec(*rules):
    return {"rules": list(rules)}


def oracle(rule, trace):
    a = rule["a"]
    template = rule["template"]
    if template == "existence":
        count = trace.count(a)
        return True, count < rule["min"] or (rule["max"] is not None and count > rule["max"])
    b = rule["b"]
    ai = [i for i, x in enumerate(trace) if x == a]
    bi = [i for i, x in enumerate(trace) if x == b]
    if template == "response":
        return bool(ai), any(not any(j > i for j in bi) for i in ai)
    if template == "chain_response":
        return bool(ai), any(i + 1 >= len(trace) or trace[i+1] != b for i in ai)
    if template == "precedence":
        return bool(bi), any(not any(i < j for i in ai) for j in bi)
    return bool(ai or bi), bool(ai and bi)


@pytest.mark.parametrize("rule", [RESPONSE, CHAIN, PRECEDENCE, NOT_BOTH, EXACT_A,
    {"template": "existence", "a": "A", "min": 2, "max": None},
    {"template": "existence", "a": "A", "min": 0, "max": 0}])
def test_finite_monitor_agrees_with_independent_trace_oracle(rule):
    rule = policy.parse_rules(spec(rule))[0]
    for n in range(7):
        for trace in itertools.product("ABC", repeat=n):
            state = 0
            for label in trace:
                state = policy._step(rule, state, label)
                state = policy._step(rule, state, None)
            assert policy._outcome(rule, state) == oracle(rule, trace), (rule, trace)


def test_verified_rule_and_silent_steps_do_not_break_chain():
    net = model(node("seq", A, TAU, B))
    r = policy.review(net, spec(RESPONSE, CHAIN, PRECEDENCE, EXACT_A))["baseline"]
    assert r["joint"]["status"] == "verified"
    assert r["joint"]["all_completed_traces_satisfy"] is True
    assert r["joint"]["witnesses"]["activated_satisfying"]["activities"] == ["A", "B"]
    assert all(x["status"] == "verified" for x in r["rules"])


def test_parallel_model_allows_policy_violation_despite_one_good_order():
    r = policy.review(model(node("and", A, B)), spec(PRECEDENCE))["baseline"]["joint"]
    assert r["status"] == "counterexample"
    assert r["witnesses"]["violating"]["activities"] == ["B", "A"]
    assert r["witnesses"]["activated_satisfying"]["activities"] == ["A", "B"]
    assert r["all_completed_traces_satisfy"] is False


def test_vacuous_satisfaction_distinct_from_activated_satisfaction():
    r = policy.review(model(node("xor", C, TAU)), spec(RESPONSE))["baseline"]["joint"]
    assert r["status"] == "vacuous_only"
    assert r["witnesses"]["vacuous"]["activities"] == []
    assert r["activated_satisfying_trace_possible"] is False
    assert r["satisfying_trace_possible"] is True
    assert r["violating_trace_possible"] is False


def test_empty_trace_violates_required_existence():
    r = policy.review(model(TAU), spec(EXACT_A))["baseline"]["joint"]
    assert r["witnesses"]["violating"]["activities"] == []
    assert r["satisfying_trace_possible"] is False


def test_each_rule_can_be_satisfied_but_no_trace_satisfies_all():
    must_a = {"template": "existence", "a": "A", "min": 1}
    must_b = {"template": "existence", "a": "B", "min": 1}
    r = policy.review(model(node("xor", A, B)), spec(must_a, must_b))["baseline"]
    assert r["joint"]["satisfying_trace_possible"] is False
    assert r["joint"]["violating_trace_possible"] is True
    assert all(x["satisfying_trace_possible"] is True for x in r["rules"])


def test_loop_count_bound_and_shortest_visible_witness():
    net = model(node("loop", A, B))
    r = policy.review(net, spec(EXACT_A))["baseline"]["joint"]
    assert r["complete"] is True
    assert r["witnesses"]["violating"]["activities"] == ["A", "B", "A"]
    assert r["witnesses"]["activated_satisfying"]["activities"] == ["A"]


def test_candidate_introduces_and_removes_policy_violations():
    base, candidate = model(node("seq", A, B)), model(node("seq", B, A))
    forward = policy.review(base, spec(PRECEDENCE), candidate)
    reverse = policy.review(candidate, spec(PRECEDENCE), base)
    assert forward["changes"]["joint"]["violating_trace_possibility"] == "introduced"
    assert forward["changes"]["rules"][0]["satisfying_trace_possibility"] == "removed"
    assert reverse["changes"]["joint"]["violating_trace_possibility"] == "removed"
    assert forward["rules_sha256"] == reverse["rules_sha256"]
    assert forward["baseline"]["model"]["pnml_sha256"] == reverse["candidate"]["model"]["pnml_sha256"]


def test_no_completed_trace_not_reported_as_verified():
    net = model(A).replace(b'<text>1</text></place></marking>', b'<text>2</text></place></marking>')
    # Generate a structurally valid PNML with an unreachable final marking.
    from xml.etree import ElementTree as ET
    root = ET.fromstring(net)
    final_text = root.find(".//finalmarkings/marking/place/text")
    assert final_text is not None
    final_text.text = "2"
    r = policy.review(ET.tostring(root), spec(RESPONSE))["baseline"]["joint"]
    assert r["status"] == "no_completed_trace"
    assert r["all_completed_traces_satisfy"] is None
    assert r["completed_trace_possible"] is False


def test_graph_and_policy_limits_are_not_proofs(monkeypatch):
    monkeypatch.setattr(soundness, "MAX_STATES", 1)
    r = policy.review(model(A), spec(EXACT_A))["baseline"]["joint"]
    assert r["status"] == "inconclusive"
    assert r["violating_trace_possible"] is None
    monkeypatch.setattr(soundness, "MAX_STATES", 5000)
    monkeypatch.setattr(policy, "MAX_TOTAL_WORK", 0)
    r = policy.review(model(A), spec(EXACT_A))["baseline"]["joint"]
    assert r["limit"] == "total_work_limit"
    assert r["all_completed_traces_satisfy"] is None


def test_real_counterexample_survives_later_product_limit(monkeypatch):
    # Direct synthetic complete marking graph: initial is accepting, then
    # a visible loop. Zero occurrences of A is a genuine counterexample.
    graph = {"edges": [{"A": {0}}], "final": 0, "complete": True}
    monkeypatch.setattr(policy, "MAX_PRODUCT_STATES", 1)
    r = policy._search(graph, policy.parse_rules(spec(EXACT_A)), policy._Budget())
    assert r["limit"] == "product_state_limit"
    assert r["violating_trace_possible"] is True
    assert r["satisfying_trace_possible"] is None
    assert r["witnesses"]["violating"]["activities"] == []


@pytest.mark.parametrize("bad", [
    {"rules": [RESPONSE], "cohort": {}}, spec(RESPONSE, RESPONSE),
    spec({"template": "response_within", "a": "A", "b": "B", "seconds": 60}),
    spec({"template": "segregation_of_duties", "a": "A", "b": "B"}),
    spec({"template": "response", "a": "A", "b": "A"}), spec(),
    spec({"template": "existence", "a": "A", "min": 4, "max": 2}),
])
def test_unsupported_or_malformed_rules_refused(bad):
    with pytest.raises(EventLogWorkspaceError):
        policy.parse_rules(bad)


def test_policy_api_owner_only_strict_framing_and_no_store(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_pnml as pnml
def model(label):
    return pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":label}),"model").encode()
a,b = model("A"),model("B")
rules = json.dumps({"rules":[{"template":"existence","a":"A","min":1}]}).encode()
def frame(r,a,b=b""):
    return len(r).to_bytes(4,"big")+r+len(a).to_bytes(4,"big")+a+b
body=frame(rules,a,b)
path="/v1/event-logs/petri-policy-review"
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),"Content-Type":"application/octet-stream"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"Content-Type":"application/octet-stream"}
    assert client.post(path,content=body,headers=bearer).status_code==403
    response=client.post(path,content=body,headers=owner)
    assert response.status_code==200,response.text
    assert response.json()["changes"]["joint"]["violating_trace_possibility"]=="introduced"
    assert response.headers["cache-control"]=="no-store"
    response=client.post(path,content=frame(rules,a),headers=owner)
    assert response.status_code==200 and response.json()["candidate"] is None
    assert client.post(path,content=frame(b'{"rules":[],"rules":[]}',a),headers=owner).status_code==422
    assert client.post(path,content=frame(b'{"rules":NaN}',a),headers=owner).status_code==422
    assert client.post(path,content=b"x",headers=owner).status_code==422
    assert client.post(path,content=(65537).to_bytes(4,"big")+b"xxxx",headers=owner).status_code==413
    assert client.post(path,content=body,headers={**owner,"Content-Type":"text/plain"}).status_code==415
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout
