"""Export a discovered process tree as PTML, the process-tree format of ProM and PM4Py.

Discovery (``event_log_model_discovery``) builds a block-structured process tree, as the
Inductive Miner does. BPMN and PNML exports flatten that tree into gateways or places; PTML keeps
the tree itself, so ``pm4py.read_ptml`` and ProM's process-tree plug-ins see the same operators:

* an activity is a ``manualTask``; a skip (tau) is an ``automaticTask``;
* ``seq``, ``xor`` and ``and`` become ``sequence``, ``xor`` and ``and``;
* a loop (body, redo) becomes ``xorLoop`` with the body, the redo part and a silent exit, the
  three-child loop ProM and PM4Py read.

Children are listed in order through ``parentsNode`` edges. Identifiers are UUIDs derived from
the node's position, so the same tree always gives the same file, and activity names only ever
appear as escaped ``name`` attributes.
"""
from __future__ import annotations

import uuid
from typing import Any
from xml.etree import ElementTree as ET

from .event_log_workspace import EventLogWorkspaceError

MAX_NODES = 5_000
_NAMESPACE = uuid.UUID("6f1d3c52-2b8e-4f0e-9a4f-2f1a7c3e5d10")
_OPERATORS = {"seq": "sequence", "xor": "xor", "and": "and", "loop": "xorLoop"}


def to_ptml(tree: dict[str, Any], name: str) -> str:
    root = ET.Element("ptml")
    nodes: list[tuple[str, str, str]] = []   # (tag, id, name)
    edges: list[tuple[str, str]] = []

    def new(tag: str, label: str) -> str:
        if len(nodes) >= MAX_NODES:
            raise EventLogWorkspaceError(f"the process tree would exceed {MAX_NODES} nodes")
        node_id = str(uuid.uuid5(_NAMESPACE, str(len(nodes))))
        nodes.append((tag, node_id, label))
        return node_id

    def emit(node: dict[str, Any]) -> str:
        op = node.get("op")
        if op == "activity":
            return new("manualTask", str(node["activity"]))
        if op == "tau":
            return new("automaticTask", "tau")
        if op not in _OPERATORS:
            raise EventLogWorkspaceError(f"unknown process tree operator: {op!r}")
        here = new(_OPERATORS[op], "")
        children = list(node["children"])
        if op == "loop":
            children.append({"op": "tau"})   # PTML loops are (do, redo, exit)
        for child in children:
            edges.append((here, emit(child)))
        return here

    top = emit(tree)
    process = ET.SubElement(root, "processTree", id=str(uuid.uuid5(_NAMESPACE, "tree")), name=name, root=top)
    for tag, node_id, label in nodes:
        ET.SubElement(process, tag, id=node_id, name=label)
    for parent, child in edges:
        ET.SubElement(process, "parentsNode", id=str(uuid.uuid5(_NAMESPACE, f"edge:{parent}:{child}")),
                      sourceId=parent, targetId=child)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def discovered_ptml(log_id: str, case_type: str | None = None, *, noise: float = 0.2, activities: float = 1.0,
                    cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    from . import event_log_model_discovery as discovery
    result = discovery.discover(log_id, case_type, noise=noise, activities=activities, cohort=cohort)
    name = f"{result['log']['name']} ({result['case_notion']}) discovered tree"[:200]
    xml = to_ptml(result["tree"], name)
    root = ET.fromstring(xml).find("processTree")
    meta = {"nodes": sum(1 for e in root if e.tag != "parentsNode"),
            "activities": sum(1 for e in root if e.tag == "manualTask"),
            "fitting_case_share": result["quality"]["fitting_case_share"],
            "process_model_sha256": result["process_model_sha256"]}
    return xml, meta
