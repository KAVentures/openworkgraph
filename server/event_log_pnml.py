"""Export a discovered process model as a Petri net in PNML (ISO/IEC 15909-2).

ProM, PM4Py, Apromore, WoPeD and most academic process-mining tools exchange models as Petri nets
in PNML. The discovery in ``event_log_model_discovery`` builds a process tree; this module turns it
into the equivalent workflow net with the usual block construction:

* an activity is a visible transition; a skip (tau) is a silent transition;
* a sequence chains its parts through new places;
* a choice (xor) puts every branch between the same two places;
* a parallel block (and) adds a silent split and a silent join around one place pair per branch;
* an inclusive choice (or) executes a nonempty subset of branches in parallel;
* an interleaving block executes every child once, in arbitrary order but never concurrently;
* a loop (body, redo) runs the body, then either leaves through a silent transition or runs the
  redo part and the body again.

The net has one source place (initial marking: one token) and one sink place (final marking: one
token), declared in the file's ``finalmarkings`` element as PM4Py and ProM read it. Silent
transitions carry the PM4Py/ProM ``invisible`` tool-specific flag. Identifiers are generated, so
activity names never become XML identifiers.
"""
from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from .event_log_workspace import EventLogWorkspaceError

PNML_NET_TYPE = "http://www.pnml.org/version-2009/grammar/pnmlcoremodel"
MAX_NODES = 5_000


class _Net:
    def __init__(self) -> None:
        self.places: list[str] = []
        self.transitions: list[tuple[str, str | None]] = []   # (id, label or None for silent)
        self.arcs: list[tuple[str, str]] = []

    def place(self) -> str:
        pid = f"p{len(self.places)}"
        self.places.append(pid)
        self._bound()
        return pid

    def transition(self, label: str | None, src: str, dst: str) -> str:
        tid = f"t{len(self.transitions)}"
        self.transitions.append((tid, label))
        self.arcs += [(src, tid), (tid, dst)]
        self._bound()
        return tid

    def silent_between(self, sources: list[str], targets: list[str]) -> str:
        tid = f"t{len(self.transitions)}"
        self.transitions.append((tid, None))
        self.arcs += [(s, tid) for s in sources] + [(tid, t) for t in targets]
        self._bound()
        return tid

    def _bound(self) -> None:
        if len(self.places) + len(self.transitions) > MAX_NODES:
            raise EventLogWorkspaceError(f"the Petri net would exceed {MAX_NODES} places and transitions")


def _emit(net: _Net, node: dict[str, Any], src: str, dst: str) -> None:
    op = node.get("op")
    if op == "activity":
        net.transition(str(node["activity"]), src, dst)
    elif op == "tau":
        net.transition(None, src, dst)
    elif op == "seq":
        children = node["children"]
        here = src
        for i, child in enumerate(children):
            nxt = dst if i == len(children) - 1 else net.place()
            _emit(net, child, here, nxt)
            here = nxt
    elif op == "xor":
        for child in node["children"]:
            _emit(net, child, src, dst)
    elif op == "and":
        starts = [net.place() for _ in node["children"]]
        ends = [net.place() for _ in node["children"]]
        net.silent_between([src], starts)
        for child, a, b in zip(node["children"], starts, ends):
            _emit(net, child, a, b)
        net.silent_between(ends, [dst])
    elif op == "or":
        # All branches start pending. A branch can be skipped or executed, but
        # only execution (including a tau child) can set the selected flag.
        # The flag persists until the final join, excluding the all-skip path.
        # Unlike a subset-per-transition construction this is linear in arity.
        children = node["children"]
        if not children:
            raise EventLogWorkspaceError("inclusive choice needs at least one child")
        none, some = net.place(), net.place()
        pending = [net.place() for _ in children]
        done = [net.place() for _ in children]
        net.silent_between([src], [none, *pending])
        for child, start, finish in zip(children, pending, done):
            completed = net.place()
            net.transition(None, start, finish)  # skip
            _emit(net, child, start, completed)
            net.silent_between([completed, none], [finish, some])
            net.silent_between([completed, some], [finish, some])
        net.silent_between([some, *done], [dst])
    elif op == "interleaved":
        # A single lock is held for the entire child, not just one activity.
        # Distinct pending and done tokens prevent repeated or omitted children.
        children = node["children"]
        if not children:
            raise EventLogWorkspaceError("interleaving needs at least one child")
        lock = net.place()
        pending = [net.place() for _ in children]
        done = [net.place() for _ in children]
        net.silent_between([src], [lock, *pending])
        for child, ready, finish in zip(children, pending, done):
            start, completed = net.place(), net.place()
            net.silent_between([lock, ready], [start])
            _emit(net, child, start, completed)
            net.silent_between([completed], [lock, finish])
        net.silent_between([lock, *done], [dst])
    elif op == "loop":
        body, redo = node["children"]
        enter, leave = net.place(), net.place()
        net.transition(None, src, enter)
        _emit(net, body, enter, leave)
        _emit(net, redo, leave, enter)
        net.transition(None, leave, dst)
    else:
        raise EventLogWorkspaceError(f"unknown process tree operator: {op!r}")


def tree_to_net(tree: dict[str, Any]) -> dict[str, Any]:
    """The workflow net of a process tree: places, transitions (label ``None`` = silent) and arcs."""
    net = _Net()
    source, sink = net.place(), net.place()
    _emit(net, tree, source, sink)
    return {"places": net.places, "transitions": net.transitions, "arcs": net.arcs,
            "source": source, "sink": sink}


def to_pnml(net: dict[str, Any], name: str) -> str:
    root = ET.Element("pnml")
    xml_net = ET.SubElement(root, "net", id="net1", type=PNML_NET_TYPE)
    ET.SubElement(ET.SubElement(xml_net, "name"), "text").text = name
    page = ET.SubElement(xml_net, "page", id="n0")
    for pid in net["places"]:
        place = ET.SubElement(page, "place", id=pid)
        ET.SubElement(ET.SubElement(place, "name"), "text").text = {
            net["source"]: "source", net["sink"]: "sink"}.get(pid, pid)
        if pid == net["source"]:
            ET.SubElement(ET.SubElement(place, "initialMarking"), "text").text = "1"
    for tid, label in net["transitions"]:
        transition = ET.SubElement(page, "transition", id=tid)
        ET.SubElement(ET.SubElement(transition, "name"), "text").text = label if label is not None else f"tau_{tid}"
        if label is None:
            ET.SubElement(transition, "toolspecific", tool="ProM", version="6.4",
                          activity="$invisible$", localNodeID=tid)
    for i, (a, b) in enumerate(net["arcs"]):
        ET.SubElement(page, "arc", id=f"a{i}", source=a, target=b)
    marking = ET.SubElement(ET.SubElement(xml_net, "finalmarkings"), "marking")
    ET.SubElement(ET.SubElement(marking, "place", idref=net["sink"]), "text").text = "1"
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def discovered_pnml(log_id: str, case_type: str | None = None, *, noise: float = 0.2, activities: float = 1.0,
                    cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    from . import event_log_model_discovery as discovery
    result = discovery.discover(log_id, case_type, noise=noise, activities=activities, cohort=cohort)
    net = tree_to_net(result["tree"])
    name = f"{result['log']['name']} ({result['case_notion']}) discovered net"[:200]
    meta = {"places": len(net["places"]), "transitions": len(net["transitions"]),
            "silent_transitions": sum(1 for _, label in net["transitions"] if label is None),
            "fitting_case_share": result["quality"]["fitting_case_share"],
            "process_model_sha256": result["process_model_sha256"]}
    return to_pnml(net, name), meta



MAX_VARIANT_RANK = 50


def variant_pnml(log_id: str, case_type: str | None = None, *, rank: int = 1,
                 cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """A sequence net of the ``rank``-th most common path (the "happy path" for rank 1), for checking every
    case against it with replay, alignments and deviation causes."""
    from . import event_log_workspace as workspace
    if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= MAX_VARIANT_RANK:
        raise workspace.EventLogWorkspaceError(f"rank must be 1 to {MAX_VARIANT_RANK}")

    def run(conn):
        rows = conn.execute("""SELECT path, count(*) AS cases FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [rank]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data["rows"]
    if len(rows) < rank:
        raise workspace.EventLogWorkspaceError(f"this selection has only {len(rows)} distinct paths")
    path, cases = rows[rank - 1]
    if len(path) > 500:
        raise workspace.EventLogWorkspaceError("that path is longer than 500 steps")
    net = tree_to_net({"op": "seq", "children": [{"op": "activity", "activity": a} for a in path]})
    name = f"{data['log']['name']} path #{rank}"[:200]
    meta = {"places": len(net["places"]), "transitions": len(net["transitions"]), "silent_transitions": 0,
            "path": list(path), "cases": cases, "share": round(cases / data["total"], 4) if data["total"] else None}
    return to_pnml(net, name), meta

__all__ = ["tree_to_net", "to_pnml", "discovered_pnml", "variant_pnml"]
