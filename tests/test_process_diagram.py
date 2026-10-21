"""Exported geometry resolves all semantic nodes/flows without changing meaning."""
from math import isfinite
from xml.etree import ElementTree as ET

import pytest

from shared.process_catalog import process_bpmn
from shared.process_diagram import add_process_diagram, MODEL, BPMNDI, DC, DI
from tests.test_process_catalog import branched_model
from server.process_agent_starter import _review_bpmn


def graph_xml():
    value=branched_model().model_dump()
    value['business_rules'].append({'id':'retry','text':'Clarify missing evidence','user_confirmed':False})
    value['transitions'].append({'source':'decision','target':'decision','rule_id':'retry'})
    return process_bpmn({'title':'Review','workflow_id':'claims','revision':4,'content_sha256':'a'*64,'process_model':value})


def test_diagram_resolves_every_shape_and_flow_preserves_self_loop_and_semantics():
    xml=graph_xml();root=ET.fromstring(xml)
    assert xml==graph_xml()
    process=root.find('{'+MODEL+'}process')
    assert process.get('isExecutable')=='false'
    assert 'revision 4 sha256 '+('a'*64) in ''.join(process.itertext())
    assert root.find('.//{'+MODEL+'}conditionExpression') is None
    plane=root.find('.//{'+BPMNDI+'}BPMNPlane')
    assert plane.get('bpmnElement')==process.get('id')
    nodes={node.get('id') for node in process if node.tag.rsplit('}',1)[-1] in {'task','exclusiveGateway'}}
    shapes=plane.findall('{'+BPMNDI+'}BPMNShape')
    assert {shape.get('bpmnElement') for shape in shapes}==nodes
    coords=[]
    for shape in shapes:
        bounds=shape.find('{'+DC+'}Bounds')
        values=[float(bounds.get(key)) for key in ('x','y','width','height')]
        assert all(isfinite(value) and value>0 for value in values)
        coords.append(tuple(values[:2]))
    assert len(coords)==len(set(coords))
    flows={flow.get('id'):flow for flow in process.findall('{'+MODEL+'}sequenceFlow')}
    edges=plane.findall('{'+BPMNDI+'}BPMNEdge')
    assert {edge.get('bpmnElement') for edge in edges}==set(flows)
    for edge in edges:
        points=edge.findall('{'+DI+'}waypoint')
        assert len(points)>=2
        assert all(isfinite(float(p.get(k))) for p in points for k in ('x','y'))
        flow=flows[edge.get('bpmnElement')]
        if flow.get('sourceRef')==flow.get('targetRef'):
            assert len(points)>=4
    all_ids=[e.get('id') for e in root.iter() if e.get('id')]
    assert len(all_ids)==len(set(all_ids))


def test_manual_gateway_starter_has_geometry_for_manual_paths_and_no_evaluated_conditions():
    xml=_review_bpmn(['Receive','Review','Close'],[{'at_step_id':'step_002',
        'condition_label':'Missing approval','response':'pause_for_review'}])
    root=ET.fromstring(xml)
    nodes=root.findall('.//{'+BPMNDI+'}BPMNShape')
    assert any(n.get('bpmnElement')=='Manual_Review_002_01' for n in nodes)
    assert any(n.get('bpmnElement')=='Review_Gateway_002' for n in nodes)
    assert 'Missing approval' in xml
    assert 'conditionExpression' not in xml
    assert root.find('{'+MODEL+'}process').get('isExecutable')=='false'


def test_geometry_rejects_unknown_endpoints_instead_of_drawing_invented_nodes():
    root=ET.Element('{'+MODEL+'}definitions')
    process=ET.SubElement(root,'{'+MODEL+'}process',{'id':'p'})
    ET.SubElement(process,'{'+MODEL+'}task',{'id':'known'})
    ET.SubElement(process,'{'+MODEL+'}sequenceFlow',{'id':'flow','sourceRef':'known','targetRef':'missing'})
    with pytest.raises(ValueError,match='existing activities'):
        add_process_diagram(root,process,entry_id='known')
