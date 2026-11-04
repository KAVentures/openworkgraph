"""Step-by-step routing for what-if simulation, calibrated from the directly-follows graph (#370 R14).

Path routing replays the most frequent complete case paths, so cases on rarer paths (for example rework
repeated a different number of times) are left out of the calibration. Transition routing instead learns,
from every eligible case, which activity starts a case and, after each activity, which activity follows
or whether the case ends. Each simulated case walks those shares one step at a time, so loops repeat as
often as the log suggests and paths never seen as a whole can still occur. Routing is first-order: the
next step depends only on the current activity, not on how the case got there.

An optional routing context relaxes that (#370 R14 higher-order / case-attribute routing):

- ``history``: the next step depends on the previous and the current activity, so rework that usually
  happens once is not repeated as often as a memoryless walk would repeat it;
- ``attribute:<name>``: each simulated case draws a value of a case attribute (for example ``priority``)
  from its observed mix, and routing is learned separately for each value.

A context row is used only when at least ``MIN_CONTEXT_SUPPORT`` calibrated cases passed through it;
otherwise the walk falls back to the first-order row for the current activity. Measured shares are
associations in the log, not causes.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

from .event_log_workspace import EventLogWorkspaceError

ROUTINGS = ("paths", "transitions")
MIN_CONTEXT_SUPPORT = 20
MAX_ATTRIBUTE_VALUES = 12
_ATTRIBUTE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ .-]{0,63}$")
OTHER, NONE = "(other)", "(none)"
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


def parse_context(value: Any) -> dict[str, Any] | None:
    """``None``/``"current"``, ``"history"`` or ``"attribute:<case attribute name>"``."""
    if value in (None, "", "current"):
        return None
    if value == "history":
        return {"kind": "history"}
    if isinstance(value, str) and value.startswith("attribute:") and _ATTRIBUTE.fullmatch(value[10:]):
        return {"kind": "attribute", "attribute": value[10:]}
    raise EventLogWorkspaceError("routing_context must be current, history or attribute:<case attribute>")


def measure_context(conn, log_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Context-specific next-step counts over ``simulation_case_paths``."""
    if context["kind"] == "history":
        rows = conn.execute("""SELECT p, a, b, count(*) FROM (
              SELECT path[i - 1] AS p, path[i] AS a, CASE WHEN i < len(path) THEN path[i + 1] END AS b
              FROM simulation_case_paths, range(2, len(path) + 1) t(i))
            GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC, 3 NULLS LAST""").fetchall()
        return {"kind": "history", "min_support": MIN_CONTEXT_SUPPORT,
                "next": [{"previous": p, "from": a, "to": b, "count": n} for p, a, b, n in rows]}
    name = context["attribute"]
    path = "$." + (name if name.isidentifier() else json.dumps(name))
    # The case object's attribute (OCEL), else the earliest event of the case that carries it (CSV columns).
    conn.execute("""CREATE OR REPLACE TEMP TABLE simulation_case_values AS
        SELECT c.case_id, coalesce(json_extract_string(o.attributes, ?), (
            SELECT json_extract_string(e.attributes, ?) FROM ce e
            WHERE e.case_id = c.case_id AND json_extract_string(e.attributes, ?) IS NOT NULL
            ORDER BY e.ts, e.event_id LIMIT 1)) AS raw
        FROM simulation_case_paths c LEFT JOIN objects o ON o.log_id = ? AND o.object_id = c.case_id""",
                 [path, path, path, log_id])
    top = [r[0] for r in conn.execute(f"""SELECT raw FROM simulation_case_values WHERE raw IS NOT NULL
        GROUP BY raw ORDER BY count(*) DESC, raw LIMIT {MAX_ATTRIBUTE_VALUES}""").fetchall()]
    conn.execute("""CREATE OR REPLACE TEMP TABLE simulation_case_groups AS
        SELECT case_id, CASE WHEN raw IS NULL THEN ? WHEN list_contains(?, raw) THEN raw ELSE ? END AS v
        FROM simulation_case_values""", [NONE, top, OTHER])
    mix = conn.execute("SELECT v, count(*) FROM simulation_case_groups GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
    starts = conn.execute("""SELECT v, path[1], count(*) FROM simulation_case_paths JOIN simulation_case_groups
        USING(case_id) GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2""").fetchall()
    rows = conn.execute("""SELECT v, a, b, count(*) FROM (
          SELECT v, path[i] AS a, CASE WHEN i < len(path) THEN path[i + 1] END AS b
          FROM simulation_case_paths JOIN simulation_case_groups USING(case_id), range(1, len(path) + 1) t(i))
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC, 3 NULLS LAST""").fetchall()
    return {"kind": "attribute", "attribute": name, "min_support": MIN_CONTEXT_SUPPORT,
            "values": [{"value": v, "cases": n} for v, n in mix],
            "start": [{"value": v, "activity": a, "cases": n} for v, a, n in starts],
            "next": [{"value": v, "from": a, "to": b, "count": n} for v, a, b, n in rows]}


def _context_rows(measured: dict[str, Any], key_of) -> dict[tuple, list[tuple[str | None, float]]]:
    grouped: dict[tuple, list[tuple[str | None, float]]] = {}
    for r in measured["next"]:
        grouped.setdefault(key_of(r), []).append((r["to"], r["count"]))
    support = measured.get("min_support", MIN_CONTEXT_SUPPORT)
    return {k: _normalized(v) for k, v in grouped.items() if sum(w for _, w in v) >= support}


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
    model = {"start": _normalized(start), "next": {a: _normalized(r) for a, r in nxt.items()}}
    context = measured.get("context")
    if context:
        edited = {row.get("from") for row in overrides or []}
        if context["kind"] == "history":
            rows = _context_rows(context, lambda r: (r["previous"], r["from"]))
            model["context"] = {"kind": "history",
                                "next": {k: v for k, v in rows.items() if k[1] not in edited}}
        else:
            rows = _context_rows(context, lambda r: (r["value"], r["from"]))
            starts: dict[str, list[tuple[str | None, float]]] = {}
            for r in context["start"]:
                starts.setdefault(r["value"], []).append((r["activity"], r["cases"]))
            model["context"] = {
                "kind": "attribute", "attribute": context["attribute"],
                "values": _normalized([(v["value"], v["cases"]) for v in context["values"]]),
                "start": {} if None in edited else {v: _normalized(r) for v, r in starts.items()
                                                    if sum(w for _, w in r) >= context.get("min_support", MIN_CONTEXT_SUPPORT)},
                "next": {k: v for k, v in rows.items() if k[1] not in edited}}
    return model


def _normalized(row: list[tuple[str | None, float]]) -> list[tuple[str | None, float]]:
    total = sum(w for _, w in row)
    return [(a, w / total) for a, w in row if w > 0]


def activities(model: dict[str, Any]) -> set[str]:
    names = {a for a, _ in model["start"]} | set(model["next"])
    names |= {b for row in model["next"].values() for b, _ in row if b is not None}
    context = model.get("context") or {}
    for rows in (context.get("next") or {}).values():
        names |= {b for b, _ in rows if b is not None}
    for rows in (context.get("start") or {}).values():
        names |= {a for a, _ in rows}
    return names


def walk(model: dict[str, Any], rng: random.Random, limit: int) -> tuple[list[str], bool]:
    """One case's route; True when it was cut at ``limit`` steps."""
    def pick(row):
        draw, acc = rng.random(), 0.0
        for name, weight in row:
            acc += weight
            if draw <= acc:
                return name
        return row[-1][0]
    context = model.get("context")
    value = pick(context["values"]) if context and context["kind"] == "attribute" else None
    first = (context.get("start", {}).get(value) if value is not None else None) or model["start"]
    path = [pick(first)]
    while len(path) < limit:
        row = None
        if context and context["kind"] == "history" and len(path) >= 2:
            row = context["next"].get((path[-2], path[-1]))
        elif value is not None:
            row = context["next"].get((value, path[-1]))
        row = row or model["next"].get(path[-1])
        if not row:
            return path, False                       # never seen followed: the case ends here
        step = pick(row)
        if step is None:
            return path, False
        path.append(step)
    return path, model["next"].get(path[-1]) is not None and any(b is not None for b, _ in model["next"][path[-1]])


def describe(model: dict[str, Any]) -> dict[str, Any]:
    out = {"start": [{"activity": a, "weight": round(w, 6)} for a, w in model["start"]],
           "next": [{"from": a, "to": b, "weight": round(w, 6)} for a, row in sorted(model["next"].items())
                    for b, w in row]}
    context = model.get("context")
    if context:
        if context["kind"] == "history":
            out["context"] = {"kind": "history", "rows_used": len(context["next"]),
                              "next": [{"previous": k[0], "from": k[1], "to": b, "weight": round(w, 6)}
                                       for k, row in sorted(context["next"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
                                       for b, w in row][:300]}
        else:
            out["context"] = {"kind": "attribute", "attribute": context["attribute"],
                              "values": [{"value": v, "share": round(w, 6)} for v, w in context["values"]],
                              "rows_used": len(context["next"]),
                              "next": [{"value": k[0], "from": k[1], "to": b, "weight": round(w, 6)}
                                       for k, row in sorted(context["next"].items()) for b, w in row][:300]}
    return out


CONTEXT_ASSUMPTIONS = {
    "history": "Routing uses the previous and current activity where at least 20 calibrated cases passed that "
               "pair; elsewhere it falls back to the current activity only.",
    "attribute": "Each simulated case draws a value of the chosen case attribute from its observed mix, and routing "
                 "uses the shares observed for that value where at least 20 cases support them; elsewhere it falls "
                 "back to all cases. These are associations in the log, not effects of the attribute.",
}


def draw(measured: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Measured transitions with each row's shares drawn from a Dirichlet distribution over its counts."""
    def resample(rows, key):
        gammas = [rng.gammavariate(max(1, r[key]), 1.0) for r in rows]
        total = sum(gammas)
        return [{**r, key: g / total * sum(x[key] for x in rows)} for r, g in zip(rows, gammas)]
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in measured["next"]:
        by_source.setdefault(r["from"], []).append(r)
    drawn = {**measured, "start": resample(measured["start"], "cases"),
             "next": [r for rows in by_source.values() for r in resample(rows, "count")]}
    context = measured.get("context")
    if context:
        groups: dict[tuple, list[dict[str, Any]]] = {}
        for r in context["next"]:
            groups.setdefault((r.get("previous"), r.get("value"), r["from"]), []).append(r)
        drawn_context = {**context, "next": [r for rows in groups.values() for r in resample(rows, "count")]}
        if context["kind"] == "attribute":
            drawn_context["values"] = resample(context["values"], "cases")
        drawn["context"] = drawn_context
    return drawn


__all__ = ["ROUTINGS", "ASSUMPTIONS", "CONTEXT_ASSUMPTIONS", "measure", "measure_context", "parse_context",
           "build", "activities", "walk", "describe", "draw"]
