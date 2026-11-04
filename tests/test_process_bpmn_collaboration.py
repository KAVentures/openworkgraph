"""One internal process plus black-box pools: message flows as notes, lanes as responsibility."""
import pytest

from shared.process_bpmn_import import BPMN, BPMNImportError, import_bpmn
from shared.process_model import ProcessModel

PROCESS = '''<process id="p"><laneSet id="ls"><lane id="l1" name="Support desk"><flowNodeRef>t1</flowNodeRef></lane>
  <lane id="l2" name="Finance"><childLaneSet id="cls"><lane id="l3" name="Billing"><flowNodeRef>t2</flowNodeRef></lane></childLaneSet></lane></laneSet>
  <startEvent id="s" name="Start"/><receiveTask id="t1" name="Receive request"/><sendTask id="t2" name="Send invoice"/>
  <endEvent id="e" name="End"/><sequenceFlow id="f1" sourceRef="s" targetRef="t1"/>
  <sequenceFlow id="f2" sourceRef="t1" targetRef="t2"/><sequenceFlow id="f3" sourceRef="t2" targetRef="e"/></process>'''

COLLAB = '''<collaboration id="c"><participant id="pp" name="Our company" processRef="p"/>
  <participant id="cust" name="Customer"/>
  <messageFlow id="m1" sourceRef="cust" targetRef="t1" messageRef="req"/>
  <messageFlow id="m2" sourceRef="t2" targetRef="cust" name="Invoice"/></collaboration>
  <message id="req" name="Service request"/>'''


def doc(collab=COLLAB, process=PROCESS):
    return f'<definitions xmlns="{BPMN}">{collab}{process}</definitions>'.encode()


def test_message_flows_become_notes_and_lanes_become_responsibilities():
    result = import_bpmn(doc())
    model = ProcessModel.model_validate(result["process_model"])
    owners = {a.label: a.responsibility_label for a in model.activities if a.kind == "activity"}
    assert owners == {"Receive request": "Support desk", "Send invoice": "Billing"}
    notes = sorted(n["text"] for n in result["documentation_notes"])
    assert notes == ["Receives Service request from Customer (message flow; no control-flow effect).",
                     "Sends Invoice to Customer (message flow; no control-flow effect)."]
    assert result["message_flow_count"] == 2 and result["lane_responsibility_count"] == 2
    assert "message_flows_recorded_as_task_notes_without_control_flow" in result["warnings"]
    assert "lane_names_imported_as_descriptive_responsibility_labels" in result["warnings"]


def test_lanes_alone_are_supported_without_a_collaboration():
    model = ProcessModel.model_validate(import_bpmn(doc(collab=""))["process_model"])
    assert {a.responsibility_label for a in model.activities if a.kind == "activity"} == {"Support desk", "Billing"}


@pytest.mark.parametrize("collab", [
    COLLAB.replace('<participant id="cust" name="Customer"/>', '<participant id="cust" name="Customer" processRef="other"/>'),
    COLLAB.replace('processRef="p"', ''),
    COLLAB.replace('targetRef="t1"', 'targetRef="t2"').replace('sourceRef="cust"', 'sourceRef="t1"'),
    COLLAB.replace('</collaboration>', '<conversation id="x"/></collaboration>'),
    COLLAB.replace('<message id="req" name="Service request"/>', '<message id="req"><extensionElements/></message>'),
    COLLAB + COLLAB.replace('id="c"', 'id="c2"').replace('id="pp"', 'id="pp2"').replace('id="cust"', 'id="cust2"')
         .replace('id="m1"', 'id="m3"').replace('id="m2"', 'id="m4"').replace('<message id="req" name="Service request"/>', ''),
])
def test_multi_process_or_malformed_collaborations_are_refused(collab):
    with pytest.raises(BPMNImportError):
        import_bpmn(doc(collab=collab))


def test_lanes_referencing_unknown_nodes_are_refused():
    with pytest.raises(BPMNImportError):
        import_bpmn(doc(process=PROCESS.replace("<flowNodeRef>t1</flowNodeRef>", "<flowNodeRef>nope</flowNodeRef>")))
