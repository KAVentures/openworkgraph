"""Export a discovered process model as a Petri net in PNML (ISO/IEC 15909-2).

ProM, PM4Py, Apromore, WoPeD and most academic process-mining tools exchange models as Petri nets
in PNML. The discovery in ``event_log_model_discovery`` builds a process tree; this module turns it
into the equivalent workflow net with the usual block construction:

* an activity is a visible transition; a skip (tau) is a silent transition;
* a sequence chains its parts through new places;
* a choice (xor) puts every branch between the same two places;
* a parallel block (and) adds a silent split and a silent join around one place pair per branch;
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


__all__ = ["tree_to_net", "to_pnml", "discovered_pnml"]
