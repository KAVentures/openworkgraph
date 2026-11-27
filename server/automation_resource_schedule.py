"""Resource-constrained opportunity planning on explicitly reviewed assumptions.

No state changes, inferred staffing, source reads or business execution.
Time is integer relative planning days; every task is nonpreemptive and
uses a single declared interchangeable resource pool for its full duration.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import automation_pipeline as pipeline
from . import automation_budget_scenario as budget

FORMAT = "openworkgraph.portfolio-resource-schedule.v1"
MAX_TASKS = 30
EXACT_TASKS = 8
MAX_POOLS = 8
MAX_ALTERNATIVES = 3
ACTIVE = {"idea", "assessing", "approved"}
ID = Annotated[str, Field(pattern=r"^idea_[a-f0-9]{16}$")]
POOL = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")]
CAPACITY = Annotated[int, Field(ge=1, le=50)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Task(Strict):
    idea_id: ID
    revision: int = Field(ge=1)
    pool: POOL
    duration_days: int = Field(ge=1, le=180)
    units: CAPACITY
    release_day: int = Field(default=0, ge=0, le=730)
    deadline_day: int | None = Field(default=None, ge=1, le=730)


class Alternative(Strict):
    label: str = Field(min_length=1, max_length=60)
    capacities: dict[POOL, CAPACITY] = Field(min_length=1, max_length=MAX_POOLS)


class Spec(Strict):
    format: Literal["openworkgraph.portfolio-resource-schedule.v1"] = FORMAT
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tasks: list[Task] = Field(min_length=1, max_length=MAX_TASKS)
    capacities: dict[POOL, CAPACITY] = Field(min_length=1, max_length=MAX_POOLS)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=MAX_ALTERNATIVES)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def inventory():
    return {**budget.inventory(), "format": "openworkgraph.portfolio-resource-inventory.v1",
            "max_tasks": MAX_TASKS, "exact_search_max_tasks": EXACT_TASKS,
            "max_pools": MAX_POOLS, "max_alternatives": MAX_ALTERNATIVES}


def _parse(payload):
    try:
        spec = Spec.model_validate(payload)
    except ValidationError:
        raise pipeline.PipelineError("invalid bounded resource scheduling assumptions") from None
    ids = [t.idea_id for t in spec.tasks]
    if len(set(ids)) != len(ids):
        raise pipeline.PipelineError("select distinct opportunity IDs")
    used = {t.pool for t in spec.tasks}
    if used != set(spec.capacities):
        raise pipeline.PipelineError("declare exactly the resource pools used by selected tasks")
    labels = {"baseline"}
    for alternative in spec.alternatives:
        name = alternative.label.strip().casefold()
        if not name or not alternative.label.isprintable() or name in labels:
            raise pipeline.PipelineError("alternative names must be distinct printable labels other than baseline")
        labels.add(name)
        if set(alternative.capacities) != used:
            raise pipeline.PipelineError("each alternative must declare the same exact resource pools")
    return spec


def _graph(spec, snapshot):
    by_id = {i["idea_id"]: i for i in snapshot["ideas"]}
    tasks = {t.idea_id: t for t in spec.tasks}
    deps, blockers = {}, []
    for key, task in sorted(tasks.items()):
        idea = by_id.get(key)
        if idea is None or idea["revision"] != task.revision:
            raise pipeline.PipelineError("selected opportunity changed; reload the portfolio")
        if idea["state"] not in ACTIVE:
            blockers.append({"idea_id": key, "code": "ineligible_state", "state": idea["state"]})
        deps[key] = set()
        for dep in idea["depends_on"]:
            predecessor = by_id.get(dep)
            if predecessor and predecessor["state"] == "implemented":
                continue
            if dep not in tasks:
                blockers.append({"idea_id": key, "code": "unfinished_prerequisite_not_selected",
                                 "dependency_id": dep,
                                 "state": predecessor["state"] if predecessor else "missing"})
            else:
                deps[key].add(dep)
    return tasks, deps, blockers, by_id


def _precedence(tasks, deps):
    """Resource-free earliest dates, longest remaining chains, and slack."""
    waiting = set(tasks)
    order, earliest, finish = [], {}, {}
    children = defaultdict(list)
    for k, parents in deps.items():
        for p in parents:
            children[p].append(k)
    while waiting:
        ready = sorted(k for k in waiting if deps[k] <= finish.keys())
        if not ready:
            return None
        for key in ready:
            task = tasks[key]
            earliest[key] = max(task.release_day, max((finish[p] for p in deps[key]), default=0))
            finish[key] = earliest[key] + task.duration_days
            order.append(key)
            waiting.remove(key)
    bound = max(finish.values())
    latest_finish, tail = {}, {}
    for key in reversed(order):
        latest_finish[key] = min((latest_finish[c] - tasks[c].duration_days for c in children[key]), default=bound)
        tail[key] = tasks[key].duration_days + max((tail[c] for c in children[key]), default=0)
    return {"order": order, "earliest": earliest, "finish": finish, "latest_finish": latest_finish,
            "tail": tail, "bound": bound}


def _fits(start, end, units, capacity, intervals):
    """Sweep half-open existing reservations; end events precede new starts."""
    changes = defaultdict(int)
    for a, b, demand in intervals:
        if a < end and b > start:
            changes[max(a, start)] += demand
            changes[min(b, end)] -= demand
    load = units
    for at in sorted(changes):
        load += changes[at]
        if at < end and load > capacity:
            return False
    return True


def _slot(task, ready, capacity, intervals):
    # If not feasible at ready, earliest feasibility must begin at an
    # existing reservation's end; moving earlier until that boundary cannot
    # remove a conflicting occupied instant. After the last end it fits.
    candidates = sorted({ready, *(end for _, end, _ in intervals if end > ready)})
    for start in candidates:
        if _fits(start, start + task.duration_days, task.units, capacity, intervals):
            return start
    raise AssertionError("admitted task must fit after the last reservation")


def _place(key, tasks, deps, capacities, schedule, reservations):
    task = tasks[key]
    ready = max(task.release_day, max((schedule[p][1] for p in deps[key]), default=0))
    start = _slot(task, ready, capacities[task.pool], reservations[task.pool])
    end = start + task.duration_days
    schedule[key] = (start, end)
    reservations[task.pool].append((start, end, task.units))


def _greedy(tasks, deps, capacities, precedence, rule):
    schedule = {}
    reservations = defaultdict(list)
    while len(schedule) < len(tasks):
        ready = [k for k in tasks if k not in schedule and deps[k] <= schedule.keys()]
        key = min(ready, key=rule)
        _place(key, tasks, deps, capacities, schedule, reservations)
    return schedule


def _makespan(schedule):
    return max(end for _, end in schedule.values())


def _lower_bound(tasks, capacities, precedence):
    pool_bounds = {}
    for pool, cap in sorted(capacities.items()):
        group = [t for t in tasks.values() if t.pool == pool]
        work = sum(t.duration_days * t.units for t in group)
        # No work in a pool before the earliest resource-free task start.
        release = min(precedence["earliest"][t.idea_id] for t in group)
        pool_bounds[pool] = release + (work + cap - 1) // cap
    return max(precedence["bound"], *pool_bounds.values()), pool_bounds


def _optimize(tasks, deps, capacities, precedence, lower):
    rules = [
        lambda k: (-precedence["tail"][k], tasks[k].release_day, k),
        lambda k: (tasks[k].deadline_day if tasks[k].deadline_day is not None else 10**9,
                   -precedence["tail"][k], k),
        lambda k: (-tasks[k].duration_days * tasks[k].units, -precedence["tail"][k], k),
        lambda k: (precedence["earliest"][k], -precedence["tail"][k], k),
    ]
    candidates = [_greedy(tasks, deps, capacities, precedence, rule) for rule in rules]
    best = min(candidates, key=lambda s: (_makespan(s), tuple(sorted(s.items()))))
    best_span = _makespan(best)
    visited, leaves, pruned = 0, 0, 0
    exact_allowed = len(tasks) <= EXACT_TASKS
    if exact_allowed and best_span > lower:
        schedule, reservations = {}, defaultdict(list)

        def search():
            nonlocal best, best_span, visited, leaves, pruned
            visited += 1
            if best_span == lower:
                return
            if len(schedule) == len(tasks):
                leaves += 1
                span = _makespan(schedule)
                if span < best_span:
                    best, best_span = dict(schedule), span
                return
            # Extending a partial serial schedule cannot shorten any fixed
            # reservation. No pruned branch can improve the incumbent span.
            if schedule and _makespan(schedule) >= best_span:
                pruned += 1
                return
            ready = sorted((k for k in tasks if k not in schedule and deps[k] <= schedule.keys()),
                           key=rules[0])
            for key in ready:
                _place(key, tasks, deps, capacities, schedule, reservations)
                search()
                reservations[tasks[key].pool].pop()
                del schedule[key]
                if best_span == lower:
                    break

        search()
    # Serial schedule generation over all topological priority orders
    # contains an optimum for this regular makespan objective: for any
    # feasible schedule, process tasks by increasing start time (ties in
    # precedence order). Inductively the serial schedule starts each task
    # no later than its original time, since earlier tasks moved left can
    # only reduce their resource occupation from that original start onward.
    proven = best_span == lower or exact_allowed
    return best, {"method": ("lower_bound_matched" if best_span == lower else
                             "complete_serial_order_search" if exact_allowed else
                             "four_deterministic_serial_priority_rules"),
                  "optimal_makespan_proven": proven,
                  "lower_bound_days": best_span if proven else lower,
                  "analytical_lower_bound_days": lower,
                  "upper_bound_days": best_span,
                  "absolute_optimality_gap_days": 0 if proven else best_span - lower,
                  "search_nodes": visited, "complete_orders_scored": leaves,
                  "dominated_prefixes_pruned": pruned,
                  "deadline_objective_optimized": False}


def _verify_and_load(tasks, deps, capacities, schedule):
    """Independent sweep of the produced reservations and exact load histogram."""
    if set(schedule) != set(tasks):
        raise AssertionError("incomplete schedule")
    events = defaultdict(lambda: defaultdict(int))
    span = _makespan(schedule)
    for key, task in tasks.items():
        start, end = schedule[key]
        if start < task.release_day or end - start != task.duration_days or any(schedule[p][1] > start for p in deps[key]):
            raise AssertionError("schedule violates precedence or duration")
        events[task.pool][start] += task.units
        events[task.pool][end] -= task.units
    result = []
    for pool, cap in sorted(capacities.items()):
        load, peak, prev = 0, 0, 0
        histogram = defaultdict(int)
        for day in sorted(events[pool]):
            histogram[load] += day - prev
            load += events[pool][day]
            if load < 0 or load > cap:
                raise AssertionError("resource capacity exceeded")
            peak = max(peak, load)
            prev = day
        histogram[load] += span - prev
        if load != 0:
            raise AssertionError("unclosed reservation")
        work = sum(k * v for k, v in histogram.items())
        result.append({"pool": pool, "capacity_units": cap, "peak_units": peak,
                       "used_unit_days": work, "available_unit_days": cap * span,
                       "utilization": round(work / (cap * span), 6),
                       "fully_occupied_days": histogram[cap],
                       "load_histogram": [{"units": k, "days": v} for k, v in sorted(histogram.items()) if v]})
    return result


def _scenario(label, tasks, deps, capacities, precedence):
    oversized = [k for k, t in sorted(tasks.items()) if t.units > capacities[t.pool]]
    if oversized:
        return {"label": label, "capacities": capacities, "status": "infeasible_resource_demand",
                "oversized_task_ids": oversized, "schedule": None, "optimization": None}
    lower, pool_bounds = _lower_bound(tasks, capacities, precedence)
    schedule, proof = _optimize(tasks, deps, capacities, precedence, lower)
    loads = _verify_and_load(tasks, deps, capacities, schedule)
    rows = []
    for key, (start, finish) in sorted(schedule.items(), key=lambda row: (row[1][0], row[0])):
        task = tasks[key]
        dependency_ready = max(task.release_day, max((schedule[p][1] for p in deps[key]), default=0))
        rows.append({"idea_id": key, "pool": task.pool, "units": task.units,
                     "start_day": start, "finish_day_exclusive": finish,
                     "duration_days": task.duration_days, "release_day": task.release_day,
                     "deadline_day": task.deadline_day,
                     "deadline_lateness_days": max(0, finish - task.deadline_day) if task.deadline_day is not None else None,
                     "dependency_ready_day": dependency_ready,
                     "delay_after_dependency_ready_days": start - dependency_ready,
                     "resource_free_earliest_start_day": precedence["earliest"][key],
                     "resource_free_total_slack_days": precedence["latest_finish"][key] - precedence["finish"][key]})
    return {"label": label, "capacities": capacities, "status": "scheduled_under_assumptions",
            "makespan_days": _makespan(schedule), "optimization": proof,
            "resource_free_precedence_bound_days": precedence["bound"],
            "pool_work_lower_bounds_days": pool_bounds,
            "deadline_tasks": sum(r["deadline_day"] is not None for r in rows),
            "deadline_misses_in_returned_schedule": sum((r["deadline_lateness_days"] or 0) > 0 for r in rows),
            "deadline_misses_even_without_resource_constraints": sum(
                t.deadline_day is not None and precedence["finish"][k] > t.deadline_day for k,t in tasks.items()),
            "total_declared_deadline_lateness_days": sum(r["deadline_lateness_days"] or 0 for r in rows),
            "schedule_verified_against_assumptions": True, "schedule": rows, "resource_load": loads}


def solve(payload):
    spec = _parse(payload)
    snapshot = budget.inventory()
    if snapshot["snapshot_sha256"] != spec.snapshot_sha256:
        raise pipeline.PipelineError("portfolio changed; reload and review the current snapshot")
    tasks, deps, blockers, by_id = _graph(spec, snapshot)
    precedence = _precedence(tasks, deps)
    if precedence is None:
        blockers.append({"code": "selected_dependency_cycle_or_downstream"})
    normalized = spec.model_dump()
    normalized["tasks"] = sorted(normalized["tasks"], key=lambda t: t["idea_id"])
    response = {"format": FORMAT, "snapshot_sha256": spec.snapshot_sha256,
                "scenario_sha256": _digest(normalized), "reviewed_inputs": normalized,
                "selected_ideas": [{"idea_id": key, "title": by_id[key]["title"],
                                    "state": by_id[key]["state"], "revision": tasks[key].revision,
                                    "unfinished_selected_prerequisites": sorted(deps[key])}
                                   for key in sorted(tasks)],
                "blockers": blockers, "scenarios": [],
                "implemented_states_independently_verified": False,
                "calendar_dates_or_actual_staffing_verified": False,
                "execution_authorized": False, "source_systems_queried": False,
                "portfolio_modified": False,
                "interpretation": "Integer relative planning days with uninterrupted single-pool tasks. "
                                  "Durations, release days, deadlines and capacities are reviewer assumptions. "
                                  "Makespan alone is optimized; deadline misses in a returned schedule do not "
                                  "prove deadline infeasibility unless the resource-free bound already misses."}
    if blockers:
        response["status"] = "blocked_selected_prerequisites"
        return response
    alternatives = [("Baseline", spec.capacities)] + [(a.label, a.capacities) for a in spec.alternatives]
    results = [_scenario(label, tasks, deps, capacities, precedence) for label, capacities in alternatives]
    base = results[0]
    for row in results:
        row["makespan_change_vs_baseline_days"] = (row["makespan_days"] - base["makespan_days"]
            if row["schedule"] is not None and base["schedule"] is not None else None)
        if row["optimization"] and base["optimization"]:
            a, b = row["optimization"], base["optimization"]
            row["optimal_makespan_change_bounds_days"] = (
                [0, 0] if row is base else
                [a["lower_bound_days"] - b["upper_bound_days"], a["upper_bound_days"] - b["lower_bound_days"]])
        else:
            row["optimal_makespan_change_bounds_days"] = None
    response.update(status="evaluated", scenarios=results)
    # A long search may have overlapped an edit. Do not return old inputs as
    # a current reviewed plan if a different snapshot is now observable.
    if budget.inventory()["snapshot_sha256"] != spec.snapshot_sha256:
        raise pipeline.PipelineError("portfolio changed during scheduling; reload and review again")
    return response
