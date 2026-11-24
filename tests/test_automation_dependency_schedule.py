"""R15 local portfolio dependency schedule review: declared facts, no deadline forecast."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_dependency_schedule as review
from server import automation_pipeline as pipeline

ALL_FIVE = {name: 5 for name in pipeline.EASE_QUESTIONS}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def create(title: str, quarter: str = "", dependencies: list[str] | None = None):
    idea = pipeline.create({"title": title})
    if quarter or dependencies:
        idea = pipeline.plan(idea["idea_id"], {
            "target_quarter": quarter, "depends_on": dependencies or [],
            "expected_revision": idea["revision"]})
    return idea


def implement(idea: dict):
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    idea = pipeline.assess(idea["idea_id"], {"ease": ALL_FIVE, "cases_per_month": 10,
        "minutes_per_case": 30, "eligible_share": 1, "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    return pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})


def test_finds_direct_and_transitive_plan_conflicts_with_paths():
    foundation = create("Foundation", "2027-Q3")
    middle = create("Middle", "2027-Q2", [foundation["idea_id"]])
    early = create("Early target", "2027-Q1", [middle["idea_id"]])
    result = review.review()
    assert result["active_ideas"] == 3
    assert result["active_ideas_with_dependencies"] == 2
    issues = [r for r in result["risk_candidates"] if r["kind"] == "dependency_target_after_dependent"]
    assert len(issues) == 3  # Middle -> Foundation, Early -> Middle and Foundation.
    assert any(r["idea_id"] == early["idea_id"] and r["dependency_id"] == foundation["idea_id"]
               and r["path"] == [early["idea_id"], middle["idea_id"], foundation["idea_id"]] for r in issues)
    assert result["schedule_feasibility_verified"] is False
    assert result["source_systems_queried"] is False


def test_implemented_dependency_no_longer_blocks_review():
    foundation = create("Done", "2028-Q4")
    child = create("Not yet", "2027-Q1", [foundation["idea_id"]])
    assert any(x["dependency_id"] == foundation["idea_id"] for x in review.review()["risk_candidates"])
    implement(foundation)
    result = review.review()
    assert result["risk_candidates"] == []
    assert result["status"] == "no_declared_conflicts_detected"
    assert result["active_ideas"] == 1


def test_rejected_and_unscheduled_dependencies_require_review():
    foundation = create("Undated")
    child = create("Promised", "2027-Q2", [foundation["idea_id"]])
    review_before = review.review()
    assert any(r["kind"] == "unscheduled_dependency" and r["idea_id"] == child["idea_id"]
               for r in review_before["risk_candidates"])
    pipeline.transition(foundation["idea_id"], {"to": "rejected", "expected_revision": foundation["revision"]})
    result = review.review()
    kinds = {r["kind"] for r in result["risk_candidates"] if r["idea_id"] == child["idea_id"]}
    assert {"blocked_dependency", "unscheduled_dependency"} <= kinds


def test_old_missing_dependency_is_disclosed_not_silently_ignored():
    child = create("Legacy", "2027-Q1")
    with closing(pipeline._connect()) as conn, conn:
        raw = conn.execute("SELECT body FROM ideas WHERE idea_id = ?", (child["idea_id"],)).fetchone()[0]
        body = json.loads(raw)
        body["planning"] = {"owner": "", "team": "", "budget": None, "target_quarter": "2027-Q1",
                            "depends_on": ["idea_" + "f" * 16]}
        conn.execute("UPDATE ideas SET body = ? WHERE idea_id = ?", (json.dumps(body), child["idea_id"]))
    result = review.review()
    assert any(r["kind"] == "deleted_or_unknown_dependency" for r in result["risk_candidates"])
    assert result["review_authorizes_implementation"] is False


def test_corrupted_legacy_cycle_fails_closed_and_truncates(monkeypatch):
    a = create("A")
    b = create("B", "2027-Q1", [a["idea_id"]])
    with closing(pipeline._connect()) as conn, conn:
        raw = conn.execute("SELECT body FROM ideas WHERE idea_id = ?", (a["idea_id"],)).fetchone()[0]
        body = json.loads(raw)
        body["planning"] = {"owner": "", "team": "", "budget": None, "target_quarter": "2027-Q2",
                            "depends_on": [b["idea_id"]]}
        conn.execute("UPDATE ideas SET body = ? WHERE idea_id = ?", (json.dumps(body), a["idea_id"]))
    result = review.review()
    assert any(r["kind"] == "dependency_cycle" for r in result["risk_candidates"])
    monkeypatch.setattr(review, "MAX_RISKS", 1)
    limited = review.review()
    assert len(limited["risk_candidates"]) == 1
    assert limited["risk_candidates_truncated"] is True
    assert limited["all_ideas_evaluated"] is False


def test_dependency_waves_use_longest_prerequisite_chain_and_exact_snapshot():
    base = create("Foundation", "2027-Q2")
    side = create("Independent", "2027-Q1")
    middle = create("Middle", "2027-Q3", [base["idea_id"]])
    end = create("End", "2027-Q1", [middle["idea_id"], side["idea_id"], base["idea_id"]])
    result = review.review()["dependency_waves"]
    assert result["layered_idea_count"] == 4 and result["wave_count"] == 3
    assert result["precedence_order_complete"]
    rows = {i["idea_id"]: i for i in result["ideas"]}
    assert rows[base["idea_id"]]["wave"] == rows[side["idea_id"]]["wave"] == 1
    assert rows[middle["idea_id"]]["wave"] == 2
    assert rows[end["idea_id"]]["wave"] == 3
    assert rows[end["idea_id"]]["latest_declared_target_in_unfinished_chain"] == "2027-Q3"
    assert rows[end["idea_id"]]["status"] == "waiting_on_earlier_wave"
    assert not result["resources_or_durations_modeled"] and not result["execution_authorized"]
    assert review.review()["dependency_waves"]["portfolio_basis_sha256"] == result["portfolio_basis_sha256"]
    implement(base)
    changed = review.review()["dependency_waves"]
    assert changed["portfolio_basis_sha256"] != result["portfolio_basis_sha256"]
    assert changed["active_idea_count"] == 3
    assert {i["idea_id"]: i["wave"] for i in changed["ideas"]}[middle["idea_id"]] == 1


def test_dependency_waves_propagate_blockers_but_not_to_independent_ideas():
    held = create("Hold")
    rejected = create("Rejected")
    direct = create("Direct", dependencies=[held["idea_id"], rejected["idea_id"]])
    indirect = create("Indirect", dependencies=[direct["idea_id"]])
    independent = create("Independent")
    pipeline.transition(held["idea_id"], {"to": "on_hold", "expected_revision": held["revision"]})
    pipeline.transition(rejected["idea_id"], {"to": "rejected", "expected_revision": rejected["revision"]})
    result = review.review()["dependency_waves"]
    rows = {i["idea_id"]: i for i in result["ideas"]}
    assert result["blocked_idea_count"] == 3
    assert rows[direct["idea_id"]]["status"] == "blocked_by_declared_state"
    assert {b["state"] for b in rows[direct["idea_id"]]["direct_blockers"]} == {"on_hold", "rejected"}
    assert rows[indirect["idea_id"]]["status"] == "depends_on_blocked_idea"
    assert rows[indirect["idea_id"]]["blocked_predecessor_ids"] == [direct["idea_id"]]
    assert rows[independent["idea_id"]]["wave"] == 1
    assert not result["precedence_order_complete"]


def test_exact_waves_handle_legacy_cycles_missing_links_and_unknown_targets(monkeypatch):
    def idea(n, deps=(), quarter="", state="idea"):
        return {"idea_id": str(n), "revision": 1, "state": state,
                "planning": {"depends_on": [str(d) for d in deps], "target_quarter": quarter}}
    ideas = [idea(1, [2]), idea(2, [1]), idea(3, [1]), idea(4, [99]), idea(5, [4]),
             idea(6), idea(7, [6], "2027-Q1"), idea(8, [9], "2027-Q1"),
             idea(9, [99], state="implemented")]
    result = review.dependency_waves(ideas)
    assert result["active_idea_count"] == 8
    assert result["cycle_or_downstream_idea_count"] == 3
    assert result["blocked_idea_count"] == 2 and result["layered_idea_count"] == 3
    rows = {i["idea_id"]: i for i in result["ideas"]}
    assert rows["4"]["direct_blockers"] == [{"idea_id": "99", "state": "missing"}]
    assert rows["7"]["latest_declared_target_in_unfinished_chain"] is None
    assert rows["8"]["wave"] == 1
    assert rows["8"]["latest_declared_target_in_unfinished_chain"] == "2027-Q1"
    assert review.dependency_waves(list(reversed(ideas))) == result
    monkeypatch.setattr(pipeline, "pipeline", lambda: {"ideas": ideas})
    monkeypatch.setattr(review, "MAX_RISKS", 1)
    limited = review.review()
    assert limited["risk_candidates_truncated"]
    assert limited["active_ideas_with_target_quarter"] == 2
    assert limited["active_ideas_with_dependencies"] == 7
    assert limited["dependency_waves"] == result


def test_dependency_waves_full_thousand_node_chain_without_recursion():
    ideas = [{"idea_id": str(n), "revision": 1, "state": "approved",
              "planning": {"depends_on": [str(n-1)] if n else [], "target_quarter": "2027-Q1"}}
             for n in range(1000)]
    result = review.dependency_waves(ideas)
    assert result["wave_count"] == result["layered_idea_count"] == 1000
    assert result["waves"][-1] == {"wave": 1000, "idea_ids": ["999"]}
    assert result["precedence_order_complete"]


def test_real_http_route_requires_dashboard_session(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
path='/v1/automation-pipeline/dependency-schedule'
with TestClient(app) as client:
    assert client.get(path).status_code==401
    token=local_auth.ensure_api_token()
    assert client.get(path,headers={'Authorization':'Bearer '+token}).status_code in (401,403)
    session=local_auth.create_dashboard_session()
    r=client.get(path,headers={'Authorization':'OWG-Session '+session})
    assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store'
    data=r.json()
    assert data['format']=='openworkgraph.automation-dependency-schedule-review.v1'
    assert data['schedule_feasibility_verified'] is False
    assert data['risk_candidates']==[]
    assert data['dependency_waves']['active_idea_count']==0
    assert data['dependency_waves']['all_active_ideas_classified'] is True
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_cycle_inventory_detects_cycles_even_with_separate_state_blockers(monkeypatch):
    def idea(n, deps=(), state="idea"):
        return {"idea_id":n,"revision":1,"state":state,
                "planning":{"depends_on":list(deps),"target_quarter":"2027-Q1"}}
    ideas = [idea("a",("b","held")),idea("b",("a",)),idea("c",("a",)),
             idea("held",state="on_hold"),idea("self",("self",)),
             idea("independent"),idea("done",("done",),state="implemented")]
    monkeypatch.setattr(pipeline,"pipeline",lambda:{"ideas":ideas})
    result=review.review()
    waves=result["dependency_waves"]
    assert waves["cycle_groups"]==[
        {"group":1,"idea_ids":["a","b"]},{"group":2,"idea_ids":["self"]}]
    assert waves["exact_cycle_member_count"]==3
    assert waves["exact_cycle_downstream_count"]==1
    assert waves["cycle_or_downstream_idea_count"]==1
    assert waves["blocked_idea_count"]==4
    by_id={row["idea_id"]:row for row in waves["ideas"]}
    assert by_id["a"]["cycle_group"]==1 and by_id["a"]["also_blocked_by_declared_state"]
    assert by_id["c"]["depends_on_cycle"] and by_id["c"]["cycle_group"] is None
    assert by_id["independent"]["wave"]==1
    assert not result["review_complete"]


def test_legacy_non_ascii_quarters_are_unknown_not_crashes():
    for value in ("²027-Q1","２０２７-Q1","٢٠٢٧-Q1","2027-Q5",None):
        assert review._quarter(value) is None
    assert review._quarter("2027-Q3")==2027*4+3


def test_exact_cycle_inventory_random_graph_reachability_oracle():
    import random
    from shared.portfolio_dependency_cycles import cycle_inventory
    rng=random.Random(20261124)
    for _ in range(1000):
        nodes=[str(i) for i in range(rng.randrange(0,45))]
        graph={i:[j for j in nodes if rng.random()<.12] for i in nodes}
        reach={}
        for i in graph:
            seen=set()
            todo=list(graph[i])
            while todo:
                j=todo.pop()
                if j not in seen:
                    seen.add(j)
                    todo.extend(graph[j])
            reach[i]=seen
        cyclic={i for i in graph if i in reach[i]}
        expected=sorted({tuple(sorted(j for j in cyclic if j in reach[i] and i in reach[j]))
                         for i in cyclic})
        groups,membership,downstream=cycle_inventory(graph)
        assert [tuple(group) for group in groups]==expected
        assert set(membership)==cyclic
        assert downstream=={i for i in graph if i not in cyclic and reach[i]&cyclic}
        assert cycle_inventory(dict(reversed(list(graph.items()))))==(groups,membership,downstream)
    for n in (1000,10000):
        graph={str(i):[str(i-1)] if i else [] for i in range(n)}
        assert cycle_inventory(graph)==([],{},set())
        graph["0"]=["1"]
        groups,membership,downstream=cycle_inventory(graph)
        assert groups==[["0","1"]] and len(downstream)==n-2
