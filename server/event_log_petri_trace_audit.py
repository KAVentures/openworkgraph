"""Exact completed-trace membership under parsed ordinary P/T semantics.

Explore (marking, trace offset) without manufacturing missing tokens.
A found completion is a witness, exhaustive failure is a proof, and any
resource cutoff is inconclusive. This is separate from heuristic fitness.
"""
from __future__ import annotations

from collections import Counter, deque
from hashlib import sha256

from . import event_log_petri_replay as replay
from . import event_log_workspace as workspace
from .event_log_variant_coverage import VARIANT_KEY_SQL

FORMAT = "openworkgraph.petri_trace_audit.v1"
MAX_VARIANTS = 200
MAX_STATES = 5000
MAX_WORK = 500_000
MAX_TOKENS = 1000
MAX_WITNESS = 500


def check(net, trace, budget):
    def key(marking):
        return tuple(sorted((p, n) for p, n in marking.items() if n > 0))

    initial = key(net["initial"])
    final = key(net["final"])
    silent = sorted(t for t, label in net["transitions"].items() if label is None)
    visible = {}
    for t, label in sorted(net["transitions"].items()):
        if label is not None:
            visible.setdefault(label, []).append(t)
    start = (initial, 0)
    parents = {start: None}
    queue = deque([start])
    longest = 0
    cutoff = None
    if any(n > MAX_TOKENS for _, n in initial):
        return {"status": "inconclusive", "limit": "token_limit", "states": 1,
                "longest_prefix": 0, "witness": None}
    while queue:
        state = queue.popleft()
        marking_key, offset = state
        longest = max(longest, offset)
        if offset == len(trace) and marking_key == final:
            steps = []
            while parents[state] is not None:
                state, tid = parents[state]
                steps.append(tid)
            steps.reverse()
            return {"status": "accepted", "limit": cutoff, "states": len(parents),
                    "longest_prefix": longest, "witness": {"transition_ids": steps[:MAX_WITNESS],
                    "steps": len(steps), "truncated": len(steps) > MAX_WITNESS}}
        marking = Counter(dict(marking_key))
        candidates = silent + (visible.get(trace[offset], []) if offset < len(trace) else [])
        for tid in candidates:
            # Charge marking/arc operations as well as candidate checks.
            cost = 1 + len(marking) + len(net["pre"][tid]) + len(net["post"][tid])
            if budget[0] + cost > MAX_WORK:
                return {"status": "inconclusive", "limit": "work_limit", "states": len(parents),
                        "longest_prefix": longest, "witness": None}
            budget[0] += cost
            if not replay._enabled(marking, net["pre"][tid]):
                continue
            nxt = marking.copy()
            nxt.subtract(net["pre"][tid])
            nxt.update(net["post"][tid])
            if any(n > MAX_TOKENS for n in nxt.values()):
                cutoff = cutoff or "token_limit"
                continue
            target = (key(nxt), offset + (net["transitions"][tid] is not None))
            if target in parents:
                continue
            if len(parents) >= MAX_STATES:
                cutoff = cutoff or "state_limit"
                continue
            parents[target] = (state, tid)
            queue.append(target)
    return {"status": "inconclusive" if cutoff else "rejected", "limit": cutoff,
            "states": len(parents), "longest_prefix": longest, "witness": None}


def audit(log_id, data, case_type=None, *, cohort=None):
    if b"\x00" in data:
        raise workspace.EventLogWorkspaceError("PNML requires ASCII-compatible XML")
    net = replay.parse_pnml(data)
    if net["final_guessed"]:
        raise workspace.EventLogWorkspaceError("exact trace audit requires a declared final marking")

    def run(conn):
        key = VARIANT_KEY_SQL.format(path="path")
        rows = conn.execute(f"""SELECT path, count(*) AS cases, {key} FROM (
            SELECT list(activity ORDER BY ts,event_id) AS path FROM ce GROUP BY case_id
            HAVING count(*) <= ?) GROUP BY path ORDER BY cases DESC,path LIMIT ?""",
            [replay.MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "total": total}

    source = workspace._query_filtered(log_id, case_type, cohort, run)
    budget = [0]
    rows = []
    counts = Counter()
    for trace, n, ident in source["rows"]:
        if budget[0] >= MAX_WORK:
            result = {"status": "inconclusive", "limit": "work_limit", "states": 0,
                      "longest_prefix": None, "witness": None}
        else:
            result = check(net, trace, budget)
        counts[result["status"]] += n
        rows.append({"variant_id": ident, "path": list(trace), "cases": n, **result})
    total = source["total"]
    examined = sum(counts.values())
    excluded = total - examined
    bounds = None if not total else {"lower": counts["accepted"] / total,
                                    "upper": (counts["accepted"] + counts["inconclusive"] + excluded) / total}
    return {"format": FORMAT, "log_id": log_id, "case_notion": source.get("case_notion"),
            "pnml_sha256": sha256(data).hexdigest(), "cases_total": total, "cases_examined": examined,
            "cases_excluded": excluded, "counts": {k: counts[k] for k in ("accepted", "rejected", "inconclusive")},
            "accepted_fraction_bounds": bounds, "variants": rows, "work": budget[0],
            "limits": {"variants": MAX_VARIANTS, "states_per_variant": MAX_STATES,
                       "work": MAX_WORK, "tokens_per_place": MAX_TOKENS,
                       "trace_length": replay.MAX_TRACE_LENGTH},
            "caveat": "Membership under the ordinary P/T semantics of the parsed net and retained event order only. "
                      "A resource cutoff or excluded case is not evidence of nonconformance. "
                      "No timing, resource, business-policy or actual execution authorization is verified."}
