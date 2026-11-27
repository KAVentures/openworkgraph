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
_NODES = {'task', 'manualTask', 'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'startEvent', 'endEvent'}


COLUMN_STEP = 280
COLUMN_LEFT = 100
COLUMN_WIDTH = 180


def _layout_levels(by_id, outgoing, entry_id):
    """Rank the SCC condensation DAG, retaining local order inside loops.

    A short direct edge must not pull a join left of a longer predecessor.
    Strongly connected components have no strict precedence ordering, so
    only their internal presentation uses a deterministic breadth-first rank.
    """
    order = {node: i for i, node in enumerate(by_id)}
    visited, finished = set(), []
    def visit(node):
        visited.add(node)
        for target in outgoing[node]:
            if target not in visited:
                visit(target)
        finished.append(node)
    for node in [entry_id, *by_id]:
        if node not in visited:
            visit(node)
    incoming = defaultdict(list)
    for node in by_id:
        for target in outgoing[node]:
            incoming[target].append(node)
    membership, components = {}, []
    def collect(node, index, members):
        membership[node] = index
        members.append(node)
        for pred in incoming[node]:
            if pred not in membership:
                collect(pred, index, members)
    for node in reversed(finished):
        if node not in membership:
            members = []
            collect(node, len(components), members)
            components.append(members)
    successors, predecessors = defaultdict(set), defaultdict(set)
    for node in by_id:
        source = membership[node]
        for target in outgoing[node]:
            dest = membership[target]
            if source != dest:
                successors[source].add(dest)
                predecessors[dest].add(source)
    internal = {}
    for index, members in enumerate(components):
        roots = [node for node in members if any(membership[p] != index for p in incoming[node])]
        if entry_id in members:
            root = entry_id
        else:
            root = min(roots or members, key=order.get)
        local = {root: 0}
        pending = deque([root])
        while pending:
            source = pending.popleft()
            for target in outgoing[source]:
                if membership[target] == index and target not in local:
                    local[target] = local[source] + 1
                    pending.append(target)
        internal[index] = local
    # Kahn traversal on the acyclic component graph. No repeated paths or
    # unbounded recursive walks through cyclic workflow edges.
    degrees = {i: len(predecessors[i]) for i in range(len(components))}
    ready = deque(sorted(i for i, degree in degrees.items() if not degree))
    last, levels = {}, {}
    while ready:
        index = ready.popleft()
        base = max((last[p] + 1 for p in predecessors[index]), default=0)
        for node, rank in internal[index].items():
            levels[node] = base + rank
        last[index] = base + max(internal[index].values())
        for target in sorted(successors[index]):
            degrees[target] -= 1
            if degrees[target] == 0:
                ready.append(target)
    return levels


# Column gaps grow only where multiple flows need distinct vertical tracks.
# All distances are integer DI coordinates to preserve portable rendering.
MIN_GAP = COLUMN_STEP - COLUMN_WIDTH
TRACK_STEP = 6
TRACK_MARGIN = 16


def _gap_layout(flows, levels):
    """Allocate a distinct vertical track for every edge/gap occurrence.

    Adjacent forward edges need one shared source/target gap track; all
    other non-self edges need two. Backward edges can share a gap with
    another edge but never its exact track. A gap before column zero is
    reserved only when an edge actually approaches that column from the left.
    """
    uses = defaultdict(list)
    for index, flow in enumerate(flows):
        source, target = flow.attrib['sourceRef'], flow.attrib['targetRef']
        a, b = levels[source], levels[target]
        if source == target or b == a+1:
            uses[a].append((index, 'source'))
        else:
            uses[a].append((index, 'source'))
            uses[b-1].append((index, 'target'))
    def width(gap):
        n = len(uses[gap])
        return max(MIN_GAP, 2*TRACK_MARGIN + TRACK_STEP*max(0,n-1))
    lefts = {0: COLUMN_LEFT + (width(-1) if uses[-1] else 0)}
    for level in range(1, max(levels.values())+1):
        lefts[level] = lefts[level-1] + COLUMN_WIDTH + width(level-1)
    tracks = {}
    for gap, entries in uses.items():
        if not entries:
            continue
        left = COLUMN_LEFT if gap == -1 else lefts[gap]+COLUMN_WIDTH
        # Center the occupied tracks inside the available gap, keeping
        # equal free margin on both sides even for narrow single edges.
        occupied = TRACK_STEP*(len(entries)-1)
        first = left + (width(gap)-occupied)//2
        for i, identity in enumerate(entries):
            tracks[identity] = first + i*TRACK_STEP
    return lefts, tracks


def _spread_ports(origin, size, count):
    """Integer interior side offsets; duplicate only past physical capacity."""
    if count <= 1:
        return [origin+size//2] if count else []
    low, high = origin+8, origin+size-8
    return [low + (high-low)*i//(count-1) for i in range(count)]


def _ports(flows, positions, by_id):
    """Spread rectangular task ports, preserving gateway/event center ports.

    Sort by the other endpoint's center to reduce local order inversions.
    Exact shapes of diamonds and circles vary among renderers, so those
    retain side-center attachment. No semantic ordering is inferred.
    """
    incoming, outgoing, loops = defaultdict(list), defaultdict(list), defaultdict(list)
    def center(node):
        x,y,w,h=positions[node]
        return (y+h//2,x+w//2)
    for i, flow in enumerate(flows):
        a,b=flow.attrib['sourceRef'],flow.attrib['targetRef']
        outgoing[a].append((center(b),i))
        if a==b:loops[b].append(i)
        else:incoming[b].append((center(a),i))
    starts, ends = {}, {}
    for node,(x,y,w,h) in positions.items():
        rectangular=by_id[node].tag.removeprefix('{'+MODEL+'}') in {'task','manualTask'}
        for items,dest,edge_x in ((outgoing[node],starts,x+w),(incoming[node],ends,x)):
            ordered=sorted(items)
            ys=_spread_ports(y,h,len(ordered)) if rectangular else [y+h//2]*len(ordered)
            for (_,index),port_y in zip(ordered,ys):
                dest[index]=(edge_x,port_y)
        ordered=sorted(loops[node])
        xs=_spread_ports(x,w,len(ordered)) if rectangular else [x+w//2]*len(ordered)
        for index,port_x in zip(ordered,xs):
            ends[index]=(port_x,y)
    return starts,ends


def _route(source, target, levels, positions, index, tracks, starts, ends):
    start,end=starts[index],ends[index]
    right=tracks[(index,'source')]
    if source==target:
        upper=positions[source][1]-18
        return [start,(right,start[1]),(right,upper),(end[0],upper),end]
    if levels[target]==levels[source]+1:
        return [start,(right,start[1]),(right,end[1]),end]
    left=tracks[(index,'target')]
    # All non-adjacent horizontal tracks lie above all node rectangles.
    track=40+index*8
    return [start,(right,start[1]),(right,track),(left,track),(left,end[1]),end]


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
    levels = _layout_levels(by_id, outgoing, entry_id)
    column_left, tracks = _gap_layout(flows, levels)

    # BPMN 2.0 lanes are actual semantic elements; DI must represent them
    # spatially as well. Layout is presentation-only; no lane is invented.
    lane_sets = process.findall('{'+MODEL+'}laneSet')
    if len(lane_sets) > 1:
        raise ValueError('The process diagram supports at most one lane set')
    lanes: list[tuple[str, list[str]]] = []
    lane_for: dict[str, str] = {}
    if lane_sets:
        for lane in lane_sets[0]:
            if lane.tag != '{'+MODEL+'}lane':
                raise ValueError('Diagram lanes must be plain BPMN lane elements')
            lane_id = lane.attrib.get('id', '')
            if not lane_id or any(row[0] == lane_id for row in lanes):
                raise ValueError('Diagram lanes must have unique IDs')
            if lane.find('{'+MODEL+'}childLaneSet') is not None:
                raise ValueError('Nested lane diagrams are unsupported; review the source model')
            members = [(child.text or '').strip() for child in lane.findall('{'+MODEL+'}flowNodeRef')]
            if not members:
                raise ValueError('A lane needs at least one referenced activity')
            for member in members:
                if member not in by_id or member in lane_for:
                    raise ValueError('Lane references must point to distinct existing diagram activities')
                lane_for[member] = lane_id
            lanes.append((lane_id, members))
    diagram = ET.SubElement(root, '{'+BPMNDI+'}BPMNDiagram', {'id': 'Diagram_OWG'})
    plane = ET.SubElement(diagram, '{'+BPMNDI+'}BPMNPlane',
                          {'id': 'Plane_OWG', 'bpmnElement': process.attrib['id']})
    rows = defaultdict(int)
    positions = {}
    top = 100 + len(flows) * 8
    lane_bands: dict[str | None, tuple[int, int]] = {}
    if lanes:
        groups: list[str | None] = [lane_id for lane_id, _ in lanes]
        if len(lane_for) < len(by_id):
            groups.append(None)  # Unassigned nodes are not assigned a fictitious lane.
        cursor = top
        for group in groups:
            by_level: dict[int, int] = defaultdict(int)
            for node_id in by_id:
                if lane_for.get(node_id) == group:
                    by_level[levels[node_id]] += 1
            band_height = max(180, max(by_level.values(), default=1) * 160 + 40)
            lane_bands[group] = (cursor, band_height)
            cursor += band_height + 22
        # Wide enough to enclose all nodes at the final level plus a margin.
        lane_width = column_left[max(levels.values())] + COLUMN_WIDTH + 90 - 20
        # Draw lane rectangles before activity shapes so they are backgrounds.
        for lane_id, _ in lanes:
            y, height = lane_bands[lane_id]
            shape = ET.SubElement(plane, '{'+BPMNDI+'}BPMNShape',
                                  {'id': 'LaneShape_'+lane_id, 'bpmnElement':lane_id,
                                   'isHorizontal':'true'})
            ET.SubElement(shape, '{'+DC+'}Bounds',
                          {'x':'20', 'y':str(y), 'width':str(lane_width), 'height':str(height)})
            label = ET.SubElement(shape, '{'+BPMNDI+'}BPMNLabel')
            ET.SubElement(label, '{'+DC+'}Bounds',
                          {'x':'25', 'y':str(y+12), 'width':'70', 'height':'60'})
    for node_id, node in by_id.items():
        kind = node.tag.removeprefix('{'+MODEL+'}')
        width, height = (50, 50) if kind in {'exclusiveGateway','parallelGateway','inclusiveGateway'} else ((36, 36) if kind.endswith('Event') else (180, 80))
        level = levels[node_id]
        x = column_left[level] + (COLUMN_WIDTH-width)//2
        if lanes:
            group = lane_for.get(node_id)
            y = lane_bands[group][0] + 40 + 160 * rows[(group, level)] + (80-height)//2
            rows[(group, level)] += 1
        else:
            y = top + 160 * rows[level] + (80-height)//2
            rows[level] += 1
        positions[node_id] = (x, y, width, height)
        shape = ET.SubElement(plane, '{'+BPMNDI+'}BPMNShape',
                              {'id': 'Shape_'+node_id, 'bpmnElement': node_id})
        ET.SubElement(shape, '{'+DC+'}Bounds', dict(zip(('x','y','width','height'), map(str, (x,y,width,height)))))
        if kind not in {'task','manualTask'} and node.get('name'):
            label = ET.SubElement(shape, '{'+BPMNDI+'}BPMNLabel')
            ET.SubElement(label, '{'+DC+'}Bounds',
                          {'x': str(x+width//2-90), 'y': str(y+height+8), 'width':'180', 'height':'40'})
    starts,ends = _ports(flows,positions,by_id)
    for index, flow in enumerate(flows):
        source, target = flow.attrib['sourceRef'], flow.attrib['targetRef']
        points = _route(source, target, levels, positions, index, tracks, starts, ends)
        edge = ET.SubElement(plane, '{'+BPMNDI+'}BPMNEdge',
                             {'id': 'Edge_'+flow.attrib['id'], 'bpmnElement': flow.attrib['id']})
        last = None
        for point in points:
            if point != last:
                ET.SubElement(edge, '{'+DI+'}waypoint', {'x':str(point[0]), 'y':str(point[1])})
                last = point
