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


def _diagram_fixture(nodes, flows, lanes=None):
    root=ET.Element('{'+MODEL+'}definitions')
    process=ET.SubElement(root,'{'+MODEL+'}process',{'id':'p','isExecutable':'false'})
    if lanes:
        lane_set=ET.SubElement(process,'{'+MODEL+'}laneSet',{'id':'lanes'})
        for index,members in enumerate(lanes):
            lane=ET.SubElement(lane_set,'{'+MODEL+'}lane',{'id':f'lane_{index}'})
            for member in members:
                ET.SubElement(lane,'{'+MODEL+'}flowNodeRef').text=member
    for name,kind in nodes:
        ET.SubElement(process,'{'+MODEL+'}'+kind,{'id':name,'name':name})
    for index,(source,target) in enumerate(flows):
        ET.SubElement(process,'{'+MODEL+'}sequenceFlow',
                      {'id':f'f{index}','sourceRef':source,'targetRef':target})
    semantic=ET.tostring(process)
    add_process_diagram(root,process,entry_id=nodes[0][0])
    assert ET.tostring(process)==semantic
    return root


def _geometry(root):
    shapes={}
    for shape in root.findall('.//{'+BPMNDI+'}BPMNShape'):
        if shape.get('id').startswith('LaneShape_'):
            continue
        bounds=shape.find('{'+DC+'}Bounds')
        shapes[shape.get('bpmnElement')]=tuple(int(bounds.get(k)) for k in ('x','y','width','height'))
    edges={e.get('bpmnElement'):[(int(p.get('x')),int(p.get('y')))
           for p in e.findall('{'+DI+'}waypoint')]
           for e in root.findall('.//{'+BPMNDI+'}BPMNEdge')}
    return shapes,edges


def _assert_clear_routes(root):
    shapes,edges=_geometry(root)
    for edge in edges.values():
        for (ax,ay),(bx,by) in zip(edge,edge[1:]):
            assert (ax,ay)!=(bx,by)
            assert ax==bx or ay==by
            for name,(x,y,w,h) in shapes.items():
                if ax==bx:
                    intersects=x<ax<x+w and max(min(ay,by),y)<min(max(ay,by),y+h)
                else:
                    intersects=y<ay<y+h and max(min(ax,bx),x)<min(max(ax,bx),x+w)
                assert not intersects, (name,(ax,ay),(bx,by))
    for flow in root.findall('.//{'+MODEL+'}sequenceFlow'):
        points=edges[flow.get('id')]
        for point,ref in ((points[0],flow.get('sourceRef')),(points[-1],flow.get('targetRef'))):
            x,y,w,h=shapes[ref]
            a,b=point
            assert (a in (x,x+w) and y<=b<=y+h) or (b in (y,y+h) and x<=a<=x+w)
    return shapes


def test_join_rank_follows_longest_predecessor_not_shortcut():
    nodes=[('start','startEvent'),('split','parallelGateway'),('short','task'),
           ('long1','task'),('long2','task'),('long3','task'),('join','parallelGateway'),('end','endEvent')]
    flows=[('start','split'),('split','short'),('split','long1'),('short','join'),
           ('long1','long2'),('long2','long3'),('long3','join'),('join','end')]
    root=_diagram_fixture(nodes,flows)
    shapes=_assert_clear_routes(root)
    assert shapes['join'][0]>shapes['long3'][0]
    assert shapes['end'][0]>shapes['join'][0]
    assert ET.tostring(root)==ET.tostring(_diagram_fixture(nodes,flows))


def test_loops_keep_exit_after_component_and_avoid_cross_lane_task_rectangles():
    nodes=[('start','startEvent'),('branch','exclusiveGateway'),('task_a','task'),
           ('retry','exclusiveGateway'),('task_b','task'),('end','endEvent'),
           ('other','task'),('other_end','endEvent')]
    flows=[('start','branch'),('branch','task_a'),('task_a','retry'),('retry','task_b'),
           ('task_b','task_a'),('retry','retry'),('retry','end'),('branch','other'),
           ('other','other_end'),('other','end')]
    lanes=[['start','branch','retry','end'],['task_a','task_b','other','other_end']]
    root=_diagram_fixture(nodes,flows,lanes)
    shapes=_assert_clear_routes(root)
    assert shapes['end'][0]>max(shapes[n][0] for n in ('task_a','retry','task_b'))
    assert ET.tostring(root)==ET.tostring(_diagram_fixture(nodes,flows,lanes))


def test_same_column_back_edges_and_disconnected_nodes_stay_visible():
    nodes=[('start','task'),('left','exclusiveGateway'),('right','task'),
           ('end','endEvent'),('standalone','manualTask')]
    flows=[('start','left'),('left','right'),('right','left'),('left','end'),
           ('right','right')]
    root=_diagram_fixture(nodes,flows)
    shapes=_assert_clear_routes(root)
    assert set(shapes)=={name for name,_ in nodes}
    assert shapes['end'][0]>shapes['right'][0]


def test_maximum_supported_graph_layout_is_bounded_and_deterministic():
    nodes=[(f'n{i}','task') for i in range(120)]
    flows=[(f'n{i}',f'n{i+1}') for i in range(119)]
    flows += [(f'n{i}',f'n{i+2}') for i in range(81)]
    root=_diagram_fixture(nodes,flows)
    shapes=_assert_clear_routes(root)
    assert len(shapes)==120
    assert shapes['n119'][0]>shapes['n118'][0]
    assert len(_geometry(root)[1])==200
