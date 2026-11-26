"""Export a discovered process model as BPMN 2.0 with diagram interchange.

Camunda Modeler, bpmn.io, Signavio, Bizagi, ARIS and most process designers exchange models as
BPMN 2.0 XML. Discovery (``event_log_model_discovery``) already lowers its process tree into the
same ``ProcessModel`` the local process catalog saves, so this module reuses the catalog's BPMN
writer and its diagram layout instead of a second serializer: tasks, exclusive gateways for
choices and loops, parallel gateways for concurrent branches, start and end events, sequence flows
and BPMNDI shapes and edges.

The only difference from a catalog export is the process documentation. A discovered model is an
unreviewed draft computed from the event log, so the file says that and carries the model digest
and discovery settings instead of a catalog revision. Observed branch shares stay on the flows as
unconfirmed rule text. The file is a non-executable description; importing it anywhere grants no
permission to run anything.
"""
from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from shared.process_catalog import process_bpmn

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def _describe(xml: str, text: str, name: str) -> str:
    root = ET.fromstring(xml)
    process = root.find(f"{{{BPMN_NS}}}process")
    process.set("name", name)
    process.find(f"{{{BPMN_NS}}}documentation").text = text
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def discovered_bpmn(log_id: str, case_type: str | None = None, *, noise: float = 0.2, activities: float = 1.0,
                    cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    from . import event_log_model_discovery as discovery
    result = discovery.discover(log_id, case_type, noise=noise, activities=activities, cohort=cohort)
    # The catalog requires a stated goal; discovery leaves it blank for the reviewer to write.
    model = {**result["process_model"], "goal": result["process_model"]["goal"] or "Discovered draft awaiting review"}
    digest = result["process_model_sha256"]
    packet = {"process_model": model, "workflow_id": "discovered", "revision": 0, "content_sha256": digest}
    name = f"{result['log']['name']} ({result['case_notion']}) discovered model"[:200]
    text = (f"Unreviewed draft discovered from event log {result['log']['name']!r} "
            f"(case notion {result['case_notion']!r}; noise {result['settings']['noise']}, "
            f"activities {result['settings']['activities']}; model sha256 {digest}). "
            "Branch rules state observed shares of cases only. Not policy or execution authority.")
    kinds = [a["kind"] for a in model["activities"]]
    meta = {"tasks": kinds.count("activity"),
            "gateways": sum(k not in {"activity", "start", "end"} for k in kinds),
            "flows": len(model["transitions"]),
            "fitting_case_share": result["quality"]["fitting_case_share"],
            "process_model_sha256": digest}
    return _describe(process_bpmn(packet), text, name), meta
