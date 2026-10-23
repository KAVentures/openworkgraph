"""Bounded descriptive BPMN import. XML is data, never an execution policy.

Support serial/exclusive choice and explicit disjoint linear parallel regions.
Nested parallel, inclusive, subprocess and event-driven semantics must not be
flattened into an incorrect sequential model. No XML resolution, provider calls or persistence.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import re
import json
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from .process_model import ProcessModel
from .process_rule_conditions import RulePredicate

MAX_BYTES = 128 * 1024
BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DIAGRAM_NAMESPACES = {
    'http://www.omg.org/spec/BPMN/20100524/DI',
    'http://www.omg.org/spec/DD/20100524/DC',
    'http://www.omg.org/spec/DD/20100524/DI',
}
TASKS = {'task','userTask','manualTask','serviceTask','sendTask','receiveTask','businessRuleTask'}
FLOW_NODES = TASKS | {'startEvent','endEvent','exclusiveGateway','parallelGateway'}
_LABEL = re.compile(r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$')
_MANUAL = re.compile(r'^UNEVALUATED manual review \((decision_point|exception_condition)\): '
    r'([A-Za-z][A-Za-z0-9 _-]{0,63}); response: '
    r'(pause_for_review|request_source_recheck|escalate_to_owner)\. No automatic branch is authorized\.$')
_RESPONSIBILITY = re.compile(r'^User-declared responsibility: (.+)\. '
    r'Descriptive only; not verified identity or review/execution authority\.$')


class BPMNImportError(ValueError):
    """Fixed safe errors must never echo source XML or annotation payloads."""


def _name(element):
    if not element.tag.startswith('{'+BPMN+'}'):
        raise BPMNImportError('Unsupported element namespace in the process definition')
    return element.tag[len(BPMN)+2:]


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
    for child in root:
        if child.tag=='{'+BPMN+'}process':processes.append(child)
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
    process=processes[0]
    nodes,flows,notes=[],[],[]
    manual_by_source, responsibility_by_source=[],{}
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
                    if manual:
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
        if tag not in {'exclusiveGateway','parallelGateway'} and outgoing[identifier]>1:
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
        if kind!='parallel_split':continue
        pending=[identifier];seen=set()
        while pending:
            cursor=pending.pop()
            if cursor in seen:continue
            seen.add(cursor)
            if source_kinds[cursor]=='parallel_join':continue
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
                def pairs(items):
                    values={}
                    for key,value in items:
                        if key in values:raise ValueError('duplicate field')
                        values[key]=value
                    return values
                def invalid(_value):raise ValueError('nonstandard JSON')
                try:predicate=RulePredicate.model_validate(json.loads(text.partition(':')[2],object_pairs_hook=pairs,parse_constant=invalid))
                except (ValueError,TypeError,RecursionError):raise BPMNImportError('Structured predicate documentation must use the complete bounded supported format') from None
            else:docs.append(text)
        if len(expressions)>1:
            raise BPMNImportError('A sequence flow may contain at most one condition expression')
        branch=outgoing[source]>1
        condition=next((value for value in expressions+docs+[flow.get('name','').strip()] if value),'')
        edge={'source':mapping[source],'target':mapping[target]}
        if source in parallel_sources or source_kinds[source]=='parallel_join':
            if expressions or predicate is not None:
                raise BPMNImportError('Parallel gateway flows cannot have conditional expressions or predicates')
            if condition:notes.append({'source_node_id':source,'text':condition[:2000],'text_truncated':len(condition)>2000})
        elif branch or condition or predicate is not None:
            rule_text=condition or 'Clarify the business condition for imported branch '+str(index)
            if len(rule_text)>2000:
                raise BPMNImportError('Imported branch descriptions must be at most 2,000 characters')
            rule_id='import_rule_'+str(index)
            rule={'id':rule_id,'text':rule_text,'user_confirmed':False}
            if predicate is not None:rule['predicate']=predicate.model_dump()
            rules.append(rule);edge['rule_id']=rule_id
        transitions.append(edge)
    if len(notes)>40:raise BPMNImportError('Imported designs support at most 40 documentation notes')
    manual_conditions=[{**{key:value for key,value in item.items() if key!='source_id'},
                        'activity_id':mapping[item['source_id']]} for item in manual_by_source]
    if notes or label_corrections:
        # Unstructured documentation/placeholder labels must not disappear and
        # silently produce a planner-ready graph. This unevaluated guard remains
        # in the saved model until a human explicitly resolves the source gaps.
        manual_conditions.append({'activity_id':mapping[entries[0]],'kind':'exception_condition',
            'condition_label':'Review original BPMN documentation and labels','response':'pause_for_review'})
    try:
        model=ProcessModel(goal='Review imported process design',baseline_source='user_declared',
            baseline_coverage='user_declared_design',baseline_path=['Imported BPMN design - no observed execution'],
            supporting_case_count=0,activities=activities,transitions=transitions,business_rules=rules,
            manual_review_conditions=manual_conditions,
            entry_activity_id=mapping[entries[0]],terminal_activity_ids=[mapping[t] for t in terminals])
    except ValidationError:
        raise BPMNImportError('Imported graph must be connected, reachable, bounded and have valid terminal routes and supported exclusive or paired parallel branches') from None
    warnings=['source_xml_is_untrusted_data','no_observed_execution_or_policy_imported',
              'rules_require_human_confirmation','task_implementations_and_permissions_not_imported']
    if any(kind in {'parallel_split','parallel_join'} for kind in source_kinds.values()):
        warnings.append('parallel_semantics_preserved_linear_agent_runtime_unavailable')
    if label_corrections:warnings.append('unsupported_or_missing_labels_use_placeholders')
    if notes:warnings.append('documentation_requires_separate_manual_review_and_is_not_saved_with_the_model')
    if manual_conditions:warnings.append('unevaluated_manual_review_guards_retained_in_the_model')
    if typed_tasks:warnings.append('typed_task_execution_semantics_not_imported')
    if ignored_diagram_count:warnings.append('diagram_layout_recomputed_by_owg')
    if process.get('isExecutable','false').lower()=='true':warnings.append('source_executable_flag_does_not_authorize_execution')
    return {'format':'openworkgraph.bpmn-draft-import.v1','process_model':model.model_dump(),
        'source_sha256':sha256(raw).hexdigest(),'source_node_ids':mapping,
        'documentation_notes':notes,'placeholder_label_count':len(label_corrections),
        'label_corrections':label_corrections,
        'retained_manual_review_condition_count':len(model.manual_review_conditions),
        'retained_responsibility_label_count':len(responsibility_by_source),
        'warnings':warnings,'import_retained':False,'process_model_saved':False,
        'business_rules_evaluated':False,'execution_authorized':False,'business_outcomes_verified':False}
