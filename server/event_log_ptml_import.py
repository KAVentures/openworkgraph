"""Read a PTML process tree (ProM, PM4Py) and turn it into a PNML workflow net.

PM4Py's and ProM's Inductive Miner produce process trees and save them as PTML
(``pm4py.write_ptml``). To check recorded cases against such a model, the tree is read here and
converted with the same block construction the discovered-model PNML export uses
(``event_log_pnml.tree_to_net``). The result goes through the ordinary Petri-net replay,
alignment and reference-net paths, so nothing downstream treats an imported tree specially.

Supported operators:

* ``manualTask``: an activity, named by its ``name`` attribute;
* ``automaticTask``: a silent step;
* ``sequence``, ``xor`` and ``and``;
* ``xorLoop``: with three children (ProM: do, redo, exit) or two (PM4Py: do, redo). The exit runs
  once after the loop.

Other operators (``or``, ``interleaved``, ``def``…) are refused with their name rather than
approximated. Child order follows the ``parentsNode`` elements. A node with two parents, a cycle,
a missing root or an unknown reference is refused. As with PNML input, XML with a DOCTYPE or
entity declaration is refused, and size and node counts are bounded.

``read_notation`` reads the same trees in PM4Py's printed notation (``print(tree)``), with the same
operators and the same refusals (``O`` and ``<>`` are not supported).
"""
from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

from .event_log_workspace import EventLogWorkspaceError

MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 5_000
_FORBIDDEN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_OPERATORS = {"sequence": "seq", "xor": "xor", "and": "and", "xorLoop": "loop"}
_LEAVES = {"manualTask", "automaticTask"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_tree(data: bytes) -> tuple[dict[str, Any], str]:
    """The process tree (``op``/``children``/``activity`` dicts) and the tree's name."""
    if len(data) > MAX_BYTES:
        raise EventLogWorkspaceError(f"the process tree file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if _FORBIDDEN.search(data):
        raise EventLogWorkspaceError("XML with a DOCTYPE or entity declaration is not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EventLogWorkspaceError(f"the file is not well-formed XML: {exc}") from None
    tree = root if _local(root.tag) == "processTree" else next(
        (e for e in root.iter() if _local(e.tag) == "processTree"), None)
    if tree is None:
        raise EventLogWorkspaceError("no <processTree> element was found; is this a PTML file?")
    nodes: dict[str, ET.Element] = {}
    children: dict[str, list[str]] = {}
    parent: dict[str, str] = {}
    for element in tree:
        kind = _local(element.tag)
        if kind == "parentsNode":
            source, target = element.get("sourceId"), element.get("targetId")
            if not source or not target:
                raise EventLogWorkspaceError("a parentsNode has no sourceId or targetId")
            if target in parent:
                raise EventLogWorkspaceError("a process tree node has more than one parent")
            parent[target] = source
            children.setdefault(source, []).append(target)
            continue
        node_id = element.get("id")
        if not node_id:
            raise EventLogWorkspaceError(f"a <{kind}> node has no id")
        if kind not in _LEAVES and kind not in _OPERATORS:
            raise EventLogWorkspaceError(f"the process tree operator <{kind}> is not supported")
        if node_id in nodes:
            raise EventLogWorkspaceError("two process tree nodes share an id")
        nodes[node_id] = element
        if len(nodes) > MAX_NODES:
            raise EventLogWorkspaceError(f"the process tree has more than {MAX_NODES} nodes")
    unknown = [n for n in list(parent) + list(children) if n not in nodes]
    if unknown:
        raise EventLogWorkspaceError("a parentsNode refers to a node that is not in the tree")
    top = tree.get("root") or next((n for n in nodes if n not in parent), None)
    if top not in nodes:
        raise EventLogWorkspaceError("the process tree has no root node")
    if top in parent:
        raise EventLogWorkspaceError("the process tree root has a parent")

    seen: set[str] = set()

    def build(node_id: str) -> dict[str, Any]:
        if node_id in seen:
            raise EventLogWorkspaceError("the process tree contains a cycle")
        seen.add(node_id)
        element = nodes[node_id]
        kind = _local(element.tag)
        kids = children.get(node_id, [])
        if kind in _LEAVES:
            if kids:
                raise EventLogWorkspaceError("a task node has children")
            if kind == "automaticTask":
                return {"op": "tau"}
            name = (element.get("name") or "").strip()
            if not name or len(name) > 200:
                raise EventLogWorkspaceError("an activity needs a name of 1 to 200 characters")
            return {"op": "activity", "activity": name}
        op = _OPERATORS[kind]
        built = [build(k) for k in kids]
        if op == "loop":
            if len(built) not in (2, 3):
                raise EventLogWorkspaceError("a loop needs two children (do, redo) or three (do, redo, exit)")
            loop = {"op": "loop", "children": built[:2]}
            if len(built) == 2 or built[2]["op"] == "tau":
                return loop
            return {"op": "seq", "children": [loop, built[2]]}
        if not built:
            raise EventLogWorkspaceError(f"a <{kind}> node has no children")
        return {"op": op, "children": built}

    result = build(top)
    if len(seen) != len(nodes):
        raise EventLogWorkspaceError("some process tree nodes are not connected to the root")
    return result, (tree.get("name") or "").strip()[:200]


_NOTATION_OPS = {"->": "seq", "X": "xor", "+": "and", "*": "loop"}
_REFUSED_NOTATION = {"O": "or", "<>": "interleaving"}
MAX_NOTATION_CHARS = 200_000


def read_notation(text: str) -> dict[str, Any]:
    """A process tree in PM4Py's printed notation, e.g. ``->( 'A', X( 'B', tau ), *( 'C', tau ) )``.

    ``print(tree)`` in PM4Py gives this text. Labels are single-quoted; a label ends at the quote
    that is followed by a comma or a closing bracket, so quotes inside a label are kept.
    """
    if not isinstance(text, str) or not text.strip():
        raise EventLogWorkspaceError("paste a process tree such as ->( 'A', 'B' )")
    if len(text) > MAX_NOTATION_CHARS:
        raise EventLogWorkspaceError(f"the process tree text is longer than {MAX_NOTATION_CHARS:,} characters")
    pos, count = 0, 0

    def skip() -> None:
        nonlocal pos
        while pos < len(text) and text[pos].isspace():
            pos += 1

    def node(depth: int) -> dict[str, Any]:
        nonlocal pos, count
        if depth > 500:
            raise EventLogWorkspaceError("the process tree is nested too deeply")
        count += 1
        if count > MAX_NODES:
            raise EventLogWorkspaceError(f"the process tree has more than {MAX_NODES} nodes")
        skip()
        if text.startswith("'", pos):
            end = pos + 1
            while True:
                end = text.find("'", end)
                if end == -1:
                    raise EventLogWorkspaceError("a quoted activity label is not closed")
                after = end + 1
                while after < len(text) and text[after].isspace():
                    after += 1
                if after >= len(text) or text[after] in ",)":
                    break
                end += 1
            label = text[pos + 1:end].strip()
            pos = end + 1
            if not label or len(label) > 200 or any(ord(c) < 32 for c in label):
                raise EventLogWorkspaceError("an activity needs a name of 1 to 200 characters")
            return {"op": "activity", "activity": label}
        if text.startswith("tau", pos) and not text[pos + 3:pos + 4].isalnum():
            pos += 3
            return {"op": "tau"}
        for symbol, name in _REFUSED_NOTATION.items():
            if text.startswith(symbol + "(", pos) or text.startswith(symbol + " (", pos):
                raise EventLogWorkspaceError(f"the process tree operator {symbol} ({name}) is not supported")
        symbol = next((s for s in _NOTATION_OPS if text.startswith(s, pos)), None)
        if symbol is None:
            raise EventLogWorkspaceError(f"unexpected text at character {pos + 1}: expected ->, X, +, *, tau or a quoted label")
        pos += len(symbol)
        skip()
        if not text.startswith("(", pos):
            raise EventLogWorkspaceError(f"expected ( after {symbol} at character {pos + 1}")
        pos += 1
        kids = []
        while True:
            kids.append(node(depth + 1))
            skip()
            if text.startswith(",", pos):
                pos += 1
                continue
            if text.startswith(")", pos):
                pos += 1
                break
            raise EventLogWorkspaceError(f"expected , or ) at character {pos + 1}")
        op = _NOTATION_OPS[symbol]
        if op == "loop":
            if len(kids) not in (2, 3):
                raise EventLogWorkspaceError("a loop needs two children (do, redo) or three (do, redo, exit)")
            loop = {"op": "loop", "children": kids[:2]}
            return loop if len(kids) == 2 or kids[2]["op"] == "tau" else {"op": "seq", "children": [loop, kids[2]]}
        return {"op": op, "children": kids}

    tree = node(0)
    skip()
    if pos != len(text):
        raise EventLogWorkspaceError(f"unexpected text after the tree at character {pos + 1}")
    return tree


def notation_to_pnml(text: str) -> dict[str, Any]:
    from . import event_log_pnml as pnml
    return _converted(pnml, read_notation(text), "pasted process tree")


def _converted(pnml, tree: dict[str, Any], name: str) -> dict[str, Any]:
    net = pnml.tree_to_net(tree)
    labels = [label for _, label in net["transitions"] if label is not None]
    return {"pnml": pnml.to_pnml(net, name), "name": name,
            "places": len(net["places"]), "transitions": len(net["transitions"]),
            "activities": sorted(set(labels)),
            "silent_transitions": sum(1 for _, label in net["transitions"] if label is None)}


def to_pnml(data: bytes) -> dict[str, Any]:
    from . import event_log_pnml as pnml
    tree, name = read_tree(data)
    return {**_converted(pnml, tree, name or "imported process tree"), "name": name}


__all__ = ["read_tree", "read_notation", "to_pnml", "notation_to_pnml", "MAX_BYTES", "MAX_NOTATION_CHARS"]
