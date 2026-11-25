"""Bounded reachability analysis of ordinary, explicitly marked P/T workflow nets.

This is separate from heuristic token replay: no event log or activity labels
are used in deciding which transitions can fire. A complete finite state graph
can establish classical workflow-net soundness; an incomplete graph cannot.
"""
from __future__ import annotations

from collections import deque
from hashlib import sha256
from xml.etree import ElementTree as ET

from . import event_log_petri_replay as replay
from .event_log_workspace import EventLogWorkspaceError

MAX_PLACES = 200
MAX_TRANSITIONS = 300
MAX_STATES = 5000
MAX_TOKENS = 256
MAX_CHECKS = 300_000
MAX_WITNESS = 100
FORMAT = "openworkgraph.petri_soundness.v1"


def _strict_net(data):
    """Refuse constructs whose semantics the ordinary replay parser does not model."""
    if len(data) > replay.MAX_BYTES:
        raise EventLogWorkspaceError("the Petri net file is larger than 2 MB")
    if b"\x00" in data or replay._FORBIDDEN.search(data):
        raise EventLogWorkspaceError("use ASCII-compatible XML without DOCTYPE or entity declarations")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise EventLogWorkspaceError("the file is not well-formed XML") from None
    local = replay._local
    nets = [e for e in root.iter() if local(e.tag) == "net"]
    if len(nets) != 1:
        raise EventLogWorkspaceError("soundness requires exactly one net")
    net = nets[0]
    allowed_types = {
        "http://www.pnml.org/version-2009/grammar/pnmlcoremodel",
        "http://www.pnml.org/version-2009/grammar/ptnet",
    }
    if net.get("type") not in allowed_types:
        raise EventLogWorkspaceError("soundness supports ordinary PNML P/T nets only")
    # Whitelist direct semantic children; annotation subtrees are not traversed
    # by this validator and must not hide objects consumed by the replay parser.
    allowed = {
        "net": {"name", "page", "place", "transition", "arc", "finalmarkings", "toolspecific", "graphics"},
        "page": {"name", "page", "place", "transition", "arc", "toolspecific", "graphics"},
        "place": {"name", "initialMarking", "graphics", "toolspecific"},
        "transition": {"name", "graphics", "toolspecific"},
        "arc": {"name", "inscription", "graphics", "toolspecific"},
        "finalmarkings": {"marking"},
        "marking": {"place"},
        "name": {"text", "graphics"},
        "initialMarking": {"text", "graphics"},
        "inscription": {"text", "graphics"},
    }
    ids = set()
    validated = set()
    finals = []

    def walk(element, parent=None, depth=0):
        if depth > 40:
            raise EventLogWorkspaceError("PNML nesting is too deep")
        kind = local(element.tag)
        validated.add(element)
        if kind in {"name", "graphics", "toolspecific", "initialMarking", "inscription"}:
            if kind in {"graphics", "toolspecific"}:
                return
        if kind in {"place", "transition", "arc"} and parent != "marking":
            ident = element.get("id")
            if not ident or len(ident) > 200 or ident in ids:
                raise EventLogWorkspaceError("net nodes and arcs need distinct IDs of at most 200 characters")
            ids.add(ident)
            # No custom arc semantics, capacities or guards via attributes.
            permitted = {"id", "source", "target"} if kind == "arc" else {"id"}
            if set(element.attrib) - permitted:
                raise EventLogWorkspaceError("unsupported net node or arc attributes")
            if kind == "arc":
                inscription = replay._child(element, "inscription")
                if inscription is not None and replay._count(replay._text(inscription), "arc weight", 0) < 1:
                    raise EventLogWorkspaceError("arc weights must be positive")
        if kind == "finalmarkings":
            finals.append(element)
        if kind == "place" and parent == "marking":
            if set(element.attrib) != {"idref"} or not element.get("idref"):
                raise EventLogWorkspaceError("invalid final marking reference")
            choices = {"text"}
        else:
            choices = allowed.get(kind, set())
        counts = {}
        for child in element:
            tag = local(child.tag)
            if tag not in choices:
                raise EventLogWorkspaceError(f"unsupported PNML element <{tag}> in <{kind}>")
            counts[tag] = counts.get(tag, 0) + 1
            walk(child, kind, depth + 1)
        for tag in ("name", "initialMarking", "inscription", "text"):
            if counts.get(tag, 0) > 1:
                raise EventLogWorkspaceError("duplicate PNML annotation")
    walk(net)
    for e in net.iter():
        if local(e.tag) in {"place", "transition", "arc", "finalmarkings"} and e not in validated:
            raise EventLogWorkspaceError("unexpected semantic element inside PNML annotation")
    if len(finals) != 1 or len(finals[0]) != 1:
        raise EventLogWorkspaceError("soundness requires one explicit final marking")
    refs = [e.get("idref") for e in finals[0][0]]
    if len(set(refs)) != len(refs):
        raise EventLogWorkspaceError("duplicate final marking place")
    parsed = replay.parse_pnml(data)
    if parsed["final_guessed"]:
        raise EventLogWorkspaceError("soundness requires a nonempty explicit final marking")
    return parsed


def analyze(data: bytes) -> dict:
    net = _strict_net(data)
    places = sorted(net["places"])
    transitions = sorted(net["transitions"])
    if len(places) > MAX_PLACES or len(transitions) > MAX_TRANSITIONS:
        raise EventLogWorkspaceError(f"soundness supports at most {MAX_PLACES} places and {MAX_TRANSITIONS} transitions")
    pos = {p: i for i, p in enumerate(places)}
    initial = tuple(net["initial"][p] for p in places)
    final = tuple(net["final"][p] for p in places)
    if max(initial + final) > MAX_TOKENS:
        raise EventLogWorkspaceError("initial or final marking exceeds the token limit")
    pre = [[(pos[p], n) for p, n in net["pre"][t].items()] for t in transitions]
    post = [[(pos[p], n) for p, n in net["post"][t].items()] for t in transitions]
    incoming = {p for t in transitions for p in net["post"][t]}
    outgoing = {p for t in transitions for p in net["pre"][t]}
    sources = set(places) - incoming
    sinks = set(places) - outgoing
    forward = {n: set() for n in places + transitions}
    reverse = {n: set() for n in forward}
    for t in transitions:
        for a, b in [(p, t) for p in net["pre"][t]] + [(t, p) for p in net["post"][t]]:
            forward[a].add(b)
            reverse[b].add(a)

    def closure(graph, roots):
        seen = set(roots)
        todo = list(roots)
        while todo:
            for v in graph[todo.pop()] - seen:
                seen.add(v)
                todo.append(v)
        return seen

    structural = (len(sources) == len(sinks) == 1
                  and dict(net["initial"]) == {next(iter(sources)): 1}
                  and dict(net["final"]) == {next(iter(sinks)): 1}
                  and closure(forward, sources) == set(forward)
                  and closure(reverse, sinks) == set(reverse))
    # Reachability graph stores marking IDs, not copies of every edge marking.
    markings = [initial]
    indices = {initial: 0}
    parents = [None]
    predecessors = [set()]
    fired = set()
    checks = 0
    complete = True
    limit = None
    deadlock = improper = unsafe = None
    final_id = None
    cursor = 0
    while cursor < len(markings):
        marking = markings[cursor]
        if marking == final:
            final_id = cursor
        if unsafe is None and max(marking) > 1:
            unsafe = cursor
        if improper is None and marking != final and all(a >= b for a, b in zip(marking, final)):
            improper = cursor
        enabled = False
        for i, t in enumerate(transitions):
            if checks >= MAX_CHECKS:
                complete, limit = False, "transition_check_limit"
                break
            checks += 1
            if not all(marking[p] >= n for p, n in pre[i]):
                continue
            enabled = True
            fired.add(t)
            if marking == final and improper is None:
                improper = cursor
            nxt = list(marking)
            for p, n in pre[i]:
                nxt[p] -= n
            for p, n in post[i]:
                nxt[p] += n
            if max(nxt) > MAX_TOKENS:
                complete, limit = False, "token_limit"
                break
            key = tuple(nxt)
            target = indices.get(key)
            if target is None:
                if len(markings) >= MAX_STATES:
                    complete, limit = False, "state_limit"
                    break
                target = len(markings)
                indices[key] = target
                markings.append(key)
                parents.append((cursor, t))
                predecessors.append(set())
            predecessors[target].add(cursor)
        if not complete:
            break
        if not enabled and marking != final and deadlock is None:
            deadlock = cursor
        cursor += 1

    # A final marking may have been discovered just before a limit.
    final_id = indices.get(final)
    noncompleting = None
    unable_count = None
    if complete:
        can_complete = set() if final_id is None else {final_id}
        pending = list(can_complete)
        while pending:
            for p in predecessors[pending.pop()] - can_complete:
                can_complete.add(p)
                pending.append(p)
        unable_count = len(markings) - len(can_complete)
        noncompleting = next((i for i in range(len(markings)) if i not in can_complete), None)

    def witness(index):
        if index is None:
            return None
        ids = []
        current = index
        while parents[current] is not None:
            current, t = parents[current]
            ids.append(t)
        ids.reverse()
        return {"transition_ids": ids[:MAX_WITNESS], "steps": len(ids),
                "truncated": len(ids) > MAX_WITNESS,
                "marking": [{"place_id": p, "tokens": n} for p, n in zip(places, markings[index]) if n]}

    no_dead = (len(fired) == len(transitions)) if complete else (True if len(fired) == len(transitions) else None)
    option_to_complete = (unable_count == 0) if complete else (False if deadlock is not None else None)
    proper_completion = False if improper is not None else (True if complete else None)
    one_safe = False if unsafe is not None else (True if complete else None)
    # A structural workflow net, option to complete, proper completion, no dead transitions.
    properties = [structural, option_to_complete, proper_completion, no_dead]
    sound = False if False in properties else (True if complete and all(properties) else None)
    return {"format": FORMAT, "pnml_sha256": sha256(data).hexdigest(),
            "complete": complete, "limit": limit, "reachable_markings": len(markings),
            "expanded_markings": cursor, "transition_checks": checks,
            "workflow_net_structure": structural, "source_places": sorted(sources), "sink_places": sorted(sinks),
            "sound": sound, "option_to_complete": option_to_complete, "proper_completion": proper_completion,
            "no_dead_transitions": no_dead, "one_safe": one_safe,
            "final_reachable": True if final_id is not None else (False if complete else None),
            "noncompleting_markings": unable_count,
            "dead_transition_ids": sorted(set(transitions) - fired) if complete else None,
            "not_yet_fired_transition_ids": sorted(set(transitions) - fired),
            "witnesses": {"deadlock": witness(deadlock), "improper_completion": witness(improper),
                          "unsafe_marking": witness(unsafe), "cannot_complete": witness(noncompleting)},
            "limits": {"states": MAX_STATES, "tokens_per_place": MAX_TOKENS, "transition_checks": MAX_CHECKS},
            "caveat": "Exact only for the supported ordinary P/T semantics; a limit is not evidence of unboundedness or soundness."}
