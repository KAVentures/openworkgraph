"""Automatic process model discovery into a reviewable draft (roadmap #326 G4).

An inductive-miner style discovery (in the spirit of Leemans et al.'s IMf, as
in PM4Py, ProM or Apromore) over the variants of a saved event log:

- the log is cut recursively into exclusive choices, sequences, structured
  parallel regions and loops found on its noise-filtered directly-follows graph;
- each cut splits the log by projection, so infrequent deviations are dropped
  rather than forced into the model, and frequently skipped behaviour becomes
  an optional branch;
- when no cut applies, an "activity once per case" parallel or a flower loop
  (any order) is used and reported as unstructured.

The resulting process tree is converted into a local catalog process model
(decision, merge, loop and parallel nodes) that validates against the catalog
schema and can be exported as BPMN after review. Every decision branch carries
an *unconfirmed* rule stating only the observed share of cases: observed
frequencies are not business conditions, the draft is not a verified workflow
and it never authorizes execution.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any

from pydantic import ValidationError

from shared.process_model import ProcessModel

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from . import process_discovery_provenance as provenance

MAX_VARIANTS = 5000          # most frequent variants used for discovery
MAX_TRACE_LENGTH = 300       # longer cases are excluded, not clipped
MAX_FITNESS_VARIANTS = 500
MAX_FITNESS_TRACE_LENGTH = 120   # bounds parallel-region replay work
MAX_MODEL_NODES = 40             # catalog process bound
MAX_DEPTH = 60
MAX_PARALLEL_BRANCHES = 8    # catalog parallel split bound
TAU = {"op": "tau"}
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")
_CHOICE_OPS = {"xor", "loop"}


def discover(log_id: str, case_type: str | None = None, *, noise: float = 0.2, activities: float = 1.0,
             cohort: dict[str, Any] | None = None, _record_snapshot: bool = False,
             _expected_model_sha256: str | None = None) -> dict[str, Any]:
    if not 0 <= float(noise) < 1:
        raise workspace.EventLogWorkspaceError("noise must be a share from 0 up to (not including) 1")
    if not 0 < float(activities) <= 1:
        raise workspace.EventLogWorkspaceError("activities must be a share above 0 and at most 1")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        ranked = conn.execute("""SELECT activity, count(*) AS events FROM ce
            GROUP BY activity ORDER BY events DESC, activity""").fetchall()
        total_events = sum(n for _, n in ranked)
        kept, covered = [], 0
        for activity, events in ranked:
            if kept and covered >= activities * total_events:
                break
            kept.append(activity)
            covered += events
        if len(kept) > MAX_MODEL_NODES - 2:
            raise workspace.EventLogWorkspaceError(
                f"{len(kept)} activities are shown, but a catalog process holds at most {MAX_MODEL_NODES} nodes "
                "including start, end and decisions. Lower the activity share.")
        total_cases, long_cases = conn.execute("""SELECT count(*), count(*) FILTER (WHERE n > ?) FROM (
            SELECT count(*) AS n FROM ce GROUP BY case_id)""", [MAX_TRACE_LENGTH]).fetchone()
        rows = conn.execute("""SELECT path, count(*) AS cases FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id
              HAVING count(*) <= ?)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        if not rows:
            raise workspace.EventLogWorkspaceError("No cases are available for discovery in this selection")
        keep = set(kept)
        log = _merge((tuple(a for a in path if a in keep), n) for path, n in rows)
        used_cases = sum(n for _, n in rows)
        notes: list[str] = []
        tree = _discover(log, float(noise), 0, notes)
        if tree["op"] == "tau":
            raise workspace.EventLogWorkspaceError("The selected activities leave no behaviour to model")
        model = _to_model(tree, rows[0], notes)
        checked = [(path, n) for path, n in rows[:MAX_FITNESS_VARIANTS] if len(path) <= MAX_FITNESS_TRACE_LENGTH]
        fitting = sum(n for path, n in checked if _accepts(tree, tuple(a for a in path if a in keep)))
        checked_cases = sum(n for _, n in checked)
        result = {
            "tree": tree,
            "process_model": model,
            "quality": {
                "cases_total": total_cases, "cases_used": used_cases,
                "cases_excluded_trace_length": long_cases,
                "cases_excluded_variant_limit": total_cases - long_cases - used_cases,
                "variants_used": len(rows),
                "checked_cases": checked_cases, "fitting_cases": fitting,
                "fitting_case_share": fitting / checked_cases if checked_cases else None,
                "activities_shown": len(kept), "activities_hidden": len(ranked) - len(kept),
                "event_share_shown": covered / total_events if total_events else None,
                "node_count": len(model["activities"]), "transition_count": len(model["transitions"]),
                "unconfirmed_rule_count": len(model["business_rules"]),
            },
            "simplifications": notes,
            "settings": {"noise": float(noise), "activities": float(activities)},
            "discovery_method": {"algorithm": "bounded_inductive.v1", "max_variants": MAX_VARIANTS,
                                 "max_trace_length": MAX_TRACE_LENGTH,
                                 "max_fitness_variants": MAX_FITNESS_VARIANTS,
                                 "max_fitness_trace_length": MAX_FITNESS_TRACE_LENGTH},
            "cohort": cohorts.describe(chosen),
            "interpretation": ("A discovered draft for review, not a verified workflow. Decision rules state only "
                               "observed shares of cases; replace them with the real business conditions. "
                               "Fitting cases are replayed against the draft over the shown activities only. "
                               "Infrequent behaviour below the noise filter is left out."),
        }
        result['process_model_sha256'] = provenance.model_digest(model)
        if _record_snapshot:
            result['source_snapshot'] = provenance.retain(conn, log_id, workspace._case_type(conn, log_id, case_type),
                                                         result, _expected_model_sha256)
        return result
    return workspace._query(log_id, case_type, run)


# --- discovery ---------------------------------------------------------------

def _merge(pairs) -> list[tuple[tuple[str, ...], int]]:
    counts: Counter = Counter()
    for trace, n in pairs:
        counts[trace] += n
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _leaf(activity: str) -> dict[str, Any]:
    return {"op": "activity", "activity": activity}


def _discover(log, noise: float, depth: int, notes: list[str]) -> dict[str, Any]:
    total = sum(n for _, n in log)
    empty = sum(n for t, n in log if not t)
    log = [(t, n) for t, n in log if t]
    if not log:
        return TAU
    if empty > noise * total:
        # Frequently empty behaviour is optional; rare empty traces are noise.
        return {"op": "xor", "children": [TAU, _discover(log, noise, depth + 1, notes)],
                "cases": [empty, total - empty]}
    total = sum(n for _, n in log)
    alphabet = sorted({a for t, _ in log for a in t})
    if len(alphabet) == 1:
        single = sum(n for t, n in log if len(t) == 1)
        if single >= (1 - noise) * total:
            return _leaf(alphabet[0])
        return {"op": "loop", "children": [_leaf(alphabet[0]), TAU],
                "repeats": sum(n * (len(t) - 1) for t, n in log), "cases": total}
    if depth >= MAX_DEPTH:
        return _flower(alphabet, log, notes)
    dfg, starts, ends = _dfg(log)
    edges = _filtered(dfg, ends, noise)
    for cut in (_xor_cut, _seq_cut, _and_cut, _loop_cut):
        parts = cut(alphabet, edges, starts, ends, noise)
        if parts:
            return _split(cut, parts, log, noise, depth, notes)
    return _fall_through(alphabet, log, noise, depth, notes)


def _dfg(log):
    dfg: Counter = Counter()
    starts: Counter = Counter()
    ends: Counter = Counter()
    for trace, n in log:
        starts[trace[0]] += n
        ends[trace[-1]] += n
        for a, b in zip(trace, trace[1:]):
            dfg[(a, b)] += n
    return dfg, starts, ends


def _filtered(dfg, ends, noise):
    """IMf: drop each activity's outgoing edges below noise × its strongest one (ending the case counts)."""
    strongest: dict[str, int] = defaultdict(int, ends)
    for (a, _), n in dfg.items():
        strongest[a] = max(strongest[a], n)
    return {edge for edge, n in dfg.items() if n >= noise * strongest[edge[0]]}


def _components(nodes, linked) -> list[set[str]]:
    seen: set[str] = set()
    parts = []
    for node in nodes:
        if node in seen:
            continue
        part, pending = {node}, [node]
        seen.add(node)
        while pending:
            current = pending.pop()
            for other in nodes:
                if other not in seen and linked(current, other):
                    seen.add(other)
                    part.add(other)
                    pending.append(other)
        parts.append(part)
    return parts


def _xor_cut(alphabet, edges, starts, ends, noise):
    parts = _components(alphabet, lambda a, b: (a, b) in edges or (b, a) in edges)
    return parts if len(parts) > 1 else None


def _reach(alphabet, edges):
    out = defaultdict(set)
    for a, b in edges:
        out[a].add(b)
    reach = {}
    for node in alphabet:
        seen, pending = set(), [node]
        while pending:
            for nxt in out[pending.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    pending.append(nxt)
        reach[node] = seen
    return reach


def _seq_cut(alphabet, edges, starts, ends, noise):
    reach = _reach(alphabet, edges)
    groups = _components(alphabet, lambda a, b: b in reach[a] and a in reach[b])

    def reaches(g, h):
        return any(reach[a] & h for a in g)
    merged = True
    while merged:   # incomparable groups belong to the same sequence step
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if not reaches(groups[i], groups[j]) and not reaches(groups[j], groups[i]):
                    groups[i] |= groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    if len(groups) < 2:
        return None
    snapshot = list(groups)   # list.sort empties the list it sorts while keys are computed
    groups.sort(key=lambda g: -sum(reaches(g, h) for h in snapshot if h is not g))
    for i in range(len(groups)):    # a sequence must be one-directional
        for j in range(i + 1, len(groups)):
            if reaches(groups[j], groups[i]):
                return None
    return groups


def _and_cut(alphabet, edges, starts, ends, noise):
    parts = _components(alphabet, lambda a, b: not ((a, b) in edges and (b, a) in edges))
    top_start, top_end = max(starts.values()), max(ends.values())
    real_starts = {a for a, n in starts.items() if n >= noise * top_start}
    real_ends = {a for a, n in ends.items() if n >= noise * top_end}
    complete = [p for p in parts if p & real_starts and p & real_ends]
    if len(complete) < 2:
        return None
    for part in parts:
        if not (part & real_starts and part & real_ends):
            complete[0] |= part
    return complete


def _loop_cut(alphabet, edges, starts, ends, noise):
    body = set(starts) | set(ends)
    rest = [a for a in alphabet if a not in body]
    if not rest:
        return None
    redo: set[str] = set()
    for part in _components(rest, lambda a, b: (a, b) in edges or (b, a) in edges):
        entered = {a for a, b in edges if b in part and a not in part}
        exits = {b for a, b in edges if a in part and b not in part}
        if entered and exits and entered <= set(ends) and exits <= set(starts):
            redo |= part
        else:
            body |= part
    return [body, redo] if redo else None


def _split(cut, parts, log, noise, depth, notes):
    index = {a: i for i, part in enumerate(parts) for a in part}
    if cut is _xor_cut:
        logs = [[] for _ in parts]
        for trace, n in log:
            votes = Counter(index[a] for a in trace)
            best = min(votes, key=lambda i: (-votes[i], i))
            logs[best].append((tuple(a for a in trace if index[a] == best), n))
        children = [_discover(_merge(sub), noise, depth + 1, notes) for sub in logs]
        return {"op": "xor", "children": children, "cases": [sum(n for _, n in sub) for sub in logs]}
    if cut is _loop_cut:
        body_log, redo_log, repeats = [], [], 0
        for trace, n in log:
            segments: list[tuple[bool, list[str]]] = []
            for a in trace:
                in_redo = index[a] == 1
                if segments and segments[-1][0] == in_redo:
                    segments[-1][1].append(a)
                else:
                    segments.append((in_redo, [a]))
            # A case that starts or ends inside the redo part skipped the body there.
            if segments[0][0]:
                segments.insert(0, (False, []))
            if segments[-1][0]:
                segments.append((False, []))
            for in_redo, segment in segments:
                (redo_log if in_redo else body_log).append((tuple(segment), n))
                repeats += n if in_redo else 0
        body = _discover(_merge(body_log), noise, depth + 1, notes)
        redo = _discover(_merge(redo_log), noise, depth + 1, notes)
        return {"op": "loop", "children": [body, redo], "repeats": repeats, "cases": sum(n for _, n in log)}
    projected = [_merge((tuple(a for a in t if index[a] == i), n) for t, n in log) for i in range(len(parts))]
    mark = len(notes)
    children = [_discover(sub, noise, depth + 1, notes) for sub in projected]
    if cut is _seq_cut:
        return {"op": "seq", "children": children}
    return _parallel(children, parts, log, noise, notes, mark)


def _parallel(children, parts, log, noise, notes, mark):
    """Keep a structured parallel region the catalog can express, else the observed order if it
    still fits, else an any-order loop."""
    if len(children) <= MAX_PARALLEL_BRANCHES and not any(_has_choice(c) for c in children):
        return {"op": "and", "children": children}
    index = {a: i for i, part in enumerate(parts) for a in part}
    weight: Counter = Counter()
    position: Counter = Counter()
    for trace, n in log:
        first: dict[int, int] = {}
        for at, a in enumerate(trace):
            first.setdefault(index[a], at)
        for i, at in first.items():
            position[i] += n * at / len(trace)
            weight[i] += n
    order = sorted(range(len(children)), key=lambda i: (position[i] / weight[i] if weight[i] else 1, i))
    ordered = {"op": "seq", "children": [children[i] for i in order], "simplified_from": "parallel"}
    names = ", ".join(_first_activity(children[i]) or "skipped work" for i in order)
    total = sum(n for _, n in log)
    if sum(n for t, n in log if _accepts(ordered, t)) >= (1 - noise) * total:
        notes.append(f"Concurrent behaviour of {names} is drawn in its most common observed order, because a "
                     "catalog parallel region cannot contain decisions or loops or more than 8 branches.")
        return ordered
    del notes[mark:]   # the children's own simplifications are replaced by the loop
    return _flower(sorted({a for t, _ in log for a in t}), log, notes,
                   f"Concurrent behaviour of {names} has decisions or loops a catalog parallel region cannot hold "
                   "and no single order fits")


def _fall_through(alphabet, log, noise, depth, notes):
    total = sum(n for _, n in log)
    counts = {a: sum(n for t, n in log if t.count(a) == 1) for a in alphabet}
    for activity in sorted(alphabet, key=lambda a: (-counts[a], a)):
        if counts[activity] == total:
            rest = _merge((tuple(x for x in t if x != activity), n) for t, n in log)
            mark = len(notes)
            other = _discover(rest, noise, depth + 1, notes)
            return _parallel([_leaf(activity), other], [{activity}, set(alphabet) - {activity}], log, noise,
                             notes, mark)
    return _flower(alphabet, log, notes)


def _flower(alphabet, log, notes, reason="No structure was found"):
    notes.append(reason + " among " + ", ".join(alphabet[:8]) + ("…" if len(alphabet) > 8 else "")
                 + "; they are drawn as a loop where they can occur in any order.")
    total = sum(n for _, n in log)
    cases = [sum(n * t.count(a) for t, n in log) for a in alphabet]
    return {"op": "loop", "unstructured": True, "cases": total,
            "repeats": sum(n * (len(t) - 1) for t, n in log),
            "children": [{"op": "xor", "children": [_leaf(a) for a in alphabet], "cases": cases, "unit": "events"}, TAU]}


def _has_choice(node) -> bool:
    return node["op"] in _CHOICE_OPS or any(_has_choice(c) for c in node.get("children", []))


def _first_activity(node) -> str | None:
    if node["op"] == "activity":
        return node["activity"]
    for child in node.get("children", []):
        found = _first_activity(child)
        if found:
            return found
    return None


# --- replay ------------------------------------------------------------------

def _alphabet(node, cache) -> frozenset:
    key = id(node)
    if key not in cache:
        cache[key] = (frozenset([node["activity"]]) if node["op"] == "activity" else
                      frozenset().union(*(_alphabet(c, cache) for c in node.get("children", []))))
    return cache[key]


def _accepts(tree, trace) -> bool:
    alphabets: dict[int, frozenset] = {}
    memo: dict[tuple[int, tuple, int], frozenset] = {}

    def ends(node, seq, i) -> frozenset:
        key = (id(node), seq, i)
        if key in memo:
            return memo[key]
        op = node["op"]
        if op == "tau":
            out = frozenset([i])
        elif op == "activity":
            out = frozenset([i + 1]) if i < len(seq) and seq[i] == node["activity"] else frozenset()
        elif op == "xor":
            out = frozenset().union(*(ends(c, seq, i) for c in node["children"]))
        elif op == "seq":
            out = frozenset([i])
            for child in node["children"]:
                out = frozenset().union(*(ends(child, seq, p) for p in out)) if out else out
        elif op == "loop":
            body, redo = node["children"]
            out = set(ends(body, seq, i))
            pending = list(out)
            while pending:
                for q in ends(redo, seq, pending.pop()):
                    for r in ends(body, seq, q):
                        if r not in out:
                            out.add(r)
                            pending.append(r)
            out = frozenset(out)
        else:   # parallel: disjoint alphabets, so each branch replays its own projection
            letters = _alphabet(node, alphabets)
            stop = i
            while stop < len(seq) and seq[stop] in letters:
                stop += 1
            found = set()
            for j in range(i, stop + 1):
                window = seq[i:j]
                if all(len(p) in ends(c, p, 0) for c in node["children"]
                       for p in [tuple(a for a in window if a in _alphabet(c, alphabets))]):
                    found.add(j)
            out = frozenset(found)
        memo[key] = out
        return out
    return len(trace) in ends(tree, trace, 0)


# --- catalog model -----------------------------------------------------------

def _label(activity: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9 _-]+", " ", activity).strip()
    text = re.sub(r" {2,}", " ", text)
    if text and not text[0].isalpha():
        text = "Step " + text
    text = text[:64].rstrip()
    return text if _LABEL.match(text) else fallback


def _pct(n, total):
    return f"{100 * n / total:.0f}%" if total else "n/a"


class _Graph:
    def __init__(self):
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.exit_rule: dict[str, str] = {}
        self.kinds: Counter = Counter()

    def node(self, kind, label=None, prefix="act"):
        self.kinds[prefix] += 1
        node_id = f"{prefix}_{self.kinds[prefix]}"
        self.nodes.append({"id": node_id, "label": label or node_id.replace("_", " ").capitalize(),
                           "kind": kind, "implementation": "unmapped"})
        return node_id

    def rule(self, text):
        rule_id = f"rule_{len(self.rules) + 1}"
        self.rules.append({"id": rule_id, "text": text, "user_confirmed": False})
        return rule_id

    def link(self, source, target, rule_id=None):
        rule_id = rule_id or self.exit_rule.pop(source, None)
        self.edges.append({"source": source, "target": target, "rule_id": rule_id})

    def emit(self, node):
        op = node["op"]
        if op == "tau":
            return None
        if op == "activity":
            node_id = self.node("activity", prefix="act")
            self.nodes[-1]["label"] = _label(node["activity"], "Activity " + node_id.split("_")[1])
            return node_id, node_id
        if op == "seq":
            frags = [f for f in (self.emit(c) for c in node["children"]) if f]
            for left, right in zip(frags, frags[1:]):
                self.link(left[1], right[0])
            return (frags[0][0], frags[-1][1]) if frags else None
        if op == "xor":
            total = sum(node["cases"])
            branches = [(self.emit(c), n, c) for c, n in zip(node["children"], node["cases"])]
            skipped = sum(n for f, n, _ in branches if f is None)
            taken = [(f, n, c) for f, n, c in branches if f]
            if not taken:
                return None
            if len(taken) == 1 and not any(f is None for f, _, _ in branches):
                return taken[0][0]
            decision = self.node("decision", prefix="decision")
            merge = self.node("routing", prefix="merge")
            unit = node.get("unit", "cases")
            for frag, n, child in taken:
                self.link(decision, frag[0], self.rule(
                    f"Observed choice, not a confirmed rule: {n} of {total} {unit} ({_pct(n, total)}) continued "
                    f"with {_first_activity(child) or 'this branch'}. State the real business condition."))
                self.link(frag[1], merge)
            if any(f is None for f, _, _ in branches):
                self.link(decision, merge, self.rule(
                    f"Observed choice, not a confirmed rule: {skipped} of {total} cases ({_pct(skipped, total)}) "
                    "skipped this step. State the real business condition."))
            return decision, merge
        if op == "loop":
            entry = self.node("routing", prefix="loop")
            body = self.emit(node["children"][0])
            check = self.node("decision", prefix="repeat")
            if body:
                self.link(entry, body[0])
                self.link(body[1], check)
            else:
                self.link(entry, check)
            redo = self.emit(node["children"][1])
            repeat_rule = self.rule(
                f"Observed repetition, not a confirmed rule: {node['repeats']} repeats across {node['cases']} "
                "cases. State when work really goes round again.")
            if redo:
                self.link(check, redo[0], repeat_rule)
                self.link(redo[1], entry)
            else:
                self.link(check, entry, repeat_rule)
            self.exit_rule[check] = self.rule(
                "Observed exit, not a confirmed rule: every case leaves this loop eventually. "
                "State when work is really done here.")
            return entry, check
        split = self.node("parallel_split", prefix="split")
        join = self.node("parallel_join", prefix="join")
        direct = False
        for child in node["children"]:
            frag = self.emit(child)
            if frag:
                self.link(split, frag[0])
                self.link(frag[1], join)
            elif not direct:
                direct = True
                self.link(split, join)
        return split, join


def _to_model(tree, top_variant, notes) -> dict[str, Any]:
    graph = _Graph()
    start = graph.node("start", "Start", prefix="start")
    frag = graph.emit(tree)
    end_id = graph.node("end", "End", prefix="end")
    graph.link(start, frag[0])
    graph.link(frag[1], end_id)
    sizes = (len(graph.nodes), len(graph.edges), len(graph.rules))
    if sizes[0] > MAX_MODEL_NODES or sizes[1] > 100 or sizes[2] > 40:
        raise workspace.EventLogWorkspaceError(
            f"The discovered draft needs {sizes[0]} nodes, {sizes[1]} transitions and {sizes[2]} rules; a catalog "
            "process allows 40, 100 and 40. Lower the activity share or raise the noise filter.")
    path, count = top_variant
    baseline = [str(a)[:240] for a in path[:40]] or ["(empty case)"]
    draft = {
        "format": "openworkgraph.process-model.v1", "goal": "Discovered draft awaiting review", "owner_label": "",
        "baseline_path": baseline, "baseline_source": "selected_csv_variant",
        "baseline_coverage": "complete_supplied_path" if len(path) <= 40 else "bounded_candidate_sequence",
        "baseline_support_unit": "declared_cases", "supporting_case_count": min(int(count), 1_000_000),
        "activities": graph.nodes, "transitions": graph.edges, "business_rules": graph.rules,
        "entry_activity_id": start, "terminal_activity_ids": [end_id], "outcome_expectations": [],
        "manual_review_conditions": [], "opportunity_scenario": None,
        "observed_path_is_policy": False, "execution_authorized": False, "outcomes_verified": False,
    }
    try:
        model = ProcessModel.model_validate(draft).model_dump(mode="json")
    except ValidationError as exc:
        raise workspace.EventLogWorkspaceError(
            "The discovered structure cannot be expressed as a catalog process; raise the noise filter "
            "or lower the activity share (" + exc.errors()[0]["msg"] + ")") from None
    if len(path) > 40:
        notes.append("The most frequent variant is longer than 40 steps; its first 40 are kept as the AS-IS baseline.")
    model["goal"] = ""   # the reviewer states the business goal before saving
    return model


__all__ = ["discover"]
