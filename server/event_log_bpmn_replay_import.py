"""Strict BPMN control-flow interchange for event-log Petri replay.

Supports one independent process, plain start/end, opaque named tasks,
unconditional exclusive gateways, paired acyclic parallel regions and
plain embedded subprocess scopes.
A sequence flow becomes a place. Gateway transitions reproduce token flow;
no task implementation, condition, message or timer is executed.
Reject unsupported semantics instead of approximating them.
"""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import re
from xml.etree import ElementTree as ET

from . import event_log_pnml as pnml
from .event_log_workspace import EventLogWorkspaceError

MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 250
MAX_FLOWS = 600
MAX_SCOPE_DEPTH = 12
MAX_PROCESSES = 40
MAX_EXPANDED_NODES = 500
MAX_EXPANDED_FLOWS = 1200
NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
DI = "http://www.omg.org/spec/BPMN/20100524/DI"
TASKS = {"task", "userTask", "manualTask", "serviceTask", "scriptTask", "businessRuleTask"}
GATEWAYS = {"exclusiveGateway", "parallelGateway"}
NODES = TASKS | GATEWAYS | {"startEvent", "endEvent", "subProcess", "callActivity"}
FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _fail(message):
    raise EventLogWorkspaceError(message)


def _tag(name):
    return "{" + NS + "}" + name


def _kind(element):
    return element.tag[len(NS)+2:] if element.tag.startswith("{" + NS + "}") else ""


def _attrs(element, allowed):
    unknown = set(element.attrib) - set(allowed)
    if unknown:
        _fail("unsupported attributes on " + (_kind(element) or "element") + ": " + ", ".join(sorted(unknown))[:240])


def _id(element):
    value = element.get("id")
    if not value or len(value) > 200 or any(c.isspace() or ord(c) < 32 for c in value):
        _fail("BPMN IDs must be nonempty, at most 200 characters and contain no whitespace")
    return value


def _reachable(start, adjacency, stop=None):
    seen = set()
    pending = [start]
    while pending:
        node = pending.pop()
        if node == stop or node in seen:
            continue
        seen.add(node)
        pending.extend(adjacency[node])
    return seen


def _acyclic(nodes, successors, predecessors):
    degree = {n: len(predecessors[n]) for n in nodes}
    queue = deque(n for n, d in degree.items() if d == 0)
    count = 0
    while queue:
        node = queue.popleft()
        count += 1
        for nxt in successors[node]:
            degree[nxt] -= 1
            if degree[nxt] == 0:
                queue.append(nxt)
    return count == len(nodes)


def _parallel_pairs(nodes, successors, predecessors, end):
    splits = [n for n, k in nodes.items() if k == "parallelGateway" and len(successors[n]) > 1]
    joins = [n for n, k in nodes.items() if k == "parallelGateway" and len(predecessors[n]) > 1]
    if not splits and not joins:
        return {}
    if not _acyclic(nodes, successors, predecessors):
        _fail("parallel regions with cycles are unsupported for direct BPMN replay")
    pairs = {}
    for split in splits:
        candidates = []
        branches = successors[split]
        for join in joins:
            if split == join or len(predecessors[join]) != len(branches):
                continue
            regions = [_reachable(branch, successors, stop=join) for branch in branches]
            # Distinct isolated regions, each entering the join exactly once;
            # no outside incoming edge or alternative exit. Empty branch is
            # permitted when its sequence flow goes directly to the join.
            combined = set()
            valid = True
            entrances = set()
            for branch, region in zip(branches, regions):
                if region & combined or end in region or split in region:
                    valid = False
                    break
                combined |= region
                if branch == join:
                    entrances.add(split)
                    continue
                incoming = {(p, n) for n in region for p in predecessors[n] if p not in region}
                outgoing = {(n, q) for n in region for q in successors[n] if q not in region}
                if incoming != {(split, branch)} or len(outgoing) != 1:
                    valid = False
                    break
                source, target = next(iter(outgoing))
                if target != join:
                    valid = False
                    break
                entrances.add(source)
            if valid and entrances == set(predecessors[join]):
                candidates.append(join)
        if len(candidates) != 1:
            _fail("parallel split has no unique isolated matching join: " + split)
        pairs[split] = candidates[0]
    if len(set(pairs.values())) != len(pairs) or set(pairs.values()) != set(joins):
        _fail("every parallel join must belong to exactly one paired region")
    return pairs


def to_pnml(data: bytes, process_id: str | None = None):
    if len(data) > MAX_BYTES:
        _fail("BPMN file is larger than 2 MB")
    if b"\x00" in data or FORBIDDEN.search(data):
        _fail("BPMN requires ASCII-compatible XML without DOCTYPE or entities")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        _fail("BPMN file is not well-formed XML")
    if root.tag != _tag("definitions"):
        _fail("expected BPMN 2.0 definitions")
    _attrs(root, {"id", "name", "targetNamespace", "exporter", "exporterVersion", "expressionLanguage", "typeLanguage"})
    processes = []
    for child in root:
        if child.tag == _tag("process"):
            processes.append(child)
        elif child.tag != "{" + DI + "}BPMNDiagram" and child.tag != _tag("documentation"):
            _fail("unsupported BPMN definitions element: " + child.tag.rsplit("}", 1)[-1])
    if not 1 <= len(processes) <= MAX_PROCESSES:
        _fail("direct replay requires 1–40 independent process definitions")
    definitions = {}
    for process in processes:
        _attrs(process, {"id", "name", "isExecutable", "processType", "isClosed"})
        pid = _id(process)
        if pid in definitions:
            _fail("duplicate BPMN process identifier: " + pid)
        if process.get("processType") not in (None, "None", "Private") or process.get("isClosed") not in (None, "false", "0"):
            _fail("only independent private process control flow is supported")
        definitions[pid] = process
    if process_id is not None and (not isinstance(process_id, str) or process_id not in definitions):
        _fail("selected BPMN process is not present")
    if process_id is None:
        if len(definitions) != 1:
            _fail("multiple BPMN processes: explicitly select a process ID")
        process_id = next(iter(definitions))
    pid = process_id
    process = definitions[pid]
    context = {"ids": set(definitions), "nodes": 0, "flows": 0}
    scopes = {ident: _read_scope(element, context, depth=0)
              for ident, element in sorted(definitions.items())}
    # Validate every definition, including unused ones. Resolve only exact
    # unqualified local IDs; external QName bindings are not guessed.
    for ident in sorted(scopes):
        _expanded_size(scopes[ident], scopes, (ident,), 0, [0, 0])
    scope = scopes[pid]
    expanded = [0, 0]
    _expanded_size(scope, scopes, (pid,), 0, expanded)
    net = pnml._Net()
    source, sink = net.place(), net.place()
    evidence = {"node_transition_ids": {}, "flow_place_ids": {}, "parallel_pairs": {},
                "scope_by_node": {}, "scope_by_flow": {}, "subprocess_boundaries": {},
                "call_boundaries": {}, "node_instances": [], "flow_instances": [],
                "parallel_pair_instances": []}
    _emit_scope(net, scope, source, sink, evidence, scopes, ())
    result_net = {"places": net.places, "transitions": net.transitions, "arcs": net.arcs,
                  "source": source, "sink": sink}
    xml = pnml.to_pnml(result_net, (process.get("name") or pid)[:200])
    if len(xml.encode("utf-8")) > MAX_BYTES:
        _fail("converted PNML exceeds the replay byte limit")
    return {"format": "openworkgraph.bpmn_replay_conversion.v1", "pnml": xml,
            "bpmn_sha256": sha256(data).hexdigest(), "pnml_sha256": sha256(xml.encode()).hexdigest(),
            "process_id": pid, "available_process_ids": sorted(definitions),
            "nodes": expanded[0], "sequence_flows": expanded[1],
            "definition_nodes": context["nodes"], "definition_sequence_flows": context["flows"],
            "places": len(net.places), "transitions": len(net.transitions), **evidence,
            "caveat": "Only unconditional visible activity and routing token semantics are represented. "
                      "Plain embedded subprocess and local call wrappers are silent; their internal tasks remain visible. "
                      "No task code, data conditions, timing, resources, collaboration or execution authority is imported."}


def _read_scope(process, context, depth):
    if depth > MAX_SCOPE_DEPTH:
        _fail("BPMN embedded subprocess nesting exceeds 12 levels")
    nodes = {}
    labels = {}
    elements = {}
    flows = {}
    children = {}
    calls = {}
    for element in process:
        kind = _kind(element)
        if kind == "documentation" or (_kind(process) == "subProcess" and kind in {"incoming", "outgoing"}):
            continue
        if kind not in NODES and kind != "sequenceFlow":
            _fail("unsupported BPMN process element: " + element.tag.rsplit("}", 1)[-1])
        ident = _id(element)
        if ident in context["ids"]:
            _fail("duplicate BPMN identifier across scopes: " + ident)
        context["ids"].add(ident)
        if kind == "sequenceFlow":
            _attrs(element, {"id", "name", "sourceRef", "targetRef"})
            if any(c.tag != _tag("documentation") for c in element):
                _fail("conditional or extended sequence flows are unsupported")
            flows[ident] = (element.get("sourceRef"), element.get("targetRef"))
            context["flows"] += 1
            if context["flows"] > MAX_FLOWS:
                _fail("too many BPMN sequence flows across scopes")
        else:
            allowed = {"id", "name"}
            if kind in GATEWAYS:
                allowed.add("gatewayDirection")
            if kind in TASKS or kind in {"subProcess", "callActivity"}:
                allowed.add("isForCompensation")
            if kind == "subProcess":
                allowed.add("triggeredByEvent")
            if kind == "callActivity":
                allowed.add("calledElement")
            if kind == "startEvent":
                allowed.add("isInterrupting")
            _attrs(element, allowed)
            if element.get("isForCompensation") not in (None, "false", "0"):
                _fail("compensation tasks are unsupported")
            if element.get("isInterrupting") not in (None, "true", "1"):
                _fail("noninterrupting start events are unsupported")
            if kind == "subProcess":
                if element.get("triggeredByEvent") not in (None, "false", "0"):
                    _fail("event subprocesses are unsupported")
            else:
                for c in element:
                    if c.tag not in {_tag("incoming"), _tag("outgoing"), _tag("documentation")}:
                        _fail("unsupported task, gateway or event semantics: " + c.tag.rsplit("}", 1)[-1])
            if kind == "callActivity":
                ref = element.get("calledElement")
                if not ref or ":" in ref or len(ref) > 200 or any(c.isspace() for c in ref):
                    _fail("call activity requires an unqualified local process ID: " + ident)
                calls[ident] = ref
            nodes[ident] = kind
            elements[ident] = element
            label = element.get("name", "")
            if kind in TASKS and (not label.strip() or label != label.strip() or len(label) > 200 or any(ord(c) < 32 for c in label)):
                _fail("every task needs a nonempty name of at most 200 characters without surrounding whitespace")
            labels[ident] = label if kind in TASKS else None
            context["nodes"] += 1
            if context["nodes"] > MAX_NODES:
                _fail("too many BPMN flow nodes across scopes")
            if kind == "subProcess":
                children[ident] = _read_scope(element, context, depth + 1)
    starts = [n for n, k in nodes.items() if k == "startEvent"]
    ends = [n for n, k in nodes.items() if k == "endEvent"]
    if len(starts) != 1 or len(ends) != 1:
        _fail("exactly one plain start and one plain end event are required")
    start, end = starts[0], ends[0]
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    successors = defaultdict(list)
    predecessors = defaultdict(list)
    seen_edges = set()
    for fid, (source, target) in flows.items():
        if source not in nodes or target not in nodes or source == target:
            _fail("sequence flow references an unknown node or itself")
        if (source, target) in seen_edges:
            _fail("parallel duplicate sequence flows are unsupported")
        seen_edges.add((source, target))
        incoming[target].append(fid)
        outgoing[source].append(fid)
        successors[source].append(target)
        predecessors[target].append(source)
    for ident, kind in nodes.items():
        n_in, n_out = len(incoming[ident]), len(outgoing[ident])
        if kind == "startEvent":
            ok = (n_in, n_out) == (0, 1)
        elif kind == "endEvent":
            ok = (n_in, n_out) == (1, 0)
        elif kind in TASKS or kind in {"subProcess", "callActivity"}:
            ok = (n_in, n_out) == (1, 1)
        elif kind == "parallelGateway":
            ok = (n_in == 1 and n_out >= 2) or (n_in >= 2 and n_out == 1)
        else:
            ok = n_in >= 1 and n_out >= 1 and (n_in > 1 or n_out > 1)
        if not ok:
            _fail("unsupported degree for " + kind + ": " + ident)
        direction = elements[ident].get("gatewayDirection")
        actual = "Mixed" if n_in > 1 and n_out > 1 else "Converging" if n_in > 1 else "Diverging"
        if direction not in (None, "Unspecified", actual):
            _fail("gateway direction disagrees with sequence flows: " + ident)
        for tag, expected in (("incoming", incoming[ident]), ("outgoing", outgoing[ident])):
            declared = [(c.text or "").strip() for c in elements[ident] if c.tag == _tag(tag)]
            if declared and (len(declared) != len(expected) or set(declared) != set(expected)):
                _fail("declared " + tag + " references disagree with sequence flows: " + ident)
    if _reachable(start, successors) != set(nodes) or _reachable(end, predecessors) != set(nodes):
        _fail("every BPMN node must lie on a start-to-end graph path")
    pairs = _parallel_pairs(nodes, successors, predecessors, end)
    return {"id": _id(process), "nodes": nodes, "labels": labels, "flows": flows,
            "incoming": incoming, "outgoing": outgoing, "pairs": pairs, "children": children, "calls": calls}


def _expanded_size(scope, scopes, ancestors, depth, totals):
    if depth > MAX_SCOPE_DEPTH:
        _fail("expanded BPMN subprocess/call nesting exceeds 12 levels")
    totals[0] += len(scope["nodes"])
    totals[1] += len(scope["flows"])
    if totals[0] > MAX_EXPANDED_NODES or totals[1] > MAX_EXPANDED_FLOWS:
        _fail("expanded BPMN call graph exceeds node or flow budget")
    for ident in sorted(scope["children"]):
        _expanded_size(scope["children"][ident], scopes, ancestors, depth + 1, totals)
    for ident, ref in sorted(scope["calls"].items()):
        if ref not in scopes:
            _fail("unresolved local calledElement: " + ref)
        if ref in ancestors:
            _fail("recursive BPMN call activity: " + " -> ".join((*ancestors, ref)))
        _expanded_size(scopes[ref], scopes, (*ancestors, ref), depth + 1, totals)


def _instance_key(path, ident):
    # Source IDs can contain arbitrary punctuation; length-prefix each call
    # segment to avoid aliasing between distinct expansion paths.
    return ident if not path else "@" + "".join(f"{len(x)}:{x}" for x in (*path, ident))


def _emit_scope(net, scope, source, sink, evidence, scopes, path):
    nodes, labels = scope["nodes"], scope["labels"]
    incoming, outgoing = scope["incoming"], scope["outgoing"]
    flow_places = {fid: net.place() for fid in sorted(scope["flows"])}
    for fid, place in flow_places.items():
        evidence["flow_place_ids"].setdefault(fid, place)
        evidence["scope_by_flow"][fid] = scope["id"]
        evidence["flow_instances"].append({"source_id": fid, "scope_id": scope["id"],
                                           "call_path": list(path), "place_id": place})
    for split, join in sorted(scope["pairs"].items()):
        evidence["parallel_pairs"][split] = join
        evidence["parallel_pair_instances"].append({"split_id": split, "join_id": join,
                                                     "call_path": list(path)})
    for ident in sorted(nodes):
        kind = nodes[ident]
        inputs = [flow_places[f] for f in sorted(incoming[ident])]
        outputs = [flow_places[f] for f in sorted(outgoing[ident])]
        if kind == "startEvent":
            tids = [net.transition(None, source, outputs[0])]
        elif kind == "endEvent":
            tids = [net.transition(None, inputs[0], sink)]
        elif kind in TASKS:
            tids = [net.transition(labels[ident], inputs[0], outputs[0])]
        elif kind == "subProcess":
            entry, exit = net.place(), net.place()
            enter = net.transition(None, inputs[0], entry)
            _emit_scope(net, scope["children"][ident], entry, exit, evidence, scopes, path)
            leave = net.transition(None, exit, outputs[0])
            tids = [enter, leave]
            evidence["subprocess_boundaries"].setdefault(ident, {"entry_place_id": entry, "exit_place_id": exit,
                                                        "entry_transition_id": enter, "exit_transition_id": leave})
        elif kind == "callActivity":
            ref = scope["calls"][ident]
            entry, exit = net.place(), net.place()
            enter = net.transition(None, inputs[0], entry)
            _emit_scope(net, scopes[ref], entry, exit, evidence, scopes, (*path, ident))
            leave = net.transition(None, exit, outputs[0])
            tids = [enter, leave]
            evidence["call_boundaries"][_instance_key(path, ident)] = {
                "source_id": ident, "called_process_id": ref, "call_path": list(path),
                "entry_place_id": entry, "exit_place_id": exit,
                "entry_transition_id": enter, "exit_transition_id": leave}
        elif kind == "exclusiveGateway":
            tids = [net.transition(None, a, b) for a in inputs for b in outputs]
        else:
            tids = [net.silent_between(inputs, outputs)]
        evidence["node_transition_ids"].setdefault(ident, []).extend(tids)
        evidence["scope_by_node"][ident] = scope["id"]
        evidence["node_instances"].append({"source_id": ident, "scope_id": scope["id"],
                                           "call_path": list(path), "transition_ids": tids})
