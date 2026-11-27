from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server import event_log_petri_acceptance as acceptance
from server import event_log_petri_language as language
from server import event_log_petri_mutation as mutation
from server import event_log_pnml as pnml
from server.event_log_workspace import EventLogWorkspaceError


def activity(label):
    return {"op": "activity", "activity": label}


def model(*labels):
    tree = activity(labels[0]) if len(labels) == 1 else {
        "op": "seq", "children": [activity(x) for x in labels]}
    return pnml.to_pnml(pnml.tree_to_net(tree), "reference").encode()


def spec(trace, expect, **settings):
    return {"suite": {"format": acceptance.SUITE_FORMAT, "cases": [
        {"id": "reviewed", "trace": trace, "expect": expect}]}, "settings": settings}


def test_full_trace_detects_each_selected_fault_and_reference_stays_unchanged():
    data = model("A", "B")
    settings = spec(["A", "B"], {"completed": True}, max_mutants=4)
    original = bytes(data)
    r = mutation.review(data, settings)
    assert r["counts"] == {"detected": 4, "survived": 0, "inconclusive": 0}
    assert r["detected_fraction_bounds"] == {"lower": 1.0, "upper": 1.0}
    assert [row["recipe"]["operator"] for row in r["mutants"]] == list(mutation.OPERATORS)
    assert all(row["failed_case_ids"] == ["reviewed"] for row in r["mutants"])
    assert data == original
    assert r == mutation.review(data, settings)
    assert len({row["pnml_sha256"] for row in r["mutants"]}) == 4


def test_survivor_witnesses_become_exact_regression_scenarios():
    data = model("A", "B")
    r = mutation.review(data, spec([], {"fireable": True},
                        operators=["hide_activity"], max_mutants=1))
    row = r["mutants"][0]
    assert row["status"] == "survived"
    assert row["language_comparison"]["equivalent"] is False
    assert r["survivors_with_proven_language_difference"] == 1
    assert len(row["suggested_cases"]) == 2
    improved = {"format": acceptance.SUITE_FORMAT, "cases": r["suite"]["cases"] + row["suggested_cases"]}
    mutant = mutation._mutate(data, row["recipe"])
    report = acceptance.review(data, improved, mutant)
    assert report["baseline"]["suite_passed"] is True
    assert report["candidate"]["suite_passed"] is False
    assert report["changes"]["regressions"] == 2
    second = mutation.review(data, {"suite": improved, "settings": r["settings"]})
    assert second["counts"]["detected"] == 1


def test_mutation_selection_does_not_claim_full_fault_coverage():
    r = mutation.review(model("A", "B", "C"), spec([], {"fireable": True}, max_mutants=2))
    assert r["selection_truncated"] is True
    assert r["available_mutants"] > r["selected_mutants"] == 2
    assert [m["recipe"]["operator"] for m in r["mutants"]] == ["hide_activity", "rename_activity"]


def test_baseline_must_pass_before_mutants_are_tested():
    r = mutation.review(model("A"), spec(["B"], {"completed": True}))
    assert r["status"] == "baseline_not_passed"
    assert r["mutants"] == [] and r["detected_fraction_bounds"] is None


def test_no_applicable_operator_returns_no_denominator():
    r = mutation.review(model("A"), spec(["A"], {"completed": True},
                       operators=["remove_transition"]))
    assert r["status"] == "no_applicable_mutations"
    assert r["available_mutants"] == 0
    assert r["detected_fraction_bounds"] is None


def test_inconclusive_mutant_counted_in_bounds(monkeypatch):
    original = acceptance._review_graph
    count = 0

    def review(graph, suite):
        nonlocal count
        count += 1
        result = original(graph, suite)
        if count == 2:
            result["cases"][0]["status"] = "inconclusive"
        return result

    monkeypatch.setattr(acceptance, "_review_graph", review)
    r = mutation.review(model("A"), spec(["A"], {"completed": True},
                       operators=["hide_activity"], max_mutants=1))
    assert r["counts"]["inconclusive"] == 1
    assert r["detected_fraction_bounds"] == {"lower": 0.0, "upper": 1.0}


def test_semantically_equivalent_removed_duplicate_transition_not_called_a_defect():
    a = activity("A")
    data = pnml.to_pnml(pnml.tree_to_net({"op": "xor", "children": [a, a]}), "reference").encode()
    r = mutation.review(data, spec(["A"], {"completed": True},
                       operators=["remove_transition"], max_mutants=8))
    survivors = [m for m in r["mutants"] if m["status"] == "survived"]
    assert survivors
    assert any(m["language_comparison"]["equivalent"] is True for m in survivors)
    assert all(not m["suggested_cases"] for m in survivors
               if m["language_comparison"]["equivalent"] is True)


@pytest.mark.parametrize("settings", [
    {"max_mutants": 0}, {"max_mutants": 9}, {"max_mutants": True},
    {"operators": []}, {"operators": ["unknown"]}, {"operators": ["hide_activity", "hide_activity"]},
    {"extra": True},
])
def test_invalid_settings_refused(settings):
    with pytest.raises(EventLogWorkspaceError):
        mutation.review(model("A"), spec([], {"fireable": True}, **settings))


def test_graph_reuse_retains_public_api_results():
    a, b = model("A"), model("B")
    assert language.compare(a, b) == language._compare_graphs(language._graph(a), language._graph(b))
    suite, _ = acceptance.parse_suite(spec([], {"fireable": True})["suite"])
    assert acceptance._review(a, suite) == acceptance._review_graph(language._graph(a), suite)


def test_owner_only_strict_framing_and_no_store(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_pnml as pnml
data = pnml.to_pnml(pnml.tree_to_net({"op":"activity","activity":"A"}), "reference").encode()
spec = {"suite":{"format":"openworkgraph.petri_acceptance_suite.v1","cases":[
    {"id":"required","trace":["A"],"expect":{"completed":True}}]},
    "settings":{"max_mutants":1,"operators":["hide_activity"]}}
raw = json.dumps(spec).encode()
def frame(raw):
    return len(raw).to_bytes(4,"big")+raw+data
path="/v1/event-logs/petri-mutation-review"
with TestClient(app) as client:
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session(),"Content-Type":"application/octet-stream"}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token(),"Content-Type":"application/octet-stream"}
    assert client.post(path,content=frame(raw),headers=bearer).status_code==403
    response=client.post(path,content=frame(raw),headers=owner)
    assert response.status_code==200,response.text
    assert response.json()["counts"]["detected"]==1
    assert response.headers["cache-control"]=="no-store"
    assert client.post(path,content=frame(b'{"suite":{},"suite":{}}'),headers=owner).status_code==422
    assert client.post(path,content=b"x",headers=owner).status_code==422
    assert client.post(path,content=(300000).to_bytes(4,"big")+b"xxxx",headers=owner).status_code==413
    assert client.post(path,content=frame(raw),headers={**owner,"Content-Type":"text/plain"}).status_code==415
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr + done.stdout
