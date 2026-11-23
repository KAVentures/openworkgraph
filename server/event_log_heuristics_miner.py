"""Heuristics miner: a dependency graph that tolerates noise, with AND/XOR splits and loops.

The heuristics miner (Weijters and Ribeiro's Flexible Heuristics Miner; ProM, PM4Py and the
"dependency" view of several commercial tools) keeps an arrow ``a → b`` only when ``a`` is directly
followed by ``b`` clearly more often than the other way round:

* dependency ``a ⇒ b = (|a>b| − |b>a|) / (|a>b| + |b>a| + 1)`` for ``a ≠ b``;
* a self-loop ``a ⇒ a = |a>a| / (|a>a| + 1)``;
* a short loop ``a b a`` uses ``(|a>>b| + |b>>a|) / (|a>>b| + |b>>a| + 1)``, where ``|a>>b|`` counts
  ``a b a`` patterns, so that ``a ⇄ b`` is not cancelled out as noise.

An arrow is kept when its dependency is at least ``dependency_threshold`` (default 0.5) and it was
seen at least ``min_occurrences`` times. With ``all_connected`` (the default, as in the Flexible
Heuristics Miner) every activity keeps its best incoming and best outgoing arrow even below the
threshold, so nothing floats free.

Where an activity has two or more kept outgoing arrows to ``b`` and ``c``, the pair is parallel (AND)
when ``(|b>c| + |c>b|) / (|a>b| + |a>c| + 1)`` is at least ``and_threshold`` (default 0.65): ``b``
and ``c`` follow each other in both orders about as often as they follow ``a``. Otherwise it is an
exclusive choice (XOR). Joins are judged the same way on incoming arrows.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_heuristics_net.v1"
MAX_ACTIVITIES = 64
START, END = "▶ start", "■ end"
_SEQUENCE = """CREATE TEMP TABLE hm_seq AS SELECT case_id, activity, ts,
    lead(activity) OVER w AS nxt, lead(activity, 2) OVER w AS nxt2,
    row_number() OVER w AS rn, count(*) OVER (PARTITION BY case_id) AS len
    FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)"""


def _check(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise EventLogWorkspaceError(f"{name} must be a number from {low} to {high}")
    return float(value)


def heuristics_net(log_id: str, case_type: str | None = None, *, dependency_threshold: float = 0.5,
                   and_threshold: float = 0.65, loop_two_threshold: float = 0.5, min_occurrences: int = 1,
                   all_connected: bool = True, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    dependency_threshold = _check("dependency_threshold", dependency_threshold, 0.0, 1.0)
    and_threshold = _check("and_threshold", and_threshold, 0.0, 1.0)
    loop_two_threshold = _check("loop_two_threshold", loop_two_threshold, 0.0, 1.0)
    if type(min_occurrences) is not int or not 1 <= min_occurrences <= 1_000_000:
        raise EventLogWorkspaceError("min_occurrences must be an integer from 1 to 1000000")
    if not isinstance(all_connected, bool):
        raise EventLogWorkspaceError("all_connected must be true or false")

    def run(conn):
        conn.execute(_SEQUENCE)
        acts = conn.execute("""SELECT activity, count(*), count(DISTINCT case_id) FROM hm_seq
            GROUP BY 1 ORDER BY 2 DESC, 1""").fetchall()
        if len(acts) > MAX_ACTIVITIES:
            raise EventLogWorkspaceError(
                f"the log has {len(acts)} activities; the heuristics net shows at most {MAX_ACTIVITIES}. "
                "Group activities with an activity mapping or filter the cases first")
        follows = conn.execute("SELECT activity, nxt, count(*) FROM hm_seq WHERE nxt IS NOT NULL GROUP BY 1, 2").fetchall()
        loops2 = conn.execute("""SELECT activity, nxt, count(*) FROM hm_seq
            WHERE nxt2 = activity AND nxt <> activity GROUP BY 1, 2""").fetchall()
        starts = conn.execute("SELECT activity, count(*) FROM hm_seq WHERE rn = 1 GROUP BY 1").fetchall()
        ends = conn.execute("SELECT activity, count(*) FROM hm_seq WHERE rn = len GROUP BY 1").fetchall()
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM hm_seq").fetchone()[0]
        return {"acts": acts, "follows": follows, "loops2": loops2, "starts": starts, "ends": ends, "cases": cases}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    acts = data.pop("acts")
    df = Counter({(a, b): n for a, b, n in data.pop("follows")})
    l2 = Counter({(a, b): n for a, b, n in data.pop("loops2")})
    starts, ends = dict(data.pop("starts")), dict(data.pop("ends"))
    names = [a for a, _, _ in acts]

    def dependency(a: str, b: str) -> float:
        if a == b:
            return df[(a, a)] / (df[(a, a)] + 1)
        ab, ba = df[(a, b)], df[(b, a)]
        return (ab - ba) / (ab + ba + 1)

    kept: dict[tuple[str, str], str] = {}
    loops = []
    for a in names:
        if df[(a, a)] >= min_occurrences and dependency(a, a) >= dependency_threshold:
            kept[(a, a)] = "threshold"
            loops.append({"kind": "self", "activities": [a], "measure": round(dependency(a, a), 4),
                          "occurrences": df[(a, a)]})
    in_self_loop = {a for (a, b) in kept if a == b}
    for a in names:
        for b in names:
            if a == b:
                continue
            if df[(a, b)] >= min_occurrences and dependency(a, b) >= dependency_threshold:
                kept[(a, b)] = "threshold"
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a in in_self_loop or b in in_self_loop:
                continue   # a self-loop already explains a b a patterns
            n = l2[(a, b)] + l2[(b, a)]
            measure = n / (n + 1)
            if n >= min_occurrences and measure >= loop_two_threshold:
                loops.append({"kind": "length_two", "activities": [a, b], "measure": round(measure, 4),
                              "occurrences": n})
                for edge in ((a, b), (b, a)):
                    if df[edge]:
                        kept.setdefault(edge, "short_loop")
    if all_connected:
        counts = {a: n for a, n, _ in acts}
        for a in names:
            # Every activity that does not always end a case gets its best outgoing arrow, and every
            # activity that does not always start one its best incoming arrow.
            outs = [(dependency(a, b), df[(a, b)], b) for b in names if b != a and df[(a, b)] >= min_occurrences]
            if outs and ends.get(a, 0) < counts[a] and not any(x == a != y for x, y in kept):
                kept.setdefault((a, max(outs)[2]), "best_connection")
            ins = [(dependency(b, a), df[(b, a)], b) for b in names if b != a and df[(b, a)] >= min_occurrences]
            if ins and starts.get(a, 0) < counts[a] and not any(y == a != x for x, y in kept):
                kept.setdefault((max(ins)[2], a), "best_connection")

    def bindings(direction: str) -> list[dict[str, Any]]:
        out = []
        for a in names:
            if direction == "split":
                targets = sorted(b for (x, b) in kept if x == a and b != a)
            else:
                targets = sorted(b for (b, y) in kept if y == a and b != a)
            if len(targets) < 2:
                continue
            pairs = []
            for i, b in enumerate(targets):
                for c in targets[i + 1:]:
                    base = df[(a, b)] + df[(a, c)] if direction == "split" else df[(b, a)] + df[(c, a)]
                    measure = (df[(b, c)] + df[(c, b)]) / (base + 1)
                    pairs.append({"between": [b, c], "measure": round(measure, 4),
                                  "kind": "and" if measure >= and_threshold else "xor"})
            kinds = {p["kind"] for p in pairs}
            out.append({"activity": a, "branches": targets, "pairs": pairs,
                        "type": "and" if kinds == {"and"} else "xor" if kinds == {"xor"} else "mixed"})
        return out

    edges = [{"from": a, "to": b, "transition_count": df[(a, b)], "dependency": round(dependency(a, b), 4),
              "kept_by": why} for (a, b), why in kept.items()]
    edges += [{"from": START, "to": a, "transition_count": n, "dependency": None, "kept_by": "start"}
              for a, n in starts.items() if n >= min_occurrences]
    edges += [{"from": a, "to": END, "transition_count": n, "dependency": None, "kept_by": "end"}
              for a, n in ends.items() if n >= min_occurrences]
    edges.sort(key=lambda e: (-e["transition_count"], e["from"], e["to"]))
    total_df = sum(df.values())
    shown = sum(df[(e["from"], e["to"])] for e in edges if e["dependency"] is not None)
    activities = [{"activity": a, "event_count": n, "case_count": c} for a, n, c in acts]
    return {"format": FORMAT, "log_id": log_id, **data,
            "parameters": {"dependency_threshold": dependency_threshold, "and_threshold": and_threshold,
                           "loop_two_threshold": loop_two_threshold, "min_occurrences": min_occurrences,
                           "all_connected": all_connected},
            "activities": activities, "start": START, "end": END,
            "start_activities": dict(sorted(starts.items(), key=lambda kv: -kv[1])),
            "end_activities": dict(sorted(ends.items(), key=lambda kv: -kv[1])),
            "edges": edges, "splits": bindings("split"), "joins": bindings("join"), "loops": loops,
            "coverage": {"directly_follows_pairs": len(df), "arrows_kept": sum(1 for e in edges if e["dependency"] is not None),
                         "transitions_on_kept_arrows_share": round(shown / total_df, 4) if total_df else None},
            "note": ("A dependency graph inferred from the order of recorded events: arrows with weak or two-way "
                     "evidence are dropped as noise, so it is simpler than the full map. AND and XOR are inferred "
                     "from how often branches follow each other in both orders, not from a modelled gateway.")}


def evidence_cases(log_id: str, case_type: str | None = None, *, selection: dict[str, Any],
                   cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Review recorded occurrences supporting one structural observation."""
    if not isinstance(selection, dict) or selection.keys() - {"group", "activities", "after", "limit"}:
        raise EventLogWorkspaceError("unknown heuristics case setting")
    group, labels = selection.get("group"), selection.get("activities")
    if group not in ("transition", "length_two", "start", "end"):
        raise EventLogWorkspaceError("choose a transition, back-and-forth loop, start or end")
    if (not isinstance(labels, list) or len(labels) != (2 if group in ("transition", "length_two") else 1)
            or any(not isinstance(a, str) or not a or len(a) > 200 for a in labels)):
        raise EventLogWorkspaceError("choose the exact activity labels from the report")
    if group == "length_two" and labels[0] == labels[1]:
        raise EventLogWorkspaceError("a back-and-forth loop needs two different activities")
    after, limit = selection.get("after"), selection.get("limit", 50)
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise EventLogWorkspaceError("limit must be a whole number from 1 to 100")
    where = {
        "transition": "activity = ? AND nxt = ?",
        "length_two": "nxt2 = activity AND ((activity = ? AND nxt = ?) OR (nxt = ? AND activity = ?))",
        "start": "activity = ? AND rn = 1",
        "end": "activity = ? AND rn = len",
    }[group]
    params = labels * 2 if group == "length_two" else labels

    def run(conn):
        conn.execute(_SEQUENCE)
        conn.execute(f"""CREATE TEMP TABLE hm_cases AS
            SELECT case_id, count(*) AS occurrences, min(ts) AS first_at, max(ts) AS last_at
            FROM hm_seq WHERE {where} GROUP BY case_id""", params)
        total, occurrences = conn.execute("SELECT count(*), coalesce(sum(occurrences), 0) FROM hm_cases").fetchone()
        rows = conn.execute("""SELECT case_id, occurrences, first_at, last_at FROM hm_cases
            WHERE ? IS NULL OR case_id > ? ORDER BY case_id LIMIT ?""", [after, after, limit + 1]).fetchall()
        return {"total": total, "occurrences": occurrences, "rows": rows}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data["rows"]
    return {"group": group, "activities": labels, "case_notion": data["case_notion"],
            "total": data["total"], "occurrences": data["occurrences"],
            "cases": [{"case_id": key, "occurrences": n, "first_at": first.isoformat() + "Z",
                       "last_at": last.isoformat() + "Z"} for key, n, first, last in rows[:limit]],
            "next_after": rows[limit - 1][0] if len(rows) > limit else None}


__all__ = ["heuristics_net", "evidence_cases", "FORMAT"]
