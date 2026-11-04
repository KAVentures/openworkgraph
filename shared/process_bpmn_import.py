"""Bounded descriptive BPMN import. XML is data, never an execution policy.

Support serial/exclusive choice and properly nested paired parallel regions.
Plain embedded subprocesses (one start, one end, single plain entry and exit, no
boundary events, loops, multi-instance or event triggers) are inlined, which is
exact for those semantics; the grouping is reported for review. Conditional
parallel, inclusive, other subprocess and event-driven semantics must not be
flattened into an incorrect sequential model. No XML resolution, provider calls or persistence.
"""
from __future__ import annotations

from collections import Counter
import copy
from hashlib import sha256
import re
import json
from xml.etree import ElementTree as ET

from pydantic import ValidationError, BaseModel, ConfigDict, Field

from .process_model import ProcessModel
from .process_rule_conditions import RulePredicate
from .process_decision_tables import DecisionTable, ID

MAX_BYTES = 128 * 1024
BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DIAGRAM_NAMESPACES = {
    'http://www.omg.org/spec/BPMN/20100524/DI',
    'http://www.omg.org/spec/DD/20100524/DC',
    'http://www.omg.org/spec/DD/20100524/DI',
}
TASKS = {'task','userTask','manualTask','serviceTask','sendTask','receiveTask','businessRuleTask'}
FLOW_NODES = TASKS | {'startEvent','endEvent','exclusiveGateway','parallelGateway','inclusiveGateway'}
_LABEL = re.compile(r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$')
_MANUAL = re.compile(r'^UNEVALUATED manual review \((decision_point|exception_condition)\): '
    r'([A-Za-z][A-Za-z0-9 _-]{0,63}); response: '
    r'(pause_for_review|request_source_recheck|escalate_to_owner)\. No automatic branch is authorized\.$')
_RESPONSIBILITY = re.compile(r'^User-declared responsibility: (.+)\. '
    r'Descriptive only; not verified identity or review/execution authority\.$')


class BPMNImportError(ValueError):
    """Fixed safe errors must never echo source XML or annotation payloads."""


class _TableRow(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    id: ID
    flow_id: str = Field(min_length=1,max_length=200)


class _TableDocument(DecisionTable):
    decision_activity_id: str = Field(min_length=1,max_length=200)
    rows: list[_TableRow] = Field(min_length=2,max_length=40)

    # The semantic row/rule/target integrity is validated after flow remapping.
    def distinct_rows(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError('Invalid effective interval')
        if len({r.id for r in self.rows})!=len(self.rows) or len({r.flow_id for r in self.rows})!=len(self.rows):
            raise ValueError('Duplicate decision row')
        return self


def _json_document(text):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate field')
            result[k]=v
        return result
    def invalid(_value):raise ValueError('nonstandard JSON')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)


def _name(element):
    if not element.tag.startswith('{'+BPMN+'}'):
        raise BPMNImportError('Unsupported element namespace in the process definition')
    return element.tag[len(BPMN)+2:]


MAX_SUBPROCESS_DEPTH = 3
_SUB_ALLOWED_ATTRS = {'id', 'name'}


def _plain_flow(flow):
    """A flow carrying no condition, documentation or label."""
    return not len(flow) and not (flow.get('name') or '').strip()


def _inline_subprocesses(container, inlined, depth=0):
    """Replace each plain embedded subprocess in ``container`` by its contents.

    Exact only because entry/exit are single plain flows and the body has one
    start and one end event; anything else is refused, never approximated.
    ``inlined`` collects (subprocess_id, label, member_source_ids).
    """
    subprocesses=[child for child in list(container) if _name(child)=='subProcess']
    if not subprocesses:
        return
    if depth>=MAX_SUBPROCESS_DEPTH:
        raise BPMNImportError('Embedded subprocesses may be nested at most 3 levels deep')
    for sub in subprocesses:
        if set(sub.attrib)-_SUB_ALLOWED_ATTRS:
            raise BPMNImportError('Event subprocesses, transactions and subprocess execution attributes are unsupported')
        _inline_subprocesses(sub, inlined, depth+1)
        sub_id=sub.get('id','')
        body=[child for child in sub if _name(child) not in {'incoming','outgoing','documentation'}]
        docs=[child for child in sub if _name(child)=='documentation']
        for child in body:
            if _name(child) not in FLOW_NODES|{'sequenceFlow'}:
                raise BPMNImportError('Subprocess loops, multi-instance, boundary events and other semantics cannot be flattened')
        flows=[child for child in body if _name(child)=='sequenceFlow']
        nodes=[child for child in body if _name(child)!='sequenceFlow']
        starts=[node for node in nodes if _name(node)=='startEvent']
        ends=[node for node in nodes if _name(node)=='endEvent']
        if len(starts)!=1 or len(ends)!=1:
            raise BPMNImportError('An embedded subprocess needs exactly one plain start and one plain end event')
        start_id,end_id=starts[0].get('id'),ends[0].get('id')
        for event in (starts[0],ends[0]):
            if any(_name(child) not in {'incoming','outgoing'} for child in event):
                raise BPMNImportError('Subprocess start/end events must be plain')
        start_out=[flow for flow in flows if flow.get('sourceRef')==start_id]
        end_in=[flow for flow in flows if flow.get('targetRef')==end_id]
        if (len(start_out)!=1 or len(end_in)!=1 or not _plain_flow(start_out[0])
                or any(flow.get('targetRef')==start_id or flow.get('sourceRef')==end_id for flow in flows)):
            raise BPMNImportError('A subprocess start and end need exactly one plain internal transition each')
        siblings=[child for child in container if _name(child)=='sequenceFlow']
        entry=[flow for flow in siblings if flow.get('targetRef')==sub_id]
        exit_=[flow for flow in siblings if flow.get('sourceRef')==sub_id]
        if len(entry)!=1 or len(exit_)!=1 or not _plain_flow(exit_[0]):
            raise BPMNImportError('An embedded subprocess needs one incoming and one plain outgoing transition')
        # Entry goes to the first inner step; the last inner flow continues to the exit target.
        entry[0].set('targetRef',start_out[0].get('targetRef'))
        end_in[0].set('targetRef',exit_[0].get('targetRef'))
        members=[node.get('id','') for node in nodes if node not in (starts[0],ends[0])]
        if not members:
            raise BPMNImportError('An embedded subprocess must contain at least one step')
        position=list(container).index(sub)
        container.remove(sub);container.remove(exit_[0])
        hoisted=[child for child in body if child not in (starts[0],ends[0],start_out[0])]
        for doc in docs:
            if (doc.text or '').strip():
                note=ET.Element('{'+BPMN+'}documentation');note.text='Subprocess documentation: '+doc.text.strip()
                hoisted.append(note)
        for offset,child in enumerate(hoisted):
            container.insert(position+offset,child)
        inlined.append((sub_id,(sub.get('name') or '').strip(),members))


_BOUNDARY_KINDS = {'errorEventDefinition': 'error', 'timerEventDefinition': 'timer',
                   'escalationEventDefinition': 'escalation'}
_LOOP_TAGS = {'standardLoopCharacteristics', 'multiInstanceLoopCharacteristics'}


def _el(tag, **attrs):
    return ET.Element('{'+BPMN+'}'+tag, {k: v for k, v in attrs.items() if v is not None})


def _flow(identifier, source, target, condition=None):
    flow = _el('sequenceFlow', id=identifier, sourceRef=source, targetRef=target)
    if condition:
        expression = _el('conditionExpression')
        expression.text = condition[:2000]
        flow.append(expression)
    return flow


def _text(element):
    return ' '.join(' '.join(element.itertext()).split())[:200]


def _lower_tasks(process, lowered):
    """Rewrite call activities, task loops and interrupting boundary events into tasks,
    exclusive gateways and rule-guarded flows, which have the same control flow.

    Parallel multi-instance tasks, non-interrupting boundary events and other trigger
    kinds keep different semantics and are refused, never approximated.
    """
    children = list(process)
    flows = [c for c in children if _name(c) == 'sequenceFlow']
    used = {e.get('id') for e in process.iter() if e.get('id')}

    def fresh(base):
        candidate = base[:180] + '__owg'
        index = 1
        while candidate in used:
            index += 1
            candidate = base[:180] + '__owg' + str(index)
        used.add(candidate)
        return candidate

    def outgoing_of(node_id):
        return [f for f in flows if f.get('sourceRef') == node_id]

    for node in children:
        if _name(node) != 'callActivity':
            continue
        extra = [c for c in node if _name(c) not in {'incoming', 'outgoing', 'documentation'}]
        if extra or set(node.attrib) - {'id', 'name', 'calledElement'}:
            raise BPMNImportError('Call activities with data mappings, loops or extensions are unsupported')
        task = _el('task', id=node.get('id'), name=node.get('name'))
        for child in node:
            task.append(child)
        called = (node.get('calledElement') or '').strip()
        note = _el('documentation')
        note.text = 'Calls process ' + (called[:120] or '(unnamed)') + '; the called process is not expanded here.'
        task.append(note)
        process.insert(list(process).index(node), task)
        process.remove(node)
        lowered.append({'kind': 'call_activity', 'source_id': node.get('id', '')})

    for node in list(process):
        if _name(node) not in TASKS:
            continue
        loops = [c for c in node if _name(c) in _LOOP_TAGS]
        if not loops:
            continue
        if len(loops) > 1:
            raise BPMNImportError('A task can declare at most one loop')
        loop = loops[0]
        if _name(loop) == 'multiInstanceLoopCharacteristics' and loop.get('isSequential', 'false').lower() != 'true':
            raise BPMNImportError('Parallel multi-instance tasks run copies at the same time and cannot be imported as a loop')
        if loop.get('testBefore', 'false').lower() == 'true':
            raise BPMNImportError('Loops that test before the first run are unsupported')
        out = outgoing_of(node.get('id'))
        if len(out) != 1 or len(out[0]):
            raise BPMNImportError('A looping task needs exactly one plain outgoing transition')
        detail = ' / '.join(t for t in (_text(c) for c in loop) if t)
        node.remove(loop)
        label = node.get('name') or node.get('id')
        what = 'instance' if _name(loop) == 'multiInstanceLoopCharacteristics' else 'iteration'
        gateway = fresh(node.get('id') + '_loop')
        process.append(_el('exclusiveGateway', id=gateway, name='Repeat ' + label[:50]))
        target = out[0].get('targetRef')
        out[0].set('sourceRef', gateway)
        out[0].append(_el('conditionExpression'))
        out[0][-1].text = 'No further ' + what + ' of ' + label
        process.append(_flow(fresh(node.get('id') + '_to_loop'), node.get('id'), gateway))
        process.append(_flow(fresh(node.get('id') + '_again'), gateway, node.get('id'),
                             'Another ' + what + ' of ' + label + ' is needed' + (' (' + detail + ')' if detail else '')))
        flows = [c for c in process if _name(c) == 'sequenceFlow']
        lowered.append({'kind': 'sequential_multi_instance' if what == 'instance' else 'standard_loop',
                        'source_id': node.get('id', '')})

    boundaries = [c for c in list(process) if _name(c) == 'boundaryEvent']
    by_task: dict[str, list] = {}
    for event in boundaries:
        by_task.setdefault(event.get('attachedToRef', ''), []).append(event)
    tasks = {c.get('id'): c for c in process if _name(c) in TASKS}
    for task_id, events in by_task.items():
        if task_id not in tasks:
            raise BPMNImportError('Boundary events must be attached to a task in the same process')
        out = outgoing_of(task_id)
        if len(out) != 1 or len(out[0]):
            raise BPMNImportError('A task with boundary events needs exactly one plain outgoing transition')
        label = tasks[task_id].get('name') or task_id
        gateway = fresh(task_id + '_outcome')
        process.append(_el('exclusiveGateway', id=gateway, name='Outcome of ' + label[:50]))
        out[0].set('sourceRef', gateway)
        out[0].append(_el('conditionExpression'))
        out[0][-1].text = label + ' completed normally'
        process.append(_flow(fresh(task_id + '_to_outcome'), task_id, gateway))
        for event in events:
            if event.get('cancelActivity', 'true').lower() == 'false':
                raise BPMNImportError('Non-interrupting boundary events run alongside the task and are unsupported')
            definitions = [c for c in event if _name(c) not in {'incoming', 'outgoing', 'documentation'}]
            if len(definitions) != 1 or _name(definitions[0]) not in _BOUNDARY_KINDS:
                raise BPMNImportError('Only interrupting error, timer and escalation boundary events are supported')
            kind = _BOUNDARY_KINDS[_name(definitions[0])]
            event_out = outgoing_of(event.get('id'))
            if len(event_out) != 1 or len(event_out[0]) or any(f.get('targetRef') == event.get('id') for f in flows):
                raise BPMNImportError('A boundary event needs exactly one plain outgoing transition and no incoming one')
            detail = _text(definitions[0]) or definitions[0].get('errorRef') or definitions[0].get('escalationRef') or ''
            name = (event.get('name') or '').strip()
            condition = {'error': 'Error', 'timer': 'Timer', 'escalation': 'Escalation'}[kind] + \
                (' ' + name if name else '') + (' (' + detail + ')' if detail else '') + \
                (' elapsed during ' if kind == 'timer' else ' raised by ') + label
            event_out[0].set('sourceRef', gateway)
            event_out[0].append(_el('conditionExpression'))
            event_out[0][-1].text = condition
            process.remove(event)
            lowered.append({'kind': 'interrupting_' + kind + '_boundary', 'source_id': event.get('id', '')})
        flows = [c for c in process if _name(c) == 'sequenceFlow']


_RESPONSIBILITY_NOTE = ('User-declared responsibility: {}. '
                        'Descriptive only; not verified identity or review/execution authority.')
_DEFINITION_TAGS = {'message', 'error', 'escalation'}


def _note(node, text):
    documentation = _el('documentation')
    documentation.text = text
    node.append(documentation)


def _apply_collaboration(collaboration, process, definitions, applied):
    """One internal process plus black-box pools: message flows become task notes."""
    participants = [c for c in collaboration if _name(c) == 'participant']
    others = [c for c in collaboration if _name(c) not in {'participant', 'messageFlow', 'documentation'}]
    if others:
        raise BPMNImportError('Collaborations may contain only participants, message flows and documentation')
    owners = [p for p in participants if p.get('processRef')]
    if len(owners) != 1 or owners[0].get('processRef') != process.get('id'):
        raise BPMNImportError('A collaboration needs exactly one pool with the imported process; other pools must be black boxes')
    if any(len([c for c in p if _name(c) != 'documentation']) for p in participants):
        raise BPMNImportError('Pool multiplicity and participant extensions are unsupported')
    pools = {p.get('id'): (p.get('name') or 'another party').strip()[:80] for p in participants}
    nodes = {n.get('id'): n for n in process if n.get('id')}
    names = {d.get('id'): (d.get('name') or '').strip()[:80] for d in definitions}
    for flow in (c for c in collaboration if _name(c) == 'messageFlow'):
        source, target = flow.get('sourceRef'), flow.get('targetRef')
        what = (flow.get('name') or names.get(flow.get('messageRef'), '') or 'a message').strip()[:80]
        if source in nodes and target in pools and target != owners[0].get('id'):
            _note(nodes[source], f'Sends {what} to {pools[target]} (message flow; no control-flow effect).')
        elif target in nodes and source in pools and source != owners[0].get('id'):
            _note(nodes[target], f'Receives {what} from {pools[source]} (message flow; no control-flow effect).')
        elif {source, target} <= set(pools):
            continue  # pool-to-pool messages describe the outside world only
        else:
            raise BPMNImportError('Message flows must connect the imported process with another pool')
        applied.append({'kind': 'message_flow'})


def _apply_lanes(process, applied):
    """Lane names become the existing descriptive responsibility labels."""
    lane_sets = [c for c in process if _name(c) == 'laneSet']
    if len(lane_sets) > 1:
        raise BPMNImportError('A process can declare at most one lane set')
    if not lane_sets:
        return
    nodes = {n.get('id'): n for n in process if n.get('id')}

    def walk(lane_set, depth):
        if depth > 3:
            raise BPMNImportError('Lanes may be nested at most three levels deep')
        for lane in lane_set:
            if _name(lane) != 'lane':
                raise BPMNImportError('Lane sets may contain only lanes')
            label = ' '.join((lane.get('name') or '').split())[:120]
            for child in lane:
                kind = _name(child)
                if kind == 'flowNodeRef':
                    node = nodes.get((child.text or '').strip())
                    if node is None:
                        raise BPMNImportError('Lanes must reference nodes of the imported process')
                    already = any(_name(d) == 'documentation' and (d.text or '').startswith('User-declared responsibility:')
                                  for d in node)
                    if label and not already and _name(node) in TASKS | {'exclusiveGateway', 'inclusiveGateway'}:
                        _note(node, _RESPONSIBILITY_NOTE.format(label))
                        applied.append({'kind': 'lane'})
                elif kind == 'childLaneSet':
                    walk(child, depth + 1)
                elif kind != 'documentation':
                    raise BPMNImportError('Lanes may contain only node references and nested lanes')
    walk(lane_sets[0], 1)
    process.remove(lane_sets[0])


def import_bpmn(raw: bytes):
    if not isinstance(raw,bytes) or not raw or len(raw)>MAX_BYTES:
        raise BPMNImportError('BPMN must be a nonempty UTF-8 file of at most 128 KiB')
    try:
        text=raw.decode('utf-8-sig',errors='strict')
    except UnicodeError:
        raise BPMNImportError('BPMN must use UTF-8 encoding') from None
    if '\x00' in text or re.search(r'<!\s*(DOCTYPE|ENTITY)\b',text,re.I):
        raise BPMNImportError('BPMN declarations, entities and invalid control bytes are not supported')
    declaration=re.match(r'\s*<\?xml\s+[^?]*encoding\s*=\s*[\'"]([^\'"]+)',text,re.I)
    if declaration and declaration.group(1).lower() not in {'utf-8','utf8'}:
        raise BPMNImportError('BPMN must declare UTF-8 encoding')
    try:
        root=ET.fromstring(text)
    except (ET.ParseError,ValueError):
        raise BPMNImportError('BPMN XML could not be parsed') from None
    elements=list(root.iter())
    if len(elements)>3000:
        raise BPMNImportError('BPMN exceeds the 3,000 XML-element limit')
    if root.tag!='{'+BPMN+'}definitions':
        raise BPMNImportError('A BPMN 2.0 definitions document is required')
    ids=[element.attrib['id'] for element in elements if 'id' in element.attrib]
    if any(not identifier or len(identifier)>200 for identifier in ids) or len(ids)!=len(set(ids)):
        raise BPMNImportError('BPMN IDs must be unique, nonempty and at most 200 characters')
    processes=[]
    ignored_diagram_count=0
    collaborations,definitions=[],[]
    for child in root:
        if child.tag=='{'+BPMN+'}process':processes.append(child)
        elif child.tag=='{'+BPMN+'}collaboration':collaborations.append(child)
        elif child.tag in {'{'+BPMN+'}'+tag for tag in _DEFINITION_TAGS}:
            if any(_name(c) not in {'documentation','errorCode'} for c in child.iter() if c is not child):
                raise BPMNImportError('Message, error and escalation definitions may not carry payload schemas or extensions')
            definitions.append(child)
        elif child.tag=='{'+BPMN+'}documentation':pass
        elif child.tag=='{http://www.omg.org/spec/BPMN/20100524/DI}BPMNDiagram':
            for node in child.iter():
                namespace=node.tag.partition('}')[0].removeprefix('{')
                if namespace not in DIAGRAM_NAMESPACES:
                    raise BPMNImportError('Diagram metadata contains unsupported elements')
            ignored_diagram_count+=1
        else:
            raise BPMNImportError('Import one process only; collaborations, external imports and executable extensions are unsupported')
    if len(processes)!=1:
        raise BPMNImportError('Import exactly one BPMN process at a time')
    process=copy.deepcopy(processes[0])
    if len(collaborations)>1:
        raise BPMNImportError('Import at most one collaboration')
    applied=[]
    if collaborations:_apply_collaboration(collaborations[0],process,definitions,applied)
    _apply_lanes(process,applied)
    inlined=[]
    _inline_subprocesses(process,inlined)
    lowered=[]
    _lower_tasks(process,lowered)
    nodes,flows,notes=[],[],[]
    manual_by_source, responsibility_by_source=[],{}
    table_by_source={}
    typed_tasks=0
    for child in process:
        tag=_name(child)
        if tag in FLOW_NODES:
            nodes.append(child)
            if tag in TASKS-{'task'}:typed_tasks+=1
            for descendant in child:
                subtag=_name(descendant)
                if subtag not in {'documentation','incoming','outgoing'}:
                    raise BPMNImportError('Event triggers, task implementations and nested process semantics are unsupported')
                if len(descendant):
                    raise BPMNImportError('Nested task/event documentation is unsupported')
                if subtag=='documentation' and (descendant.text or '').strip():
                    note=descendant.text.strip()
                    manual=_MANUAL.fullmatch(note);responsibility=_RESPONSIBILITY.fullmatch(note)
                    if note.startswith('OWG decision table'):
                        if not note.startswith('OWG decision table v1:') or child.get('id','') in table_by_source:
                            raise BPMNImportError('Use one complete supported decision-table declaration per decision')
                        try:
                            table=_TableDocument.model_validate(_json_document(note.partition(':')[2]))
                            if table.decision_activity_id!=child.get('id',''):raise ValueError('decision differs')
                        except (ValueError,TypeError,RecursionError):
                            raise BPMNImportError('Decision-table documentation must use the complete bounded supported format') from None
                        table_by_source[child.get('id','')]=table
                    elif manual:
                        manual_by_source.append({'source_id':child.get('id',''),'kind':manual.group(1),
                            'condition_label':manual.group(2),'response':manual.group(3)})
                    elif responsibility:
                        if child.get('id','') in responsibility_by_source:
                            raise BPMNImportError('Each activity may declare at most one responsibility label')
                        responsibility_by_source[child.get('id','')]=responsibility.group(1)
                    elif note.startswith(('UNEVALUATED manual review (','User-declared responsibility: ')):
                        raise BPMNImportError('Recognized OWG review/responsibility documentation must use its complete supported format')
                    else:
                        notes.append({'source_node_id':child.get('id',''),'text':note[:2000],'text_truncated':len(note)>2000})
        elif tag=='sequenceFlow':
            flows.append(child)
            for descendant in child:
                if _name(descendant) not in {'documentation','conditionExpression'} or len(descendant):
                    raise BPMNImportError('Sequence flow contains unsupported execution semantics')
        elif tag=='documentation':
            if len(child):raise BPMNImportError('Nested process documentation is unsupported')
            if (child.text or '').strip():
                note=child.text.strip()
                notes.append({'source_node_id':'','text':note[:2000],'text_truncated':len(note)>2000})
        else:
            raise BPMNImportError('Only tasks, plain events and supported exclusive/parallel gateways are allowed; other process semantics cannot be flattened')
    if not 1<=len(nodes)<=40 or len(flows)>100:
        raise BPMNImportError('Imported designs support 1–40 activities and at most 100 transitions')
    if len(notes)>40:
        raise BPMNImportError('Imported designs support at most 40 documentation notes')
    node_ids=[node.get('id') for node in nodes]
    if any(not identifier for identifier in node_ids):
        raise BPMNImportError('Every activity and gateway needs an explicit BPMN ID')
    known=set(node_ids)
    incoming,outgoing=Counter(),Counter()
    for flow in flows:
        source,target=flow.get('sourceRef'),flow.get('targetRef')
        if source not in known or target not in known:
            raise BPMNImportError('Every transition must reference activities within the selected process')
        incoming[target]+=1;outgoing[source]+=1
    starts=[node.get('id') for node in nodes if _name(node)=='startEvent']
    entries=starts or [identifier for identifier in node_ids if not incoming[identifier]]
    if len(entries)!=1:
        raise BPMNImportError('An imported design needs one unambiguous structural entry')
    terminals=[identifier for identifier in node_ids if not outgoing[identifier]]
    # Positional graph identifiers cannot become guessable hashes of a source
    # XML ID containing a customer/native record key. Original IDs are returned
    # only in the ephemeral inspection mapping, never inside the saved model.
    mapping={identifier:'import_step_'+str(index) for index,identifier in enumerate(node_ids,1)}
    activities=[]
    label_corrections=[]
    for index,node in enumerate(nodes,1):
        tag=_name(node);identifier=node.get('id');label=(node.get('name') or '').strip()
        if not _LABEL.fullmatch(label):
            label_corrections.append({'activity_id':mapping[identifier],'source_label':label[:240],
                                      'source_label_truncated':len(label)>240})
            label='Imported step '+str(index)
        kind=('decision' if outgoing[identifier]>1 else 'routing') if tag=='exclusiveGateway' else {
            'startEvent':'start','endEvent':'end'}.get(tag,'activity')
        if tag=='parallelGateway':
            if incoming[identifier]<=1 and outgoing[identifier]>=2:kind='parallel_split';direction='Diverging'
            elif incoming[identifier]>=2 and outgoing[identifier]==1:kind='parallel_join';direction='Converging'
            else:raise BPMNImportError('Parallel gateways must be distinct explicit splits or joins')
            if 'default' in node.attrib or node.get('gatewayDirection','Unspecified') not in {'Unspecified',direction}:
                raise BPMNImportError('Parallel gateway direction/default contradicts its unconditional topology')
        if tag=='inclusiveGateway':
            if incoming[identifier]<=1 and outgoing[identifier]>=2:kind='inclusive_split';direction='Diverging'
            elif incoming[identifier]>=2 and outgoing[identifier]==1:kind='inclusive_join';direction='Converging'
            else:raise BPMNImportError('Inclusive gateways must be distinct explicit splits or joins')
            if node.get('gatewayDirection','Unspecified') not in {'Unspecified',direction}:
                raise BPMNImportError('Inclusive gateway direction contradicts its topology')
            if 'default' in node.attrib and kind!='inclusive_split':
                raise BPMNImportError('Only an inclusive split can declare a default branch')
        if tag not in {'exclusiveGateway','parallelGateway','inclusiveGateway'} and outgoing[identifier]>1:
            raise BPMNImportError('Implicit task branching is unsupported; use an explicit exclusive gateway')
        if tag=='startEvent' and not outgoing[identifier]:
            raise BPMNImportError('Start events require an outgoing transition')
        activity={'id':mapping[identifier],'label':label,'kind':kind,'implementation':'unmapped'}
        if identifier in responsibility_by_source:activity['responsibility_label']=responsibility_by_source[identifier]
        activities.append(activity)
    source_kinds={identifier:activity['kind'] for identifier,activity in zip(node_ids,activities)}
    raw_out={identifier:[] for identifier in node_ids}
    for flow in flows:raw_out[flow.get('sourceRef')].append(flow.get('targetRef'))
    parallel_sources=set()
    for identifier,kind in source_kinds.items():
        if kind not in {'parallel_split','inclusive_split'}:continue
        join_kind=kind.replace('_split','_join')
        # An inclusive split's own branches carry rules; flows inside its branches do not.
        pending=list(raw_out[identifier]) if kind=='inclusive_split' else [identifier];seen=set()
        while pending:
            cursor=pending.pop()
            if cursor in seen:continue
            seen.add(cursor)
            if source_kinds[cursor]==join_kind:continue
            parallel_sources.add(cursor);pending.extend(raw_out[cursor])
    transitions,rules=[],[]
    for index,flow in enumerate(flows,1):
        source,target=flow.get('sourceRef'),flow.get('targetRef')
        expressions=[(child.text or '').strip() for child in flow if _name(child)=='conditionExpression']
        docs=[];predicate=None
        for child in flow:
            if _name(child)!='documentation':continue
            text=(child.text or '').strip()
            if text.startswith('OWG declared predicate v1:'):
                if predicate is not None:raise BPMNImportError('A flow may declare at most one structured predicate')
                try:predicate=RulePredicate.model_validate(_json_document(text.partition(':')[2]))
                except (ValueError,TypeError,RecursionError):raise BPMNImportError('Structured predicate documentation must use the complete bounded supported format') from None
            else:docs.append(text)
        if len(expressions)>1:
            raise BPMNImportError('A sequence flow may contain at most one condition expression')
        branch=outgoing[source]>1
        condition=next((value for value in expressions+docs+[flow.get('name','').strip()] if value),'')
        edge={'source':mapping[source],'target':mapping[target]}
        if source in parallel_sources or source_kinds[source] in {'parallel_join','inclusive_join'}:
            if expressions or predicate is not None:
                raise BPMNImportError('Parallel gateway flows cannot have conditional expressions or predicates')
            if condition:notes.append({'source_node_id':source,'text':condition[:2000],'text_truncated':len(condition)>2000})
        elif branch or condition or predicate is not None:
            default_of_source=next((n.get('default') for n in nodes if n.get('id')==source),None)
            if not condition and default_of_source==flow.get('id') and source_kinds[source]=='inclusive_split':
                condition='Otherwise (default branch when no other condition applies)'
            rule_text=condition or 'Clarify the business condition for imported branch '+str(index)
            if len(rule_text)>2000:
                raise BPMNImportError('Imported branch descriptions must be at most 2,000 characters')
            rule_id='import_rule_'+str(index)
            rule={'id':rule_id,'text':rule_text,'user_confirmed':False}
            if predicate is not None:rule['predicate']=predicate.model_dump()
            rules.append(rule);edge['rule_id']=rule_id
        transitions.append(edge)
    tables=[]
    flow_map={flow.get('id'):edge for flow,edge in zip(flows,transitions)}
    for source,document in table_by_source.items():
        value=document.model_dump(mode='json');value['decision_activity_id']=mapping[source];value['user_confirmed']=False
        rows=[]
        for row in document.rows:
            edge=flow_map.get(row.flow_id)
            if edge is None or edge['source']!=mapping[source] or not edge.get('rule_id'):
                raise BPMNImportError('Decision-table rows must reference outgoing conditional flows of their decision')
            rows.append({'id':row.id,'rule_id':edge['rule_id'],'target_activity_id':edge['target']})
        value['rows']=rows;tables.append(value)
    if len(notes)>40:raise BPMNImportError('Imported designs support at most 40 documentation notes')
    manual_conditions=[{**{key:value for key,value in item.items() if key!='source_id'},
                        'activity_id':mapping[item['source_id']]} for item in manual_by_source]
    if notes or label_corrections:
        # Unstructured documentation/placeholder labels must not disappear and
        # silently produce a planner-ready graph. This unevaluated guard remains
        # in the saved model until a human explicitly resolves the source gaps.
        guard={'activity_id':mapping[entries[0]],'kind':'exception_condition',
            'condition_label':'Review original BPMN documentation and labels','response':'pause_for_review'}
        if guard not in manual_conditions:manual_conditions.append(guard)
    try:
        model=ProcessModel(goal='Review imported process design',baseline_source='user_declared',
            baseline_coverage='user_declared_design',baseline_path=['Imported BPMN design - no observed execution'],
            supporting_case_count=0,activities=activities,transitions=transitions,business_rules=rules,
            manual_review_conditions=manual_conditions,decision_tables=tables,
            entry_activity_id=mapping[entries[0]],terminal_activity_ids=[mapping[t] for t in terminals])
    except ValidationError:
        raise BPMNImportError('Imported graph must be connected, reachable, bounded and have valid terminal routes and supported exclusive or paired parallel branches') from None
    warnings=['source_xml_is_untrusted_data','no_observed_execution_or_policy_imported',
              'rules_require_human_confirmation','task_implementations_and_permissions_not_imported']
    if any(kind in {'parallel_split','parallel_join'} for kind in source_kinds.values()):
        warnings.append('parallel_semantics_preserved_sdk_uses_serial_token_valid_schedule')
    if any(kind in {'inclusive_split','inclusive_join'} for kind in source_kinds.values()):
        warnings.append('inclusive_branches_run_when_their_unconfirmed_rules_apply')
    if tables:warnings.append('decision_tables_retained_as_unconfirmed_declarations_not_dmn_execution')
    if inlined:warnings.append('embedded_subprocesses_inlined_grouping_not_saved_in_model')
    if any(item['kind']=='message_flow' for item in applied):warnings.append('message_flows_recorded_as_task_notes_without_control_flow')
    if any(item['kind']=='lane' for item in applied):warnings.append('lane_names_imported_as_descriptive_responsibility_labels')
    warnings.extend(sorted({{'call_activity':'call_activities_imported_as_single_steps_called_process_not_expanded',
        'standard_loop':'task_loops_imported_as_exclusive_repeat_choices',
        'sequential_multi_instance':'sequential_multi_instance_imported_as_exclusive_repeat_choices'}.get(
        item['kind'],'interrupting_boundary_events_imported_as_exclusive_outcome_choices') for item in lowered}))
    if label_corrections:warnings.append('unsupported_or_missing_labels_use_placeholders')
    if notes:warnings.append('documentation_requires_separate_manual_review_and_is_not_saved_with_the_model')
    if manual_conditions:warnings.append('unevaluated_manual_review_guards_retained_in_the_model')
    if typed_tasks:warnings.append('typed_task_execution_semantics_not_imported')
    if ignored_diagram_count:warnings.append('diagram_layout_recomputed_by_owg')
    if process.get('isExecutable','false').lower()=='true':warnings.append('source_executable_flag_does_not_authorize_execution')
    return {'format':'openworkgraph.bpmn-draft-import.v1','process_model':model.model_dump(),
        'source_sha256':sha256(raw).hexdigest(),'source_node_ids':mapping,
        'documentation_notes':notes,'placeholder_label_count':len(label_corrections),
        'lowered_constructs':[{'kind':item['kind']} for item in lowered][:40],
        'message_flow_count':sum(item['kind']=='message_flow' for item in applied),
        'lane_responsibility_count':sum(item['kind']=='lane' for item in applied),
        'inlined_subprocesses':[{'label':label if _LABEL.fullmatch(label) else '',
            'activity_ids':[mapping[member] for member in members if member in mapping]}
            for _,label,members in inlined],
        'label_corrections':label_corrections,
        'retained_manual_review_condition_count':len(model.manual_review_conditions),
        'retained_responsibility_label_count':len(responsibility_by_source),
        'warnings':warnings,'import_retained':False,'process_model_saved':False,
        'business_rules_evaluated':False,'execution_authorized':False,'business_outcomes_verified':False}
