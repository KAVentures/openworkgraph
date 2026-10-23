"""Deterministic BPMN Diagram Interchange for OWG's bounded descriptive graphs.

Only geometry is added. Flow semantics, declared conditions and execution
permissions are untouched. This is a portable initial layout, not an optimizer
or a claim of full BPMN modeling/conformance support.
"""
from collections import defaultdict, deque
from xml.etree import ElementTree as ET

MODEL = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
BPMNDI = 'http://www.omg.org/spec/BPMN/20100524/DI'
DC = 'http://www.omg.org/spec/DD/20100524/DC'
DI = 'http://www.omg.org/spec/DD/20100524/DI'
_NODES = {'task', 'manualTask', 'exclusiveGateway', 'parallelGateway', 'startEvent', 'endEvent'}


def add_process_diagram(root: ET.Element, process: ET.Element, *, entry_id: str) -> None:
    """Attach one diagram for an OWG-created process; never parse external XML."""
    for prefix, ns in [('bpmn', MODEL), ('bpmndi', BPMNDI), ('dc', DC), ('di', DI)]:
        ET.register_namespace(prefix, ns)
    nodes = [node for node in process if node.tag.removeprefix('{'+MODEL+'}') in _NODES]
    flows = list(process.findall('{'+MODEL+'}sequenceFlow'))
    if not nodes or len(nodes) > 120 or len(flows) > 200:
        raise ValueError('A bounded OWG process is required for diagram layout')
    by_id = {node.attrib['id']: node for node in nodes}
    if entry_id not in by_id or len(by_id) != len(nodes):
        raise ValueError('Diagram entry and unique activity identities are required')
    outgoing = defaultdict(list)
    for flow in flows:
        source, target = flow.attrib['sourceRef'], flow.attrib['targetRef']
        if source not in by_id or target not in by_id:
            raise ValueError('Diagram transitions must reference existing activities')
        outgoing[source].append(target)
    levels = {entry_id: 0}
    pending = deque([entry_id])
    while pending:
        source = pending.popleft()
        for target in outgoing[source]:
            if target not in levels:
                levels[target] = levels[source] + 1
                pending.append(target)
    # OWG's validated models are reachable; retained standalone end/manual nodes
    # still receive visible geometry rather than silently disappearing.
    fallback = max(levels.values()) + 1
    for node_id in by_id:
        if node_id not in levels:
            levels[node_id] = fallback
    diagram = ET.SubElement(root, '{'+BPMNDI+'}BPMNDiagram', {'id': 'Diagram_OWG'})
    plane = ET.SubElement(diagram, '{'+BPMNDI+'}BPMNPlane',
                          {'id': 'Plane_OWG', 'bpmnElement': process.attrib['id']})
    rows = defaultdict(int)
    positions = {}
    top = 100 + len(flows) * 8
    for node_id, node in by_id.items():
        kind = node.tag.removeprefix('{'+MODEL+'}')
        width, height = (50, 50) if kind in {'exclusiveGateway','parallelGateway'} else ((36, 36) if kind.endswith('Event') else (180, 80))
        level = levels[node_id]
        x, y = 100 + 280 * level + (180-width)//2, top + 160*rows[level] + (80-height)//2
        rows[level] += 1
        positions[node_id] = (x, y, width, height)
        shape = ET.SubElement(plane, '{'+BPMNDI+'}BPMNShape',
                              {'id': 'Shape_'+node_id, 'bpmnElement': node_id})
        ET.SubElement(shape, '{'+DC+'}Bounds', dict(zip(('x','y','width','height'), map(str, (x,y,width,height)))))
        if kind not in {'task','manualTask'} and node.get('name'):
            label = ET.SubElement(shape, '{'+BPMNDI+'}BPMNLabel')
            ET.SubElement(label, '{'+DC+'}Bounds',
                          {'x': str(x+width//2-90), 'y': str(y+height+8), 'width':'180', 'height':'40'})
    for index, flow in enumerate(flows):
        source, target = flow.attrib['sourceRef'], flow.attrib['targetRef']
        sx, sy, sw, sh = positions[source]
        tx, ty, tw, th = positions[target]
        start, end = (sx+sw, sy+sh//2), (tx, ty+th//2)
        if source == target:
            lane = sx+sw+35
            points = [(sx+sw,sy+sh//3), (lane,sy+sh//3), (lane,sy+sh+25),
                      (sx+sw//2,sy+sh+25), (sx+sw//2,sy+sh)]
        elif levels[target] == levels[source]+1:
            middle = (sx+sw+tx)//2
            points = [start, (middle,start[1]), (middle,end[1]), end]
        else:
            # Back/skip edges travel through a dedicated upper lane. This keeps
            # loops visible and avoids claiming that the model is acyclic.
            right, left, lane = sx+sw+28, tx-28, 40+index*8
            points = [start, (right,start[1]), (right,lane), (left,lane), (left,end[1]), end]
        edge = ET.SubElement(plane, '{'+BPMNDI+'}BPMNEdge',
                             {'id': 'Edge_'+flow.attrib['id'], 'bpmnElement': flow.attrib['id']})
        last = None
        for point in points:
            if point != last:
                ET.SubElement(edge, '{'+DI+'}waypoint', {'x':str(point[0]), 'y':str(point[1])})
                last = point
