"""Process catalog on the existing owner-scoped, append-only knowledge store."""
from __future__ import annotations
import json

from typing import Annotated, Any
from uuid import uuid4
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .process_model import ProcessModel
from .workflow_knowledge import KnowledgeStore, KnowledgeWrite
from .process_diagram import add_process_diagram

ProcessID = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')]


class CatalogWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workflow_id: ProcessID | None = None
    title: str = Field(min_length=1, max_length=240)
    expected_revision: Annotated[StrictInt, Field(ge=0)] = 0
    user_confirmed: StrictBool = False
    process_model: ProcessModel
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class ProcessCatalog:
    def __init__(self, store: KnowledgeStore, owner: str):
        self.store, self.owner = store, owner

    def save(self, request: CatalogWrite) -> dict[str, Any]:
        model = request.process_model
        workflow_id = request.workflow_id or 'process_' + uuid4().hex
        if request.workflow_id:
            current = self.store.list(self.owner, workflow_id)['workflows']
            if current and not current[0].get('process_model'):
                raise ValueError('This ID belongs to workflow knowledge without a process model')
        return self.store.save(self.owner, KnowledgeWrite(
            workflow_id=workflow_id, title=request.title.strip(),
            procedure='User-reviewed process design: ' + model.goal,
            process_model=model, source_client='OWG local process designer',
            decision_rules=[r.text for r in model.business_rules if r.user_confirmed],
            unresolved_questions=[r.text for r in model.business_rules if not r.user_confirmed],
            evidence_refs=request.evidence_refs,
            expected_revision=request.expected_revision, user_confirmed=request.user_confirmed,
        ))

    def get(self, workflow_id: str, revision: int | None = None) -> dict[str, Any]:
        result = self.store.history(self.owner, workflow_id, revision=revision, limit=1)['workflows']
        if not result or not result[0].get('process_model'):
            raise LookupError('Process revision not found')
        return result[0]

    def opportunities(self, currency: str) -> dict[str, Any]:
        from .process_opportunities import OpportunityScenario
        after, rows, scanned, missing, more = '', [], 0, 0, False
        # A bounded local catalog scan, never a source-system inventory scan.
        for _ in range(10):
            page = self.store.page(self.owner, after=after, limit=100)
            scanned += len(page['workflows'])
            for record in page['workflows']:
                model = record.get('process_model')
                if not model:
                    continue
                scenario = model.get('opportunity_scenario')
                if not scenario:
                    missing += 1
                    continue
                assumption = OpportunityScenario.model_validate(scenario)
                if assumption.currency != currency:
                    continue
                rows.append({key: record[key] for key in ('workflow_id', 'title', 'revision')} | {
                    'declared_owner': model['owner_label'],
                    'selected_baseline_supporting_count': model['supporting_case_count'],
                    'baseline_support_unit': model.get('baseline_support_unit', 'declared_cases'),
                    'baseline_count_is_declared_not_verified_monthly_volume': True,
                    'unconfirmed_rule_count': sum(not r['user_confirmed'] for r in model['business_rules']),
                    'manual_review_condition_count': len(model.get('manual_review_conditions', [])),
                    'unmapped_activity_count': sum(a['kind'] == 'activity' and a['implementation'] == 'unmapped' for a in model['activities']),
                    'assumptions': assumption.model_dump(), 'projection': assumption.projection(),
                })
            more = page['has_more']
            if not more:
                break
            after = page['next_after']
        rows.sort(key=lambda row: (-row['projection']['net_monthly_value_before_other_costs'], row['workflow_id']))
        return {'currency': currency, 'opportunities': rows[:100],
                'knowledge_records_scanned': scanned, 'catalog_scan_truncated': more,
                'ranked_results_truncated': len(rows) > 100,
                'processes_without_scenarios_in_scanned_records': missing,
                'ranking_basis': 'declared_net_monthly_scenario_within_selected_currency',
                'labor_savings_measured': False, 'execution_authorized': False}

    def export(self, workflow_id: str, revision: int) -> dict[str, Any]:
        record = self.get(workflow_id, revision)
        return {
            'format': 'openworkgraph.versioned-process-export.v1',
            'workflow_id': record['workflow_id'], 'revision': record['revision'],
            'content_sha256': record['content_sha256'], 'title': record['title'],
            'saved_at': record['saved_at'], 'process_model': record['process_model'],
            'evidence_refs': record['evidence_refs'],
            'review_basis': record['review_basis'], 'organization_policy': False,
            'execution_authorization': False, 'business_outcomes_verified': False,
        }


def process_bpmn(packet: dict[str, Any]) -> str:
    model = ProcessModel.model_validate(packet['process_model'])
    ns = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
    ET.register_namespace('bpmn', ns)
    root = ET.Element(f'{{{ns}}}definitions', {
        'id': 'Definitions_OWG', 'targetNamespace': 'urn:openworkgraph:process-model',
    })
    process = ET.SubElement(root, f'{{{ns}}}process', {'id': 'Process_OWG', 'isExecutable': 'false'})
    ET.SubElement(process, f'{{{ns}}}documentation').text = (
        f"Reviewed design {packet['workflow_id']} revision {packet['revision']} "
        f"sha256 {packet['content_sha256']}; not policy or execution authority."
    )
    tags = {'activity': 'task', 'decision': 'exclusiveGateway', 'routing': 'exclusiveGateway', 'start': 'startEvent', 'end': 'endEvent', 'parallel_split':'parallelGateway', 'parallel_join':'parallelGateway'}
    for activity in model.activities:
        node = ET.SubElement(process, f'{{{ns}}}{tags[activity.kind]}', {'id': 'Activity_' + activity.id, 'name': activity.label})
        if activity.kind in {'parallel_split','parallel_join'}:
            node.set('gatewayDirection','Diverging' if activity.kind=='parallel_split' else 'Converging')
        if activity.responsibility_label:
            ET.SubElement(node,f'{{{ns}}}documentation').text=(
                'User-declared responsibility: '+activity.responsibility_label+
                '. Descriptive only; not verified identity or review/execution authority.')
        for table in model.decision_tables:
            if table.decision_activity_id == activity.id:
                declaration=table.model_dump(mode='json')
                declaration['decision_activity_id']='Activity_'+activity.id
                declaration['rows']=[{'id':row.id,'flow_id':'Flow_'+str(next(
                    i for i,e in enumerate(model.transitions,1)
                    if e.source==activity.id and e.rule_id==row.rule_id and e.target==row.target_activity_id))}
                    for row in table.rows]
                ET.SubElement(node,f'{{{ns}}}documentation').text='OWG decision table v1: '+json.dumps(
                    declaration,sort_keys=True,ensure_ascii=True,allow_nan=False)
        for condition in model.manual_review_conditions:
            if condition.activity_id == activity.id:
                ET.SubElement(node, f'{{{ns}}}documentation').text = (
                    f'UNEVALUATED manual review ({condition.kind}): {condition.condition_label}; '
                    f'response: {condition.response}. No automatic branch is authorized.'
                )
    rules = {r.id: r for r in model.business_rules}
    for index, edge in enumerate(model.transitions, 1):
        flow = ET.SubElement(process, f'{{{ns}}}sequenceFlow', {
            'id': f'Flow_{index}', 'sourceRef': 'Activity_' + edge.source, 'targetRef': 'Activity_' + edge.target,
        })
        if edge.rule_id:
            rule = rules[edge.rule_id]
            # Declarative documentation only; no expression language or execution.
            ET.SubElement(flow, f'{{{ns}}}documentation').text = (
                f'User-confirmed={rule.user_confirmed}: {rule.text}'
            )
            if rule.predicate is not None:
                ET.SubElement(flow,f'{{{ns}}}documentation').text='OWG declared predicate v1: '+json.dumps(
                    rule.predicate.model_dump(),sort_keys=True,ensure_ascii=True,allow_nan=False)
    add_process_diagram(root, process, entry_id='Activity_' + model.entry_activity_id)
    return ET.tostring(root, encoding='unicode') + '\n'


def process_document(packet: dict[str, Any]) -> str:
    """Plain text PDD; user-entered text never becomes agent instructions or code."""
    model = ProcessModel.model_validate(packet['process_model'])
    lines = [packet['title'], f"Process: {packet['workflow_id']} · revision {packet['revision']}",
             'SHA256: ' + packet['content_sha256'],
             'User-reviewed design. Not approved policy or authorization to execute.',
             '', 'Declared goal: ' + model.goal, 'Declared owner: ' + model.owner_label,
             '', ('Imported declared design - no observed baseline' if model.baseline_coverage=='user_declared_design'
                   else 'AS-IS observed baseline (' + model.baseline_source + '):'),
             ' → '.join(model.baseline_path), 'Coverage: ' + model.baseline_coverage,
             f'Support: {model.supporting_case_count} {model.baseline_support_unit} (not verified case identity)',
             '', 'TO-BE proposed activities:']
    lines += [f'{a.id}: {a.label} ({a.kind}; suggested implementation: {a.implementation})'+
              (f' · Declared responsibility: {a.responsibility_label}' if a.responsibility_label else '') for a in model.activities]
    if any(a.responsibility_label for a in model.activities):
        lines.append('Responsibility labels are user declarations, not verified identity or permission grants.')
    lines += ['', 'Proposed transitions:'] + [f'{e.source} → {e.target}; rule: {e.rule_id or "none"}' for e in model.transitions]
    lines += ['', 'Declared rules (not independently validated):'] + [f'{r.id}: {r.text} (user-confirmed={r.user_confirmed})' for r in model.business_rules]
    if any(rule.predicate is not None for rule in model.business_rules):
        lines+=['','Structured declared predicates (not policy or automatic branching):']+[
            rule.id+': '+json.dumps(rule.predicate.model_dump(),sort_keys=True,ensure_ascii=True,allow_nan=False)
            for rule in model.business_rules if rule.predicate is not None]
    if model.decision_tables:
        ordered=any(t.hit_policy=='FIRST' for t in model.decision_tables)
        lines+=['','Declared '+('UNIQUE/FIRST' if ordered else 'UNIQUE')+' decision tables (not DMN, policy certification or execution authority):',
                'Inclusive effective start; exclusive end. A supplied-case match is not exhaustive coverage.',
                ('FIRST uses declared row order; an unresolved earlier row withholds later matches. Agent projects require separate reviewed FIRST precedence and current-source bindings.' if ordered else
                 'Agent projects require separately reviewed current-source UNIQUE bindings and UTC effective dates.')]+[
            json.dumps(table.model_dump(mode='json'),sort_keys=True,ensure_ascii=True,allow_nan=False)
            for table in model.decision_tables]
    lines += ['', 'Unverified manual review conditions (not evaluated or executable):'] + [
        f'{c.activity_id}: {c.kind} · {c.condition_label}; response: {c.response}' for c in model.manual_review_conditions]
    lines += ['', 'Declared source-state expectations:'] + [f'{e.terminal_activity_id}: {e.operation} (user-confirmed={e.user_confirmed})' for e in model.outcome_expectations]
    lines += ['', 'Unknowns: authoritative case identity, business authorization, exception handling,',
              'tool mappings and independently checked success must be validated for deployment.']
    return '\n'.join(lines) + '\n'
