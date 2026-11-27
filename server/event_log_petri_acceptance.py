"""Executable acceptance suites for exact visible PNML trace/prefix semantics.

Uses the marking graph from the formal language checker. A prefix can be
fireable without being extendable to a completed trace; those are separate
assertions. Unknown graph coverage never proves a negative assertion.
"""
from __future__ import annotations

import heapq
import json
from hashlib import sha256

from . import event_log_petri_language as language
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_acceptance.v1"
SUITE_FORMAT = "openworkgraph.petri_acceptance_suite.v1"
MAX_CASES = 40
MAX_TRACE = 100
MAX_LABELS = 100
MAX_SUITE_BYTES = 256 * 1024
MAX_WORK = 600_000
MAX_SUFFIX = 100


def _invalid(message):
    raise EventLogWorkspaceError(message)


def _labels(value, maximum, unique=False):
    if not isinstance(value, list) or len(value) > maximum:
        _invalid(f"expected an activity list of at most {maximum} entries")
    if any(not isinstance(s, str) or not s.strip() or len(s) > language.MAX_LABEL_LENGTH
           or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in s) for s in value):
        _invalid("activity labels must be nonempty, at most 200 characters and contain no control characters")
    if unique and len(set(value)) != len(value):
        _invalid("duplicate activity in next-step expectations")
    return list(value)


def parse_suite(spec):
    if not isinstance(spec, dict) or set(spec) != {"format", "cases"} or spec["format"] != SUITE_FORMAT:
        _invalid("send an acceptance suite with the supported format and cases")
    if not isinstance(spec["cases"], list) or not 1 <= len(spec["cases"]) <= MAX_CASES:
        _invalid(f"provide between 1 and {MAX_CASES} acceptance cases")
    result = []
    ids = set()
    for case in spec["cases"]:
        if not isinstance(case, dict) or set(case) != {"id", "trace", "expect"}:
            _invalid("each case needs exactly id, trace and expect")
        ident = case["id"]
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 80
                or any(not (c.isascii() and (c.isalnum() or c in "._-")) for c in ident)
                or ident in ids):
            _invalid("case IDs must be distinct, 1–80 ASCII letters, digits, dots, dashes or underscores")
        ids.add(ident)
        trace = _labels(case["trace"], MAX_TRACE)
        expect = case["expect"]
        allowed = {"completed", "fireable", "finishable", "all_prefix_states_finishable",
                   "required_next", "forbidden_next", "exact_next"}
        if not isinstance(expect, dict) or not expect or set(expect) - allowed:
            _invalid("unknown or missing acceptance expectations")
        normalized = {}
        for key in ("completed", "fireable", "finishable", "all_prefix_states_finishable"):
            if key in expect:
                if not isinstance(expect[key], bool):
                    _invalid(f"{key} expectation must be boolean")
                normalized[key] = expect[key]
        for key in ("required_next", "forbidden_next", "exact_next"):
            if key in expect:
                normalized[key] = sorted(_labels(expect[key], MAX_LABELS, unique=True))
        if set(normalized.get("required_next", [])) & set(normalized.get("forbidden_next", [])):
            _invalid("required and forbidden next activities overlap")
        if "exact_next" in normalized:
            exact = set(normalized["exact_next"])
            if not set(normalized.get("required_next", [])) <= exact or exact & set(normalized.get("forbidden_next", [])):
                _invalid("exact next activities contradict required or forbidden next activities")
        result.append({"id": ident, "trace": trace, "expect": normalized})
    canonical = {"format": SUITE_FORMAT, "cases": result}
    data = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > MAX_SUITE_BYTES:
        _invalid("normalized acceptance suite exceeds the byte limit")
    return canonical, sha256(data).hexdigest()


class _Limit(Exception):
    pass


class _Budget:
    def __init__(self):
        self.work = 0

    def spend(self, n=1):
        if self.work + n > MAX_WORK:
            raise _Limit()
        self.work += n


def _reverse_completion(graph, budget):
    """Shortest observed suffix from each marking, by visible steps then firings."""
    edges = graph["edges"]
    reverse = [[] for _ in edges]
    for source, outgoing in enumerate(edges):
        budget.spend()
        for label, targets in sorted(outgoing.items(), key=lambda kv: (kv[0] is not None, kv[0] or "")):
            budget.spend(1 + len(targets))
            for target in sorted(targets):
                reverse[target].append((source, label))
    final = graph["final"]
    if final is None:
        return {}, {}
    dist = {final: (0, 0)}
    successor = {}
    queue = [(0, 0, final)]
    while queue:
        v, f, node = heapq.heappop(queue)
        budget.spend()
        if dist[node] != (v, f):
            continue
        for source, label in reverse[node]:
            budget.spend()
            cost = (v + (label is not None), f + 1)
            if source not in dist or cost < dist[source]:
                dist[source] = cost
                successor[source] = (node, label)
                heapq.heappush(queue, (*cost, source))
    return dist, successor


def _closure(graph, seeds, budget):
    seen = set(seeds)
    pending = list(seen)
    while pending:
        node = pending.pop()
        budget.spend()
        for nxt in graph["edges"][node].get(None, ()):
            budget.spend()
            if nxt not in seen:
                seen.add(nxt)
                pending.append(nxt)
    return seen


def _observed(graph, trace, dist, successor, budget):
    states = _closure(graph, {0}, budget)
    matched = 0
    for label in trace:
        targets = set()
        for node in states:
            outgoing = graph["edges"][node].get(label, ())
            budget.spend(1 + len(outgoing))
            targets.update(outgoing)
        states = _closure(graph, targets, budget)
        if not states:
            break
        matched += 1
    if matched != len(trace):
        states = set()
    final = graph["final"]
    complete = graph["complete"]

    def observed_or_unknown(found):
        return True if found else (False if complete else None)

    viable = states & dist.keys()
    next_labels = set()
    for node in states:
        budget.spend()
        for label, targets in graph["edges"][node].items():
            budget.spend(1 + len(targets))
            if label is not None and any(t in dist for t in targets):
                next_labels.add(label)
    suffix = None
    if viable:
        cursor = min(viable, key=lambda n: (dist[n], n))
        visible, firings = dist[cursor]
        labels = []
        while cursor != final:
            budget.spend()
            cursor, label = successor[cursor]
            if label is not None and len(labels) < MAX_SUFFIX:
                labels.append(label)
        suffix = {"activities": labels, "length": visible, "firings": firings,
                  "truncated": visible > MAX_SUFFIX, "shortest_proven": complete}
    return {"completed": observed_or_unknown(final is not None and final in states),
            "fireable": observed_or_unknown(bool(states)),
            "finishable": observed_or_unknown(bool(viable)),
            "all_prefix_states_finishable": bool(states) and states <= viable if complete else None,
            "matched_markings": len(states),
            "known_completable_markings": len(viable),
            "noncompletable_markings": len(states - viable) if complete else None,
            "viable_next_activities": sorted(next_labels),
            "next_activities_complete": complete,
            "matched_prefix_length": matched,
            "matched_prefix_exact": complete or matched == len(trace),
            "completion_suffix": suffix}


def _unknown():
    return {"completed": None, "fireable": None, "finishable": None,
            "all_prefix_states_finishable": None, "matched_markings": None,
            "known_completable_markings": None, "noncompletable_markings": None,
            "viable_next_activities": [], "next_activities_complete": False,
            "matched_prefix_length": None, "matched_prefix_exact": False,
            "completion_suffix": None}


def _assertions(expect, actual):
    checks = []
    for key, expected in expect.items():
        if key in {"completed", "fireable", "finishable", "all_prefix_states_finishable"}:
            observed = actual[key]
            passed = None if observed is None else observed == expected
            checks.append({"property": key, "expected": expected, "observed": observed, "passed": passed})
            continue
        known = set(actual["viable_next_activities"])
        wanted = set(expected)
        exhaustive = actual["next_activities_complete"]
        if key == "required_next":
            missing = sorted(wanted - known)
            passed = True if not missing else (False if exhaustive else None)
            detail = {"not_observed": missing}
        elif key == "forbidden_next":
            forbidden = sorted(wanted & known)
            passed = False if forbidden else (True if exhaustive or not wanted else None)
            detail = {"forbidden_observed": forbidden}
        else:
            extra = sorted(known - wanted)
            missing = sorted(wanted - known)
            passed = False if extra or (exhaustive and missing) else (True if exhaustive else None)
            detail = {"unexpected_observed": extra, "expected_not_observed": missing}
        checks.append({"property": key, "expected": expected,
                       "observed": actual["viable_next_activities"], "passed": passed, **detail})
    return checks


def _review(data, suite):
    graph = language._graph(data)
    budget = _Budget()
    exhausted = False
    try:
        dist, successor = _reverse_completion(graph, budget)
    except _Limit:
        dist, successor = {}, {}
        exhausted = True
    results = []
    for case in suite["cases"]:
        try:
            if exhausted:
                raise _Limit()
            observed = _observed(graph, case["trace"], dist, successor, budget)
        except _Limit:
            exhausted = True
            observed = _unknown()
        checks = _assertions(case["expect"], observed)
        flags = [c["passed"] for c in checks]
        status = "failed" if False in flags else ("passed" if all(v is True for v in flags) else "inconclusive")
        results.append({"id": case["id"], "trace": case["trace"], "status": status,
                        "observed": observed, "assertions": checks})
    counts = {k: sum(row["status"] == k for row in results) for k in ("passed", "failed", "inconclusive")}
    return {"model": {k: v for k, v in graph.items() if k not in {"edges", "final"}},
            "cases": results, "counts": counts, "total_work": budget.work,
            "suite_passed": False if counts["failed"] else (None if counts["inconclusive"] else True),
            "work_limit_reached": exhausted}


def review(baseline: bytes, spec, candidate: bytes | None = None):
    suite, digest = parse_suite(spec)
    before = _review(baseline, suite)
    after = _review(candidate, suite) if candidate is not None else None
    changes = None
    if after is not None:
        rows = []
        for a, b in zip(before["cases"], after["cases"]):
            left, right = a["status"], b["status"]
            change = ("inconclusive" if "inconclusive" in (left, right) else
                      "regression" if (left, right) == ("passed", "failed") else
                      "improvement" if (left, right) == ("failed", "passed") else "unchanged_" + left)
            rows.append({"id": a["id"], "baseline": left, "candidate": right, "change": change})
        changes = {"cases": rows, "regressions": sum(x["change"] == "regression" for x in rows),
                   "improvements": sum(x["change"] == "improvement" for x in rows),
                   "inconclusive": sum(x["change"] == "inconclusive" for x in rows)}
    return {"format": FORMAT, "suite_sha256": digest, "suite": suite,
            "baseline": before, "candidate": after, "changes": changes,
            "limits": {"cases": MAX_CASES, "trace_activities": MAX_TRACE, "work_per_model": MAX_WORK,
                       "suffix_activities": MAX_SUFFIX, "marking_states": language.soundness.MAX_STATES},
            "caveat": "Acceptance scenarios are declared expectations over visible activity sequences. "
                      "Passing the suite is not full model equivalence, policy compliance, or evidence of real execution."}


def _read_bounded(path, limit):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        _invalid("input file exceeds the supported byte limit")
    return data


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite JSON value")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        _invalid("suite must be strict UTF-8 JSON")


def main(argv=None):
    """Customer-operated, no-network CI entry point; JSON to stdout."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run exact PNML workflow acceptance scenarios locally")
    parser.add_argument("--baseline", required=True, help="baseline PNML file")
    parser.add_argument("--suite", required=True, help="acceptance suite JSON file")
    parser.add_argument("--candidate", help="optional candidate PNML file")
    args = parser.parse_args(argv)
    try:
        limit = language.soundness.replay.MAX_BYTES
        baseline = _read_bounded(args.baseline, limit)
        candidate = _read_bounded(args.candidate, limit) if args.candidate else None
        spec = _strict_json(_read_bounded(args.suite, MAX_SUITE_BYTES))
        result = review(baseline, spec, candidate)
    except (EventLogWorkspaceError, OSError, UnicodeError) as exc:
        print("Acceptance input refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
    # Candidate is the target if supplied; baseline failures remain visible in
    # the report but do not prevent an improved candidate from passing.
    target = result["candidate"] if args.candidate else result["baseline"]
    return 0 if target["suite_passed"] is True else (1 if target["suite_passed"] is False else 3)


if __name__ == "__main__":
    raise SystemExit(main())
