"""R13 BPMN semantic swimlanes for user-declared process responsibilities."""
from __future__ import annotations

from xml.etree import ElementTree as ET

from shared.process_bpmn_import import BPMN, import_bpmn
from shared.process_catalog import process_bpmn
from shared.process_model import ProcessModel

NS = {"bpmn": BPMN}


def _process():
    source = f'''<definitions xmlns="{BPMN}"><process id="test">
      <startEvent id="start" name="Start"/>
      <task id="receive" name="Receive"/>
      <exclusiveGateway id="choice" name="Choose" default="reject_path"/>
      <task id="approve" name="Approve"/><task id="reject" name="Reject"/>
      <exclusiveGateway id="join" name="Merge"/>
      <endEvent id="end" name="Done"/>
      <sequenceFlow id="a" sourceRef="start" targetRef="receive"/>
      <sequenceFlow id="b" sourceRef="receive" targetRef="choice"/>
      <sequenceFlow id="approve_path" sourceRef="choice" targetRef="approve">
        <conditionExpression>approved</conditionExpression>
      </sequenceFlow>
      <sequenceFlow id="reject_path" sourceRef="choice" targetRef="reject"/>
      <sequenceFlow id="c" sourceRef="approve" targetRef="join"/>
      <sequenceFlow id="d" sourceRef="reject" targetRef="join"/>
      <sequenceFlow id="e" sourceRef="join" targetRef="end"/>
    </process></definitions>'''.encode()
    return ProcessModel.model_validate(import_bpmn(source)["process_model"])


def _packet(model):
    return {"workflow_id": "process_lane_demo", "revision": 3, "content_sha256": "a" * 64,
            "title": "Approved design draft", "process_model": model.model_dump()}


def test_bpmn_real_lane_set_groups_responsibilities_and_refs_with_correct_namespace():
    model = _process()
    by_label = {node.label: node for node in model.activities}
    by_label["Receive"].responsibility_label = "Finance & Operations"
    by_label["Approve"].responsibility_label = "Finance & Operations"
    by_label["Reject"].responsibility_label = "Support <tier 2>"
    by_label["Choose"].responsibility_label = "Review Team"
    xml = process_bpmn(_packet(model))
    assert xml == process_bpmn(_packet(model))  # deterministic user-reviewed export
    root = ET.fromstring(xml)
    process = root.find(".//bpmn:process", NS)
    assert process is not None and process.get("isExecutable") == "false"
    sets = process.findall("bpmn:laneSet", NS)
    assert len(sets) == 1 and sets[0].get("id") == "LaneSet_OWG"
    lanes = sets[0].findall("bpmn:lane", NS)
    assert [x.get("name") for x in lanes] == [
        "Finance & Operations", "Review Team", "Support <tier 2>"
    ] or [x.get("name") for x in lanes] == [
        "Finance & Operations", "Support <tier 2>", "Review Team"
    ]  # order is based on the model's node order, never a random hash
    assignments = {
        lane.get("name"): [node.text for node in lane.findall("bpmn:flowNodeRef", NS)]
        for lane in lanes
    }
    assert set(assignments["Finance & Operations"]) == {
        "Activity_" + by_label["Receive"].id, "Activity_" + by_label["Approve"].id,
    }
    assert assignments["Support <tier 2>"] == ["Activity_" + by_label["Reject"].id]
    assert assignments["Review Team"] == ["Activity_" + by_label["Choose"].id]
    assigned = [name for ids in assignments.values() for name in ids]
    assert len(assigned) == len(set(assigned)) == 4
    for node in lanes:
        documentation = node.find("bpmn:documentation", NS)
        assert documentation is not None and "Not a verified person" in documentation.text
    # Names are XML-escaped, not treated as HTML or code.
    assert "Finance &amp; Operations" in xml and "Support &lt;tier 2&gt;" in xml
    assert "<script>" not in xml


def test_exported_lanes_and_responsibility_notes_survive_owg_bpmn_import():
    model = _process()
    for node in model.activities:
        if node.label == "Receive":
            node.responsibility_label = "First owner"
        elif node.label in {"Approve", "Reject"}:
            node.responsibility_label = "Second owner"
    packet = _packet(model)
    imported = import_bpmn(process_bpmn(packet).encode("utf-8"))
    values = imported["process_model"]["activities"]
    labels = {node["label"]: node.get("responsibility_label", "") for node in values}
    assert labels["Receive"] == "First owner"
    assert labels["Approve"] == labels["Reject"] == "Second owner"
    assert imported["execution_authorized"] is False
    assert imported["business_outcomes_verified"] is False
    # Parsing a document containing real lanes never approves a policy.
    assert all(not row.get("user_confirmed", False) for row in imported["process_model"]["business_rules"])


def test_no_responsibilities_produce_no_phantom_lanes_or_new_authority():
    model = _process()
    exported = process_bpmn(_packet(model))
    doc = ET.fromstring(exported)
    assert not doc.findall(".//bpmn:laneSet", NS)
    assert "isExecutable=\"false\"" in exported
    assert "Lane_1" not in exported


def test_xml_injection_like_labels_remain_literal_and_do_not_affect_structure():
    model = _process()
    chosen = next(node for node in model.activities if node.label == "Receive")
    chosen.responsibility_label = '</lane><script>alert("x")</script>'
    xml = process_bpmn(_packet(model))
    root = ET.fromstring(xml)
    lanes = root.findall(".//bpmn:lane", NS)
    assert len(lanes) == 1
    assert lanes[0].get("name") == '</lane><script>alert("x")</script>'
    assert "</lane><script>" not in xml
    assert len(root.findall(".//bpmn:script", NS)) == 0
    assert import_bpmn(xml.encode("utf-8"))["execution_authorized"] is False
