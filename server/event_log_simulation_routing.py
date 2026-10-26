"""Step-by-step routing for what-if simulation, calibrated from the directly-follows graph (#370 R14).

Path routing replays the most frequent complete case paths, so cases on rarer paths (for example rework
repeated a different number of times) are left out of the calibration. Transition routing instead learns,
from every eligible case, which activity starts a case and, after each activity, which activity follows
or whether the case ends. Each simulated case walks those shares one step at a time, so loops repeat as
often as the log suggests and paths never seen as a whole can still occur. Routing is first-order: the
next step depends only on the current activity, not on how the case got there.
"""
from __future__ import annotations

import random
from typing import Any

from .event_log_workspace import EventLogWorkspaceError

ROUTINGS = ("paths", "transitions")
ASSUMPTIONS = [
    "Routing is step by step: the next activity, or the end of the case, is drawn from the shares observed after "
    "the current activity in all eligible cases. It depends only on the current activity, not on earlier steps.",
    "A simulated case that reaches the path-length limit ends there; such cases are counted.",
]


def measure(conn) -> dict[str, Any]:
    """Start and next-activity counts over ``simulation_case_paths`` (all eligible cases)."""
    starts = conn.execute("""SELECT path[1], count(*) FROM simulation_case_paths GROUP BY 1 ORDER BY 2 DESC, 1""").fetchall()
    edges = conn.execute("""SELECT a, b, count(*) FROM (
          SELECT path[i] AS a, CASE WHEN i < len(path) THEN path[i + 1] END AS b
          FROM simulation_case_paths, range(1, len(path) + 1) t(i))
        GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2 NULLS LAST""").fetchall()
    total = sum(n for _, n in starts)
    out_of: dict[str, int] = {}
    for a, _, n in edges:
        out_of[a] = out_of.get(a, 0) + n
    return {
        "start": [{"activity": a, "cases": n, "weight": round(n / total, 6)} for a, n in starts],
        "next": [{"from": a, "to": b, "count": n, "weight": round(n / out_of[a], 6)} for a, b, n in edges],
        "cases": total,
    }


def _row(items: Any, what: str) -> list[tuple[str | None, float]]:
    if not isinstance(items, list) or not 0 < len(items) <= 101:
        raise EventLogWorkspaceError(f"{what} needs 1 to 101 next steps")
    row = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"activity", "weight"} or "weight" not in item:
            raise EventLogWorkspaceError(f"each step in {what} takes an activity (null to end the case) and a weight")
        name, weight = item.get("activity"), item["weight"]
        if name is not None and not (isinstance(name, str) and 0 < len(name) <= 200):
            raise EventLogWorkspaceError(f"{what} names an activity that is not a short name")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 1e6:
            raise EventLogWorkspaceError(f"weights in {what} must be numbers from 0 to 1,000,000")
        row.append((name, float(weight)))
    if sum(w for _, w in row) <= 0:
        raise EventLogWorkspaceError(f"weights in {what} must not all be zero")
    return row


def build(base: dict[str, Any], overrides: Any) -> dict[str, Any]:
    """Start and next-step shares from the calibration, with entered rows replacing whole rows."""
    measured = base.get("transitions")
    if not measured:
        raise EventLogWorkspaceError("transition routing needs a calibration with routing transitions")
    start = [(r["activity"], r["cases"]) for r in measured["start"]]
    nxt: dict[str, list[tuple[str | None, float]]] = {}
    for r in measured["next"]:
        nxt.setdefault(r["from"], []).append((r["to"], r["count"]))
    if overrides is not None:
        if not isinstance(overrides, list) or len(overrides) > 101:
            raise EventLogWorkspaceError("transitions must be a list of at most 101 rows")
        seen = set()
        for row in overrides:
            if not isinstance(row, dict) or set(row) - {"from", "to"} or "to" not in row:
                raise EventLogWorkspaceError("each transitions row takes from (null for the start) and to")
            source = row.get("from")
            if source is not None and not (isinstance(source, str) and 0 < len(source) <= 200):
                raise EventLogWorkspaceError("a transitions row names an activity that is not a short name")
            if source in seen:
                raise EventLogWorkspaceError("each activity may have one transitions row")
            seen.add(source)
            steps = _row(row["to"], "the start" if source is None else f"the steps after {source}")
            if source is None:
                if any(a is None for a, _ in steps):
                    raise EventLogWorkspaceError("a case cannot end before its first activity")
                start = steps
            else:
                nxt[source] = steps
    return {"start": _normalized(start), "next": {a: _normalized(r) for a, r in nxt.items()}}


def _normalized(row: list[tuple[str | None, float]]) -> list[tuple[str | None, float]]:
    total = sum(w for _, w in row)
    return [(a, w / total) for a, w in row if w > 0]


def activities(model: dict[str, Any]) -> set[str]:
    names = {a for a, _ in model["start"]} | set(model["next"])
    return names | {b for row in model["next"].values() for b, _ in row if b is not None}


def walk(model: dict[str, Any], rng: random.Random, limit: int) -> tuple[list[str], bool]:
    """One case's route; True when it was cut at ``limit`` steps."""
    def pick(row):
        draw, acc = rng.random(), 0.0
        for name, weight in row:
            acc += weight
            if draw <= acc:
                return name
        return row[-1][0]
    path = [pick(model["start"])]
    while len(path) < limit:
        row = model["next"].get(path[-1])
        if not row:
            return path, False                       # never seen followed: the case ends here
        step = pick(row)
        if step is None:
            return path, False
        path.append(step)
    return path, model["next"].get(path[-1]) is not None and any(b is not None for b, _ in model["next"][path[-1]])


def describe(model: dict[str, Any]) -> dict[str, Any]:
    return {"start": [{"activity": a, "weight": round(w, 6)} for a, w in model["start"]],
            "next": [{"from": a, "to": b, "weight": round(w, 6)} for a, row in sorted(model["next"].items())
                     for b, w in row]}


def draw(measured: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Measured transitions with each row's shares drawn from a Dirichlet distribution over its counts."""
    def resample(rows, key):
        gammas = [rng.gammavariate(max(1, r[key]), 1.0) for r in rows]
        total = sum(gammas)
        return [{**r, key: g / total * sum(x[key] for x in rows)} for r, g in zip(rows, gammas)]
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in measured["next"]:
        by_source.setdefault(r["from"], []).append(r)
    return {**measured, "start": resample(measured["start"], "cases"),
            "next": [r for rows in by_source.values() for r in resample(rows, "count")]}


__all__ = ["ROUTINGS", "ASSUMPTIONS", "measure", "build", "activities", "walk", "describe", "draw"]
