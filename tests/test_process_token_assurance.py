"""Independent expected marking coverage for reviewed structural designs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared.process_model import ProcessModel
from shared.process_parallel import EdgeTokenModel, replay_node_ids
from shared.process_token_assurance import analyze, analyze_saved


def graph(kind="decision", loop=False):
    nodes = [("start", "start"), ("split", kind), ("a", "activity"),
             ("b", "activity"), ("join", {"decision": "routing",
                                           "parallel_split": "parallel_join",
                                           "inclusive_split": "inclusive_join"}[kind]),
             ("end", "end")]
    pairs = [("start", "split"), ("split", "a"), ("split", "b"),
             ("a", "join"), ("b", "join"), ("join", "end")]
    rule_map = {("split", "a"): "ra", ("split", "b"): "rb"} if kind != "parallel_split" else {}
    if loop:
        # An exclusive decision after the paired block may repeat it.
        nodes.append(("retry", "decision"))
        pairs[-1:] = [("join", "retry"), ("retry", "end"), ("retry", "split")]
        # A paired split cannot have two incoming edges; insert a routing merge.
        nodes.append(("merge", "routing"))
        pairs[0] = ("start", "merge")
        pairs[-1] = ("retry", "merge")
        pairs.append(("merge", "split"))
        rule_map.update({("retry", "end"): "finish", ("retry", "merge"): "again"})
    return ProcessModel(
        goal="Review a selected structural workflow",
        baseline_source="user_declared", baseline_path=["A", "B"],
        activities=[{"id": key, "label": key.capitalize(), "kind": node_kind} for key, node_kind in nodes],
        transitions=[{"source": a, "target": b, "rule_id": rule_map.get((a,b))} for a,b in pairs],
        business_rules=[{"id": rule, "text": "Declared branch condition " + rule} for rule in rule_map.values()],
        entry_activity_id="start", terminal_activity_ids=["end"],
    )


@pytest.mark.parametrize("kind,states,firings,shortest", [
    ("decision", 8, 8, 5),
    ("parallel_split", 11, 12, 6),
    ("inclusive_split", 15, 18, 5),
])
def test_marking_exploration_and_completed_witnesses(kind, states, firings, shortest):
    model = graph(kind)
    result = analyze(model)
    assert result["complete"] and result["completion_reachable"]
    assert result["every_reachable_marking_can_complete"]
    assert result["known_deadlock_count"] == result["known_cyclic_component_count"] == 0
    assert result["activities_with_complete_witness"] == len(model.activities)
    assert result["transitions_with_complete_witness"] == len(model.transitions)
    assert result["shortest_completion"]["firing_count"] == shortest
    # Compare all schedules to the same engine used by generated agent projects.
    # For inclusive choices a node sequence can admit multiple edge histories,
    # so the independently replayed union must contain the specific witness.
    for item in result["activities"] + result["transitions"]:
        witness = item["witness"]
        assert not witness["truncated"]
        replay = replay_node_ids(witness["node_ids"], model)
        assert set(witness["transition_indices"]) <= set(replay["transition_indices"])
        if "index" in item:
            assert item["index"] in witness["transition_indices"]
        else:
            assert item["activity_id"] in witness["node_ids"]
    # Exact independent state and arc counts on the hand-sized fixtures.
    assert result["states_discovered"] == states
    assert result["firings_examined"] == firings
    assert not result["business_rules_evaluated"] and not result["execution_authorized"]


@pytest.mark.parametrize("kind", ["decision", "parallel_split", "inclusive_split"])
def test_loops_are_not_deadlocks_and_can_still_complete(kind):
    model = graph(kind, loop=True)
    result = analyze(model)
    assert result["complete"] and result["every_reachable_marking_can_complete"]
    assert result["known_cyclic_component_count"] >= 1
    assert result["known_deadlock_count"] == 0
    for item in result["cycle_examples"]:
        assert item["cycle"]["firing_count"] > 0
        assert item["known_completion_from_cycle"]
        # The reported prefix reaches a marking that can traverse the reported
        # cycle and return to the same marking (check possible markings directly).
        engine = EdgeTokenModel(model)
        def advance(markings, sequence):
            for node_id in sequence:
                markings = {following for marking in markings
                            for node, following, _ in engine.firings(marking)
                            if node.id == node_id}
            return markings
        reached = advance({engine.initial}, item["prefix"]["node_ids"])
        assert reached
        assert any(m in advance({m}, item["cycle"]["node_ids"]) for m in reached)


@pytest.mark.parametrize("budgets", [{"max_states": 1}, {"max_firings": 1}])
def test_budget_exhaustion_does_not_invent_negative_conclusions(budgets):
    result = analyze(graph("inclusive_split"), **budgets)
    assert not result["complete"]
    assert result["completion_reachable"] is None
    assert result["every_reachable_marking_can_complete"] is None
    assert result["noncompletable_marking_count"] is None
    assert not result["deadlock_count_exact"]
    assert all(row["status"] == "unknown_budget" for row in result["activities"])


def test_multiple_terminals_preserve_stable_ids_not_display_labels():
    value = graph().model_dump()
    value["activities"] = [n for n in value["activities"] if n["id"] not in ("join", "end")]
    for n in value["activities"]:
        if n["id"] in ("a", "b"):
            n["label"] = "Resolve"
    value["transitions"] = [e for e in value["transitions"] if e["source"] in ("start", "split")]
    value["terminal_activity_ids"] = ["a", "b"]
    result = analyze(ProcessModel.model_validate(value))
    assert {t["activity_id"] for t in result["terminals"]} == {"a", "b"}
    for t in result["terminals"]:
        assert t["witness"]["node_ids"][-1] == t["activity_id"]


def test_report_preserves_revision_and_is_repeatable():
    model = graph("inclusive_split")
    packet = {"workflow_id": "test", "revision": 3, "content_sha256": "a"*64,
              "process_model": model.model_dump()}
    first = analyze_saved(packet)
    assert first == analyze_saved(packet)
    assert first["reference"] == {k: packet[k] for k in ("workflow_id", "revision", "content_sha256")}
    assert first["policy_compliance_verified"] is False
    assert first["outcomes_verified"] is False


@pytest.mark.parametrize("budget", [0, -1, True, 100_001])
def test_invalid_work_budget_refused(budget):
    with pytest.raises(ValueError):
        analyze(graph(), max_firings=budget)


def test_real_http_revision_digest_and_dashboard_scope(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_process_token_assurance import graph
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    saved = client.post("/v1/process-catalog", json={"title":"Assurance",
        "user_confirmed":True,"process_model":graph().model_dump()}, headers=headers)
    assert saved.status_code == 200, saved.text
    rec = saved.json()
    params = {k:rec[k] for k in ("workflow_id","revision","content_sha256")}
    url = "/v1/process-catalog/token-assurance"
    assert client.get(url, params=params, headers=bearer).status_code in (401,403)
    response = client.get(url, params=params, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["reference"] == params
    assert response.json()["complete"]
    assert client.get(url, params={**params,"content_sha256":"0"*64},headers=headers).status_code == 409
    assert client.get(url, params={**params,"revision":99},headers=headers).status_code == 404
"""
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'tests');" + code],
                            cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
