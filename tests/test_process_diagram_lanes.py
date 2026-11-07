"""BPMN DI geometry must visualize declared lanes, not just semantic lane refs."""
from __future__ import annotations

from math import isfinite
from xml.etree import ElementTree as ET

import pytest

from shared.process_catalog import process_bpmn
from shared.process_diagram import MODEL, BPMNDI, DC, add_process_diagram
from shared.process_model import ProcessModel
from tests.test_process_catalog import branched_model


def _packet(model):
    return {"workflow_id": "swimlanes", "revision": 1,
            "content_sha256": "a" * 64, "title": "Reviewed proposed design",
            "process_model": model.model_dump()}


def _bounds(node):
    rect = node.find("{" + DC + "}Bounds")
    assert rect is not None
    values = tuple(float(rect.get(k)) for k in ("x", "y", "width", "height"))
    assert all(isfinite(v) and v > 0 for v in values)
    return values


def _contains(outer, inner):
    x, y, width, height = outer
    a, b, c, d = inner
    return a >= x and b >= y and a + c <= x + width and b + d <= y + height


def _model_with_lanes():
    value = branched_model().model_dump()
    labels = {
        "a": "Operations", "b": "Finance", "decision": "Operations",
        "c": "Support", "reject": "Support",
    }
    for step in value["activities"]:
        step["responsibility_label"] = labels[step["id"]]
    return ProcessModel.model_validate(value)


def test_diagram_creates_standard_lane_shapes_and_places_respective_steps_inside():
    model = _model_with_lanes()
    xml = process_bpmn(_packet(model))
    assert xml == process_bpmn(_packet(model))  # reproducible order, IDs, positioning
    root = ET.fromstring(xml)
    ns = "{" + MODEL + "}"
    di = "{" + BPMNDI + "}"
    process = root.find(ns + "process")
    lane_elements = process.find(ns + "laneSet").findall(ns + "lane")
    assert [node.get("name") for node in lane_elements] == [
        "Operations", "Finance", "Support",
    ]
    plane = root.find(".//" + di + "BPMNPlane")
    shapes = plane.findall(di + "BPMNShape")
    by_ref = {node.get("bpmnElement"): node for node in shapes}
    assert len(shapes) == len(by_ref) == len(model.activities) + len(lane_elements)
    lane_refs = {lane.get("id") for lane in lane_elements}
    assert all(by_ref[lane_id].get("isHorizontal") == "true" for lane_id in lane_refs)
    for lane in lane_elements:
        shape = by_ref[lane.get("id")]
        assert shape.get("id") == "LaneShape_" + lane.get("id")
        label = shape.find(di + "BPMNLabel")
        assert label is not None and _bounds(label)
        for member in lane.findall(ns + "flowNodeRef"):
            assert member.text in by_ref
            assert _contains(_bounds(shape), _bounds(by_ref[member.text]))
    all_ids = [node.get("id") for node in root.iter() if node.get("id")]
    assert len(all_ids) == len(set(all_ids))
    assert process.get("isExecutable") == "false"
    assert "not policy or execution authority" in xml


def test_unassigned_steps_are_not_silently_put_into_an_invented_lane():
    model = _model_with_lanes()
    value = model.model_dump()
    for step in value["activities"]:
        if step["id"] == "reject":
            step["responsibility_label"] = ""
    source = process_bpmn(_packet(ProcessModel.model_validate(value)))
    root = ET.fromstring(source)
    di = "{" + BPMNDI + "}"
    plane = root.find(".//" + di + "BPMNPlane")
    shapes = {node.get("bpmnElement"): node for node in plane.findall(di + "BPMNShape")}
    lane_set = root.find(".//{" + MODEL + "}laneSet")
    lanes = lane_set.findall("{" + MODEL + "}lane")
    assert len(lanes) == 3
    unassigned = _bounds(shapes["Activity_reject"])
    assert not any(_contains(_bounds(shapes[lane.get("id")]), unassigned) for lane in lanes)
    assigned = [
        ref.text for lane in lanes for ref in lane.findall("{" + MODEL + "}flowNodeRef")
    ]
    assert "Activity_reject" not in assigned
    assert len(set(assigned)) == len(assigned)


def test_multiple_nodes_at_same_graph_level_fit_their_lane_without_overlap():
    value = _model_with_lanes().model_dump()
    for step in value["activities"]:
        if step["id"] == "c":
            step["responsibility_label"] = "Support"
        if step["id"] == "reject":
            step["responsibility_label"] = "Support"
    root = ET.fromstring(process_bpmn(_packet(ProcessModel.model_validate(value))))
    di = "{" + BPMNDI + "}"
    plane = root.find(".//" + di + "BPMNPlane")
    shapes = {node.get("bpmnElement"): node for node in plane.findall(di + "BPMNShape")}
    support_lane = next(
        lane for lane in root.findall(".//{" + MODEL + "}lane") if lane.get("name") == "Support"
    )
    rectangle = _bounds(shapes[support_lane.get("id")])
    first, second = _bounds(shapes["Activity_c"]), _bounds(shapes["Activity_reject"])
    assert _contains(rectangle, first) and _contains(rectangle, second)
    assert first[1] != second[1]
    assert first[1] + first[3] <= second[1] or second[1] + second[3] <= first[1]


def test_no_lane_set_keeps_previous_shape_contract_and_determinism():
    model = branched_model()
    xml = process_bpmn(_packet(model))
    assert xml == process_bpmn(_packet(model))
    root = ET.fromstring(xml)
    assert root.find(".//{" + MODEL + "}laneSet") is None
    shapes = root.findall(".//{" + BPMNDI + "}BPMNShape")
    assert len(shapes) == len(model.activities)
    assert not any(s.get("id", "").startswith("LaneShape_") for s in shapes)


@pytest.mark.parametrize("reason", ["unknown", "duplicated", "nested", "multiple_sets"])
def test_untrusted_or_malformed_di_lane_reference_fails_closed(reason):
    root = ET.Element("{" + MODEL + "}definitions")
    process = ET.SubElement(root, "{" + MODEL + "}process", {"id": "p"})
    ET.SubElement(process, "{" + MODEL + "}task", {"id": "one"})
    ET.SubElement(process, "{" + MODEL + "}task", {"id": "two"})
    ET.SubElement(process, "{" + MODEL + "}sequenceFlow",
                  {"id": "flow", "sourceRef": "one", "targetRef": "two"})
    set_node = ET.SubElement(process, "{" + MODEL + "}laneSet", {"id": "ls"})
    lane = ET.SubElement(set_node, "{" + MODEL + "}lane", {"id": "lane1"})
    ET.SubElement(lane, "{" + MODEL + "}flowNodeRef").text = "missing" if reason == "unknown" else "one"
    if reason == "duplicated":
        other = ET.SubElement(set_node, "{" + MODEL + "}lane", {"id": "lane2"})
        ET.SubElement(other, "{" + MODEL + "}flowNodeRef").text = "one"
    elif reason == "nested":
        ET.SubElement(lane, "{" + MODEL + "}childLaneSet", {"id": "child"})
    elif reason == "multiple_sets":
        ET.SubElement(process, "{" + MODEL + "}laneSet", {"id": "ls2"})
    with pytest.raises(ValueError, match="lane|Lane"):
        add_process_diagram(root, process, entry_id="one")
