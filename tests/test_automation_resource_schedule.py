"""R15 resource-constrained portfolio schedule and exact small-instance oracles."""
from __future__ import annotations

from itertools import product
import random

import pytest

from server import automation_resource_schedule as schedule
from server import automation_pipeline as pipeline
from server import automation_budget_scenario as budget


def key(i):
    return "idea_" + f"{i:016x}"


def task(i, duration, units=1, pool="team", release=0, deadline=None):
    return {"idea_id": key(i), "revision": 1, "pool": pool, "duration_days": duration,
            "units": units, "release_day": release, "deadline_day": deadline}


def spec(tasks, capacities=None, alternatives=None):
    return {"snapshot_sha256": "a"*64, "tasks": tasks, "capacities": capacities or {"team": 2},
            "alternatives": alternatives or []}


def fixture(monkeypatch, tasks, deps=None, states=None, extras=None):
    deps, states = deps or {}, states or {}
    rows = [{"idea_id": t["idea_id"], "revision": t["revision"], "title": t["idea_id"],
             "state": states.get(t["idea_id"], "approved"),
             "depends_on": deps.get(t["idea_id"], [])} for t in tasks]
    rows.extend(extras or [])
    data = {"snapshot_sha256": "a"*64, "ideas": rows}
    monkeypatch.setattr(budget, "inventory", lambda: data)
    return data


def verify_discrete(result, task_list, deps):
    rows = {r["idea_id"]: r for r in result["schedule"]}
    assert set(rows) == {t["idea_id"] for t in task_list}
    for t in task_list:
        r = rows[t["idea_id"]]
        assert r["finish_day_exclusive"] - r["start_day"] == t["duration_days"]
        assert r["start_day"] >= t["release_day"]
        for p in deps.get(t["idea_id"], []):
            assert rows[p]["finish_day_exclusive"] <= r["start_day"]
    for pool, cap in result["capacities"].items():
        for day in range(result["makespan_days"]):
            assert sum(r["units"] for r in rows.values() if r["pool"] == pool
                       and r["start_day"] <= day < r["finish_day_exclusive"]) <= cap


def brute(tasks, deps, cap, upper):
    # Independently enumerate every integer start tuple through a known
    # feasible horizon, not just serial priority orders.
    by_id = {t["idea_id"]: i for i,t in enumerate(tasks)}
    best = upper
    for starts in product(*(range(t["release_day"], upper-t["duration_days"]+1) for t in tasks)):
        ends = [s+t["duration_days"] for s,t in zip(starts,tasks)]
        span = max(ends)
        if span >= best:
            continue
        if any(ends[by_id[p]] > starts[i] for i,t in enumerate(tasks) for p in deps.get(t["idea_id"], [])):
            continue
        if any(sum(t["units"] for i,t in enumerate(tasks) if t["pool"]==pool and starts[i]<=day<ends[i]) > c
               for pool,c in cap.items() for day in range(span)):
            continue
        best = span
    return best


def test_precedence_capacity_deadlines_and_scenario_delta(monkeypatch):
    tasks = [task(1,3,deadline=3),task(2,2),task(3,4,deadline=6)]
    deps = {key(3):[key(1),key(2)]}
    fixture(monkeypatch,tasks,deps)
    report = schedule.solve(spec(tasks,{"team":1},[{"label":"Two resources","capacities":{"team":2}}]))
    a,b = report["scenarios"]
    assert a["makespan_days"] == 9
    assert b["makespan_days"] == 7
    assert b["optimal_makespan_change_bounds_days"] == [-2,-2]
    assert b["deadline_misses_even_without_resource_constraints"] == 1
    assert b["resource_free_precedence_bound_days"] == 7
    assert b["optimization"]["optimal_makespan_proven"]
    assert a["resource_load"][0]["used_unit_days"] == 9
    assert a["resource_load"][0]["fully_occupied_days"] == 9
    verify_discrete(a,tasks,deps)
    verify_discrete(b,tasks,deps)
    assert not report["portfolio_modified"] and not report["execution_authorized"]


def test_exhaustive_integer_oracle_random_small_instances(monkeypatch):
    rng = random.Random(7081)
    for _ in range(35):
        n = rng.randint(2,4)
        cap = {"team":rng.randint(1,3),"other":2}
        tasks = [task(i,rng.randint(1,3),pool="other" if i%2 else "team",release=rng.randint(0,2)) for i in range(n)]
        for t in tasks:
            t["units"] = rng.randint(1,cap[t["pool"]])
        deps = {key(i):[key(j) for j in range(i) if rng.random()<.30] for i in range(n)}
        fixture(monkeypatch,tasks,deps)
        result = schedule.solve(spec(tasks,cap))["scenarios"][0]
        verify_discrete(result,tasks,deps)
        assert result["optimization"]["optimal_makespan_proven"]
        assert result["makespan_days"] == brute(tasks,deps,cap,result["makespan_days"])
        assert result["optimization"]["analytical_lower_bound_days"] <= result["makespan_days"]


def test_independent_tasks_need_resource_packing_not_just_critical_path(monkeypatch):
    tasks = [task(1,2,2),task(2,2,2),task(3,2,2)]
    fixture(monkeypatch,tasks)
    result = schedule.solve(spec(tasks,{"team":3}))["scenarios"][0]
    assert result["optimization"]["analytical_lower_bound_days"] == 4
    assert result["makespan_days"] == 6
    assert result["optimization"]["method"] == "complete_serial_order_search"
    assert result["optimization"]["optimal_makespan_proven"]
    assert result["optimization"]["search_nodes"] > 0


def test_large_instance_does_not_claim_unsupported_optimum(monkeypatch):
    tasks = [task(i,2,2) for i in range(9)]
    fixture(monkeypatch,tasks)
    result = schedule.solve(spec(tasks,{"team":3}))["scenarios"][0]
    assert result["makespan_days"] == 18
    assert result["optimization"]["analytical_lower_bound_days"] == 12
    assert result["optimization"]["absolute_optimality_gap_days"] == 6
    assert not result["optimization"]["optimal_makespan_proven"]


def test_infeasible_capacity_and_feasible_alternative(monkeypatch):
    tasks = [task(1,4,2)]
    fixture(monkeypatch,tasks)
    report = schedule.solve(spec(tasks,{"team":1},[{"label":"Enough","capacities":{"team":2}}]))
    a,b = report["scenarios"]
    assert a["status"] == "infeasible_resource_demand" and a["schedule"] is None
    assert b["makespan_days"] == 4
    assert b["makespan_change_vs_baseline_days"] is None
    assert b["optimal_makespan_change_bounds_days"] is None


def test_implemented_prerequisites_and_missing_held_cycle_blockers(monkeypatch):
    tasks = [task(1,2),task(2,3)]
    extra = {"idea_id":key(3),"revision":1,"title":"done","state":"implemented","depends_on":[]}
    fixture(monkeypatch,tasks,{key(2):[key(1),key(3)]},extras=[extra])
    assert schedule.solve(spec(tasks))["scenarios"][0]["makespan_days"] == 5
    fixture(monkeypatch,tasks,{key(2):[key(3)]})
    assert schedule.solve(spec(tasks))["blockers"][0]["code"] == "unfinished_prerequisite_not_selected"
    fixture(monkeypatch,tasks,states={key(1):"on_hold"})
    assert schedule.solve(spec(tasks))["scenarios"] == []
    fixture(monkeypatch,tasks,{key(1):[key(2)],key(2):[key(1)]})
    assert schedule.solve(spec(tasks))["blockers"] == [{"code":"selected_dependency_cycle_or_downstream"}]


@pytest.mark.parametrize("kind",["duplicate","bad_pool","extra_pool","bool","unknown","alternative"])
def test_strict_assumptions(kind,monkeypatch):
    tasks = [task(1,2)]
    fixture(monkeypatch,tasks)
    request = spec(tasks)
    if kind=="duplicate": request["tasks"].append(tasks[0].copy())
    elif kind=="bad_pool": tasks[0]["pool"]="not valid"
    elif kind=="extra_pool": request["capacities"]["unused"]=1
    elif kind=="bool": tasks[0]["duration_days"]=True
    elif kind=="unknown": request["execute"]=True
    else: request["alternatives"]=[{"label":"Baseline","capacities":{"team":3}}]
    with pytest.raises(pipeline.PipelineError):
        schedule.solve(request)


def test_stale_snapshot_before_and_after_calculation(monkeypatch):
    tasks=[task(1,2)]
    data=fixture(monkeypatch,tasks)
    request=spec(tasks)
    request["snapshot_sha256"]="b"*64
    with pytest.raises(pipeline.PipelineError,match="changed"):
        schedule.solve(request)
    snapshots=iter([data,{**data,"snapshot_sha256":"b"*64}])
    monkeypatch.setattr(budget,"inventory",lambda:next(snapshots))
    with pytest.raises(pipeline.PipelineError,match="changed during"):
        schedule.solve(spec(tasks))


def test_http_session_and_no_store(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import automation_pipeline_routes as routes
    tasks=[task(1,2)]
    fixture(monkeypatch,tasks)
    monkeypatch.setattr(routes,"dashboard_session_valid",lambda token:token=="local")
    app=FastAPI();app.include_router(routes.router)
    owner={"Authorization":"OWG-Session local"}
    route="/v1/automation-pipeline/resource-schedule"
    with TestClient(app) as client:
        assert client.get(route+"/inventory").status_code==403
        assert client.post(route,json=spec(tasks),headers={"Authorization":"Bearer token"}).status_code==403
        assert client.get(route+"/inventory",headers=owner).headers["cache-control"]=="no-store"
        response=client.post(route,json=spec(tasks),headers=owner)
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        assert response.json()["scenarios"][0]["makespan_days"]==2
        bad=spec(tasks);bad["snapshot_sha256"]="b"*64
        assert client.post(route,json=bad,headers=owner).status_code==409
