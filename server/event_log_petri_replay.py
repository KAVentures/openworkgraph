"""Check an event log against a Petri net from another tool, by token-based replay.

ProM, PM4Py, Apromore, WoPeD and most academic tools exchange process models as Petri nets in PNML
(ISO/IEC 15909-2). This module reads such a net and replays the recorded cases on it with the
token-based replay of Rozinat and van der Aalst (2008), as PM4Py's ``token_based_replay`` does:

* each case starts with the net's initial marking and fires the transition labelled with each
  activity in order;
* a transition that is not enabled can first be enabled by firing silent (invisible) transitions,
  found by a bounded search; if that fails, the missing tokens are created and counted;
* at the end, silent transitions may move toward the final marking, the final marking is consumed
  (counting tokens still missing) and any tokens left over are counted as remaining.

Fitness per case is ``½(1 − missing/consumed) + ½(1 − remaining/produced)``; the log's fitness uses
the same formula over the totals. A case fits when nothing is missing or left over. Places where
tokens go missing or are left behind show where the recorded cases and the net disagree.

The net is read from the request and not stored. XML with a DOCTYPE or entity declaration is
refused, and the net is bounded in size. Labels are matched to activity names exactly.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from typing import Any
from xml.etree import ElementTree as ET

from . import event_log_workspace as workspace
from .event_log_variant_coverage import VARIANT_KEY_SQL
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_petri_replay.v1"
MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 5_000
MAX_ARCS = 20_000
MAX_WEIGHT = 1_000
MAX_VARIANTS = 2_000
MAX_TRACE_LENGTH = 500
MAX_SILENT_STATES = 400
TOP_VARIANTS = 20
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    text = _child(element, "text")
    value = (text.text if text is not None else element.text) or ""
    return value.strip() or None


def _count(raw: str | None, what: str, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise EventLogWorkspaceError(f"{what} must be a whole number, not {raw[:40]!r}") from None
    if not 0 <= value <= MAX_WEIGHT:
        raise EventLogWorkspaceError(f"{what} must be from 0 to {MAX_WEIGHT}")
    return value


def parse_pnml(data: bytes) -> dict[str, Any]:
    """Places, transitions (label ``None`` = silent), weighted arcs and the two markings of a PNML net."""
    if len(data) > MAX_BYTES:
        raise EventLogWorkspaceError(f"the Petri net file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if _FORBIDDEN.search(data):
        raise EventLogWorkspaceError("XML with a DOCTYPE or entity declaration is not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EventLogWorkspaceError(f"the file is not well-formed XML: {exc}") from None
    net = root if _local(root.tag) == "net" else next((e for e in root.iter() if _local(e.tag) == "net"), None)
    if net is None:
        raise EventLogWorkspaceError("no <net> element was found; is this a PNML file?")
    places: dict[str, str] = {}
    initial: Counter = Counter()
    transitions: dict[str, str | None] = {}
    arcs: list[tuple[str, str, int]] = []
    final: Counter = Counter()
    for element in net.iter():
        kind = _local(element.tag)
        if kind == "place" and element.get("idref") is not None:
            continue   # a reference inside <finalmarkings>, read below
        if kind == "place":
            pid = element.get("id")
            if not pid:
                raise EventLogWorkspaceError("a place has no id")
            places[pid] = _text(_child(element, "name")) or pid
            tokens = _count(_text(_child(element, "initialMarking")), "an initial marking", 0)
            if tokens:
                initial[pid] = tokens
        elif kind == "transition":
            tid = element.get("id")
            if not tid:
                raise EventLogWorkspaceError("a transition has no id")
            label = _text(_child(element, "name"))
            silent = any(_local(c.tag) == "toolspecific" and (c.get("activity") or "") == "$invisible$" for c in element)
            transitions[tid] = None if silent or label is None else label
        elif kind == "arc":
            source, target = element.get("source"), element.get("target")
            if not source or not target:
                raise EventLogWorkspaceError("an arc has no source or target")
            arcs.append((source, target, max(1, _count(_text(_child(element, "inscription")), "an arc weight", 1))))
        elif kind == "finalmarkings":
            marking = _child(element, "marking")
            for place in (marking if marking is not None else []):
                if _local(place.tag) == "place" and place.get("idref"):
                    tokens = _count(_text(place), "a final marking", 0)
                    if tokens:
                        final[place.get("idref")] += tokens
        if len(places) + len(transitions) > MAX_NODES or len(arcs) > MAX_ARCS:
            raise EventLogWorkspaceError(f"the net is larger than {MAX_NODES} nodes or {MAX_ARCS} arcs")
    if not places or not transitions:
        raise EventLogWorkspaceError("the net needs at least one place and one transition")
    pre: dict[str, Counter] = {t: Counter() for t in transitions}
    post: dict[str, Counter] = {t: Counter() for t in transitions}
    for source, target, weight in arcs:
        if source in places and target in transitions:
            pre[target][source] += weight
        elif source in transitions and target in places:
            post[source][target] += weight
        else:
            raise EventLogWorkspaceError(f"arc {source!r} → {target!r} must join a place and a transition")
    if not initial:
        raise EventLogWorkspaceError("the net has no initial marking; mark the start place with one token")
    final_guessed = False
    if not final:
        # No declared final marking: one token on each place nothing consumes from, as tools commonly assume.
        consumed = {p for counts in pre.values() for p in counts}
        final = Counter({p: 1 for p in places if p not in consumed})
        final_guessed = True
    for place in list(initial) + list(final):
        if place not in places:
            raise EventLogWorkspaceError(f"a marking names an unknown place {place!r}")
    return {"places": places, "transitions": transitions, "pre": pre, "post": post,
            "initial": initial, "final": final, "final_guessed": final_guessed}


def _enabled(marking: Counter, need: Counter) -> bool:
    return all(marking[p] >= n for p, n in need.items())


def _silent_path(net: dict[str, Any], marking: Counter, goal) -> list[str] | None:
    """Shortest sequence of silent transitions after which ``goal(marking)`` holds, if one is found."""
    silent = [t for t, label in net["transitions"].items() if label is None]
    if not silent:
        return None
    start = tuple(sorted((p, n) for p, n in marking.items() if n))
    queue, seen = deque([(start, [])]), {start}
    while queue and len(seen) < MAX_SILENT_STATES:
        state, path = queue.popleft()
        current = Counter(dict(state))
        for t in silent:
            if not _enabled(current, net["pre"][t]):
                continue
            nxt = current.copy()
            nxt.subtract(net["pre"][t])
            nxt.update(net["post"][t])
            key = tuple(sorted((p, n) for p, n in nxt.items() if n))
            if key in seen:
                continue
            if goal(+nxt):
                return path + [t]
            seen.add(key)
            queue.append((key, path + [t]))
    return None


def _replay(net: dict[str, Any], trace: tuple[str, ...], by_label: dict[str, list[str]]) -> dict[str, Any]:
    marking = Counter(net["initial"])
    produced = sum(marking.values())
    consumed = missing = 0
    missing_at: Counter = Counter()
    problem_transitions: Counter = Counter()
    unknown: list[str] = []
    fired: Counter = Counter()

    def fire(t: str) -> None:
        nonlocal produced, consumed, missing
        fired[t] += 1
        for p, n in net["pre"][t].items():
            if marking[p] < n:
                missing += n - marking[p]
                missing_at[p] += n - marking[p]
                marking[p] = n
            marking[p] -= n
            consumed += n
        for p, n in net["post"][t].items():
            marking[p] += n
            produced += n

    for activity in trace:
        candidates = by_label.get(activity)
        if not candidates:
            unknown.append(activity)
            continue
        chosen = next((t for t in candidates if _enabled(marking, net["pre"][t])), None)
        if chosen is None:
            for t in candidates:
                path = _silent_path(net, marking, lambda m, need=net["pre"][t]: _enabled(m, need))
                if path is not None:
                    for silent in path:
                        fire(silent)
                    chosen = t
                    break
        if chosen is None:
            chosen = candidates[0]
            problem_transitions[chosen] += 1
        fire(chosen)
    final = net["final"]
    if not _enabled(marking, final) or sum((+marking).values()) != sum(final.values()):
        path = _silent_path(net, marking, lambda m: m == final)
        if path is None:
            path = _silent_path(net, marking, lambda m: _enabled(m, final))
        for silent in path or []:
            fire(silent)
    for p, n in final.items():
        if marking[p] < n:
            missing += n - marking[p]
            missing_at[p] += n - marking[p]
            marking[p] = n
        marking[p] -= n
        consumed += n
    remaining_at = Counter({p: n for p, n in marking.items() if n > 0})
    remaining = sum(remaining_at.values())
    fitness = 0.5 * (1 - missing / consumed if consumed else 1) + 0.5 * (1 - remaining / produced if produced else 1)
    return {"fits": missing == 0 and remaining == 0 and not unknown, "fitness": fitness,
            "missing": missing, "remaining": remaining, "consumed": consumed, "produced": produced,
            "missing_at": missing_at, "remaining_at": remaining_at,
            "problem_transitions": problem_transitions, "unknown": unknown, "fired": fired}


def _reachable_labels(net: dict[str, Any], marking: Counter) -> set[str]:
    """Visible labels enabled now or after silent transitions only (bounded search)."""
    labels: set[str] = set()
    silent = [t for t, label in net["transitions"].items() if label is None]
    start = tuple(sorted((p, n) for p, n in marking.items() if n))
    queue, seen = deque([start]), {start}
    while queue:
        current = Counter(dict(queue.popleft()))
        for t, label in net["transitions"].items():
            if label is not None and _enabled(current, net["pre"][t]):
                labels.add(label)
        if len(seen) >= MAX_SILENT_STATES:
            continue
        for t in silent:
            if _enabled(current, net["pre"][t]):
                nxt = current.copy()
                nxt.subtract(net["pre"][t])
                nxt.update(net["post"][t])
                key = tuple(sorted((p, n) for p, n in nxt.items() if n))
                if key not in seen:
                    seen.add(key)
                    queue.append(key)
    return labels


def _precision(net: dict[str, Any], rows, by_label: dict[str, list[str]]) -> dict[str, Any]:
    """ETConformance precision (Muñoz-Gama and Carmona, 2010) over the prefixes that replay without problems.

    For each prefix of the log, the activities the net allows next (escaping edges are those no case
    took after that prefix) are weighed by how many cases share the prefix."""
    observed: dict[tuple[str, ...], set[str]] = {}
    weight: Counter = Counter()
    for path, n in rows:
        for i in range(len(path)):
            observed.setdefault(tuple(path[:i]), set()).add(path[i])
            weight[tuple(path[:i])] += n
    allowed: dict[tuple[str, ...], set[str]] = {}
    for path, _ in rows:
        marking = Counter(net["initial"])
        for i, activity in enumerate(path):
            prefix = tuple(path[:i])
            if prefix not in allowed:
                allowed[prefix] = _reachable_labels(net, marking)
            candidates = by_label.get(activity) or []
            chosen = next((t for t in candidates if _enabled(marking, net["pre"][t])), None)
            if chosen is None:
                for t in candidates:
                    route = _silent_path(net, marking, lambda m, need=net["pre"][t]: _enabled(m, need))
                    if route is not None:
                        for silent in route:
                            marking.subtract(net["pre"][silent])
                            marking.update(net["post"][silent])
                        chosen = t
                        break
            if chosen is None:
                break   # the rest of this case does not replay; its later prefixes are not judged
            marking.subtract(net["pre"][chosen])
            marking.update(net["post"][chosen])
    total = escaping = 0
    escapes: Counter = Counter()
    for prefix, labels in allowed.items():
        w = weight[prefix]
        extra = labels - observed.get(prefix, set())
        total += w * len(labels)
        escaping += w * len(extra)
        for label in extra:
            escapes[(prefix[-1] if prefix else None, label)] += w
    return {"precision": round(1 - escaping / total, 4) if total else None, "prefixes_judged": len(allowed),
            "escaping_edges": [{"after": a, "net_also_allows": b, "cases": k} for (a, b), k in escapes.most_common(20)]}


def replay(log_id: str, pnml: bytes, case_type: str | None = None, *,
           cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    net = parse_pnml(pnml)
    by_label: dict[str, list[str]] = {}
    for t, label in sorted(net["transitions"].items()):
        if label is not None:
            by_label.setdefault(label, []).append(t)

    def run(conn):
        variant_key = VARIANT_KEY_SQL.format(path="path")
        rows = conn.execute(f"""SELECT path, count(*) AS cases, {variant_key} FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id HAVING count(*) <= ?)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "cases_total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data.pop("rows")
    if not rows:
        raise EventLogWorkspaceError("No cases are available to replay in this selection")
    totals = Counter()
    missing_at: Counter = Counter()
    remaining_at: Counter = Counter()
    problems: Counter = Counter()
    unknown: Counter = Counter()
    fitting_cases = cases = 0
    fitness_sum = 0.0
    variants = []
    for path, n, variant_id in rows:
        result = _replay(net, tuple(path), by_label)
        cases += n
        fitting_cases += n if result["fits"] else 0
        fitness_sum += result["fitness"] * n
        for key in ("missing", "remaining", "consumed", "produced"):
            totals[key] += result[key] * n
        for p, k in result["missing_at"].items():
            missing_at[p] += k * n
        for p, k in result["remaining_at"].items():
            remaining_at[p] += k * n
        for t, k in result["problem_transitions"].items():
            problems[t] += k * n
        for a in set(result["unknown"]):
            unknown[a] += n
        if not result["fits"]:
            variants.append({"path": list(path), "variant_id": variant_id, "cases": n, "fitness": round(result["fitness"], 4),
                             "missing_tokens": result["missing"], "remaining_tokens": result["remaining"],
                             "activities_not_in_net": sorted(set(result["unknown"]))})
    c, p = totals["consumed"], totals["produced"]
    log_fitness = 0.5 * (1 - totals["missing"] / c if c else 1) + 0.5 * (1 - totals["remaining"] / p if p else 1)
    names = net["places"]
    labels = {t: (label if label is not None else f"silent {t}") for t, label in net["transitions"].items()}
    observed = {a for path, _, _ in rows for a in path}
    variants.sort(key=lambda v: (-v["cases"], v["fitness"]))
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "net": {"places": len(names), "transitions": len(net["transitions"]),
                "silent_transitions": sum(1 for v in net["transitions"].values() if v is None),
                "labels": sorted(by_label), "final_marking_declared": not net["final_guessed"]},
        "cases": cases, "cases_total": data.get("cases_total"),
        "cases_excluded": max(0, (data.get("cases_total") or cases) - cases),
        "variants": len(rows),
        "fitness": {"log": round(log_fitness, 4), "average_case": round(fitness_sum / cases, 4),
                    "fitting_cases": fitting_cases, "fitting_case_share": round(fitting_cases / cases, 4)},
        "precision": _precision(net, [(path, cases) for path, cases, _ in rows], by_label),
        "tokens": {k: totals[k] for k in ("produced", "consumed", "missing", "remaining")},
        "places": sorted(({"place": names[pl], "missing": missing_at[pl], "remaining": remaining_at[pl]}
                          for pl in set(missing_at) | set(remaining_at)),
                         key=lambda r: (-(r["missing"] + r["remaining"]), r["place"]))[:50],
        "transitions_fired_without_tokens": [{"transition": labels[t], "times": k}
                                             for t, k in problems.most_common(30)],
        "activities_not_in_net": [{"activity": a, "cases": k} for a, k in unknown.most_common(30)],
        "net_labels_never_seen": sorted(set(by_label) - observed),
        "non_fitting_variants": variants[:TOP_VARIANTS],
        "method": ("token-based replay (Rozinat and van der Aalst, 2008): silent transitions fire when a bounded "
                   "search finds them enabling the next step or reaching the final marking; otherwise missing "
                   "tokens are created and counted"),
        "caveat": ("Token replay can over-count problems when a net has duplicate labels or long silent paths. "
                   "Precision judges only the parts of cases that replay without problems. Activities the net "
                   "does not have are skipped and counted."),
    }


__all__ = ["parse_pnml", "replay", "FORMAT", "MAX_BYTES"]
