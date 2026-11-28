"""Export the handover and working-together network as GraphML, for Gephi, yEd and Cytoscape.

Disco, ProM and Celonis draw a social network from who hands work to whom. Analysts who want to
lay it out or measure it further usually move it into a graph tool, and GraphML is the format
those tools share. The export is the organizational view of ``event_log_organization``, with
the same filters, limits and naming choice:

* one node per listed resource, with its event, case and activity counts and its kind
  (``automation`` or ``unclassified``), plus "Other resources" when unlisted resources take part;
* directed ``handover`` edges (count and cases), directed ``subcontracting`` edges (work passed
  on and straight back) and ``working_together`` edges (shared cases) marked ``symmetric``.

The graph is directed throughout: networkx and other readers refuse a directed graph with
``directed="false"`` edges, so a working-together pair is one edge whose ``symmetric`` attribute is
true and whose direction carries no meaning.

Resources are pseudonymous ("Resource 01") unless the user asks for names, exactly as on the
dashboard. No per-person duration, speed, ranking or score is added. Node identifiers are
generated (``r0``, ``r1``, …); labels are XML-escaped data, never markup.
"""
from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
_KEYS = [("label", "node", "string"), ("events", "node", "int"), ("cases", "node", "int"),
         ("activities", "node", "int"), ("kind", "node", "string"),
         ("relation", "edge", "string"), ("weight", "edge", "int"), ("edge_cases", "edge", "int"),
         ("symmetric", "edge", "boolean")]


def to_graphml(result: dict[str, Any], name: str) -> str:
    ET.register_namespace("", GRAPHML_NS)
    root = ET.Element(f"{{{GRAPHML_NS}}}graphml")
    for key, domain, kind in _KEYS:
        ET.SubElement(root, f"{{{GRAPHML_NS}}}key", {"id": key, "for": domain, "attr.name": key, "attr.type": kind})
    graph = ET.SubElement(root, f"{{{GRAPHML_NS}}}graph", {"id": "organization", "edgedefault": "directed"})
    ET.SubElement(graph, f"{{{GRAPHML_NS}}}desc").text = (
        f"{name}. {result['interpretation']} Resource labels are "
        f"{'names from the log' if result['names'] == 'shown' else 'pseudonyms valid within this file only'}.")
    ids: dict[str, str] = {}

    def data(parent: ET.Element, key: str, value: Any) -> None:
        ET.SubElement(parent, f"{{{GRAPHML_NS}}}data", {"key": key}).text = str(value)

    def node(label: str, stats: dict[str, Any] | None = None) -> str:
        if label not in ids:
            ids[label] = f"r{len(ids)}"
            element = ET.SubElement(graph, f"{{{GRAPHML_NS}}}node", {"id": ids[label]})
            data(element, "label", label)
            for key in ("events", "cases", "activities", "kind"):
                if stats is not None:
                    data(element, key, stats[key])
        return ids[label]

    for resource in result["resources"]:
        node(resource["resource"], resource)
    edges: list[tuple[str, str, str, int, int | None, bool]] = (
        [(h["from"], h["to"], "handover", h["handovers"], h["cases"], True) for h in result["handover_of_work"]]
        + [(s["from"], s["to"], "subcontracting", s["times"], s["cases"], True) for s in result["subcontracting"]]
        + [(*w["resources"], "working_together", w["shared_cases"], w["shared_cases"], False)
           for w in result["working_together"]])
    for index, (source, target, relation, weight, cases, directed) in enumerate(edges):
        attrs = {"id": f"e{index}", "source": node(source), "target": node(target)}
        element = ET.SubElement(graph, f"{{{GRAPHML_NS}}}edge", attrs)
        data(element, "relation", relation)
        data(element, "weight", weight)
        data(element, "edge_cases", cases)
        data(element, "symmetric", "false" if directed else "true")
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def organization_graphml(log_id: str, case_type: str | None = None, **options: Any) -> tuple[str, dict[str, Any]]:
    from . import event_log_organization as organization
    result = organization.organization(log_id, case_type, **options)
    name = f"{result['log']['name']} ({result['case_notion']}) resource network"[:200]
    meta = {"resources": len(result["resources"]), "handovers": len(result["handover_of_work"]),
            "working_together": len(result["working_together"]), "names": result["names"]}
    return to_graphml(result, name), meta
