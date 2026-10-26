"""Decision-table boundary cases, competing unknowns, provenance and export isolation."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shared.process_decision_tables import evaluate_table
from shared.process_model import ProcessModel
from shared.process_rule_checks import RuleCase, RuleCaseBatch, check_case_rules
from shared.process_catalog import process_bpmn, process_document
from shared.process_bpmn_import import import_bpmn, BPMNImportError
from shared.process_agent_project import build_runtime_spec, build_runtime_project, process_model_digest
from test_process_collaboration import store, person, app, review, grant
from gateway.process_collaboration import SharedProcessWrite, ProcessCollaborationError


def graph():
    return {'goal':'Review an authorized purchase', 'baseline_source':'user_declared',
        'baseline_path':['Review','Accept'], 'baseline_coverage':'user_declared_design',
        'activities':[{'id':'choose','label':'Review','kind':'decision'},
                      {'id':'accept','label':'Accept'}, {'id':'escalate','label':'Escalate'}],
        'transitions':[{'source':'choose','target':'accept','rule_id':'small'},
                       {'source':'choose','target':'escalate','rule_id':'large'}],
        'entry_activity_id':'choose','terminal_activity_ids':['accept','escalate'],
        'business_rules':[{'id':'small','text':'Small purchase','user_confirmed':True,
             'predicate':{'conditions':[{'path':'/amount','operator':'lt','expected':100}]}},
            {'id':'large','text':'Large purchase','user_confirmed':True,
             'predicate':{'conditions':[{'path':'/amount','operator':'gte','expected':100}]}}],
        'decision_tables':[{'id':'purchase','decision_activity_id':'choose',
            'effective_from':'2026-01-01','effective_until':'2027-01-01',
            'source_basis':{'label':'Approved policy reference','document_sha256':'a'*64},
            'rows':[{'id':'low','rule_id':'small','target_activity_id':'accept'},
                    {'id':'high','rule_id':'large','target_activity_id':'escalate'}],
            'user_confirmed':True}]}


def record(value=None):
    return {'format':'openworkgraph.versioned-process-export.v1','workflow_id':'purchase_process',
        'revision':1,'content_sha256':'b'*64,'title':'Purchase design','execution_authorization':False,
        'process_model':ProcessModel.model_validate(value or graph()).model_dump()}


def check(facts, value=None, when='2026-10-26'):
    model=ProcessModel.model_validate(value or graph())
    return evaluate_table(model.decision_tables[0],{r.id:r for r in model.business_rules},facts,when)


@pytest.mark.parametrize('amount,target',[(0,'accept'),(99.999,'accept'),(100,'escalate'),(100.001,'escalate'),(10**15,'escalate')])
def test_independent_purchase_boundary_oracle(amount,target):
    result=check({'amount':amount,'PRIVATE_UNUSED':'PRIVATE_FACT'})
    assert result['status']=='unique_match' and result['selected_target_activity_id']==target
    assert 'PRIVATE_' not in json.dumps(result)
    assert not result['facts_authoritativeness_verified'] and not result['execution_authorized']


@pytest.mark.parametrize('facts,status', [({},'unknown'),({'amount':None},'unknown'),({'amount':'100'},'unknown'),({'amount':True},'unknown')])
def test_missing_and_wrong_scalar_types_never_choose(facts,status):
    result=check(facts)
    assert result['status']==status and result['selected_target_activity_id'] is None


def test_unknown_competitor_withholds_known_match_and_overlap_is_not_first_row_wins():
    g=graph();g['business_rules'][1]['predicate']['conditions'][0]['path']='/other_amount'
    assert check({'amount':50},g)['status']=='unknown'
    overlap=check({'amount':50,'other_amount':200},g)
    assert overlap['status']=='overlap' and overlap['selected_target_activity_id'] is None
    g['decision_tables'][0]['rows'].reverse()
    assert check({'amount':50,'other_amount':200},g)['status']=='overlap'
    assert check({'amount':200,'other_amount':50},g)['status']=='gap'


@pytest.mark.parametrize('when,status',[(None,'date_required'),('2025-12-31','not_yet_effective'),
    ('2026-01-01','unique_match'),('2026-12-31','unique_match'),('2027-01-01','expired')])
def test_explicit_date_start_inclusive_end_exclusive_no_wall_clock(when,status):
    result=check({'amount':50},when=when)
    assert result['status']==status
    if status!='unique_match':assert result['selected_target_activity_id'] is None and result['rows']==[]


def test_open_end_unconfirmed_table_and_rules_are_not_implicit_authority():
    g=graph();g['decision_tables'][0]['effective_until']=None
    assert check({'amount':50},g,'2099-01-01')['status']=='unique_match'
    g['decision_tables'][0]['user_confirmed']=False
    assert check({'amount':50},g)['status']=='unconfirmed'
    g['business_rules'][0]['user_confirmed']=False
    assert check({'amount':50},g)['status']=='unconfirmed'
    g['decision_tables'][0]['user_confirmed']=True
    with pytest.raises(ValidationError,match='Confirm each'):ProcessModel.model_validate(g)


@pytest.mark.parametrize('change',[
    lambda g:g['decision_tables'][0].update(hit_policy='FIRST'),
    lambda g:g['decision_tables'][0].update(effective_from='2026-02-30'),
    lambda g:g['decision_tables'][0].update(effective_from='2026-1-1'),
    lambda g:g['decision_tables'][0].update(effective_until='2026-01-01'),
    lambda g:g['decision_tables'][0].update(execution_authorized=True),
    lambda g:g['decision_tables'][0].update(user_confirmed=1),
    lambda g:g['decision_tables'][0]['source_basis'].update(document_sha256='not_a_digest'),
    lambda g:g['decision_tables'][0]['source_basis'].update(independently_verified=True),
    lambda g:g['decision_tables'][0]['source_basis'].update(label='PRIVATE\nSOURCE'),
    lambda g:g['decision_tables'][0]['rows'][1].update(rule_id='small'),
    lambda g:g['decision_tables'][0]['rows'][1].update(target_activity_id='accept'),
    lambda g:g['decision_tables'][0]['rows'][1].update(target_activity_id='missing'),
    lambda g:g['decision_tables'][0].update(decision_activity_id='accept'),
    lambda g:g['decision_tables'].append(deepcopy(g['decision_tables'][0])),
    lambda g:g['business_rules'][0].pop('predicate'),
])
def test_malformed_misbound_or_executable_declarations_refused(change):
    g=graph();change(g)
    with pytest.raises(ValidationError):ProcessModel.model_validate(g)


def test_exact_model_and_spec_hash_compatibility_without_tables():
    from test_process_rule_conditions import graph as legacy_graph, packet
    model=ProcessModel.model_validate(legacy_graph())
    assert 'decision_tables' not in model.model_dump()
    assert process_model_digest(model.model_dump())=='b885456de37894d281ea2a1d79a3b5cbaed2abcb448bffcfd074971a0a86e0e5'
    spec=build_runtime_spec(packet(model.model_dump()))
    assert hashlib.sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()=='9e7efd0dff9a6b9e107dd3ce9285d7b81bb0c649ce2fa689c8a2b78d0be8e40d'
    with pytest.raises(ValueError,match='Decision tables'):build_runtime_project(record())


def test_exported_runtime_refuses_manually_injected_tables_before_planning():
    spec=importlib.util.spec_from_file_location('table_test_runtime',Path(__file__).parents[1]/'shared/process_agent_templates/runtime.py.txt',
        loader=__import__('importlib.machinery',fromlist=['SourceFileLoader']).SourceFileLoader('table_test_runtime',str(Path(__file__).parents[1]/'shared/process_agent_templates/runtime.py.txt')))
    runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    with pytest.raises(ValueError,match='cannot evaluate decision tables'):runtime.validate_spec({'process_model':record()['process_model']})


def test_minimized_report_pins_exact_model_and_declared_date_and_route_disagreement():
    r=record();before=deepcopy(r)
    batch=RuleCaseBatch.model_validate({'as_of_date':'2026-10-26','cases':[
        {'case_id':'case_1','facts':{'amount':100},'activity_ids':['choose','accept']},
        {'case_id':'case_2','facts':{},'activity_ids':['missing']},
        {'case_id':'case_3','facts':{'amount':99},'activity_ids':['choose','accept']}]})
    report=check_case_rules(r,batch.cases,batch.as_of_date)
    table=report['cases'][0]['decision_tables'][0]
    assert table['status']=='unique_match' and table['selected_target_activity_id']=='escalate'
    assert table['proposed_branch_agrees'] is False
    assert report['cases'][1]['decision_tables'][0]['status']=='not_evaluated_invalid_route'
    assert report['cases'][2]['decision_tables'][0]['proposed_branch_agrees'] is True
    assert report['decision_table_as_of_date']=='2026-10-26' and report['decision_table_coverage']=='supplied_cases_only_not_exhaustive'
    assert report['process_reference']=={k:r[k] for k in ('workflow_id','revision','content_sha256')}
    assert r==before and 'amount' not in json.dumps(report) and 'Approved policy' not in json.dumps(report)
    for bad in ['2026-02-29','2026-10-26T00:00:00Z',True]:
        with pytest.raises(ValidationError):RuleCaseBatch.model_validate({'as_of_date':bad,'cases':[{'case_id':'case_1','facts':{}}]})


def test_bpmn_references_are_remapped_with_dates_basis_conditions_and_no_confirmation():
    raw=process_bpmn(record()).encode();result=import_bpmn(raw);model=ProcessModel.model_validate(result['process_model'])
    assert len(model.decision_tables)==1
    table=model.decision_tables[0]
    assert table.effective_from=='2026-01-01' and table.effective_until=='2027-01-01'
    assert table.source_basis.document_sha256=='a'*64 and not table.user_confirmed
    assert all(not r.user_confirmed for r in model.business_rules)
    assert {(r.rule_id,r.target_activity_id) for r in table.rows}=={(e.rule_id,e.target) for e in model.transitions}
    assert {r.predicate.conditions[0].operator for r in model.business_rules}=={'lt','gte'}
    assert not model.execution_authorized and not result['business_rules_evaluated']
    assert 'Declared UNIQUE decision tables' in process_document(record())
    assert 'a'*64 in process_document(record())
    # A second export/import retains the declaration instead of reducing it to a note.
    packet=record(model.model_dump());again=import_bpmn(process_bpmn(packet).encode())
    assert len(again['process_model']['decision_tables'])==1


def test_bpmn_duplicate_corrupt_missing_flow_or_wrong_node_documents_fail_closed():
    root=ET.fromstring(process_bpmn(record()));ns='{http://www.omg.org/spec/BPMN/20100524/MODEL}'
    node=next(n for n in root.iter() if n.tag==ns+'exclusiveGateway')
    doc=next(d for d in node if (d.text or '').startswith('OWG decision table'))
    original=doc.text
    for text in [original.replace('Flow_1','Flow_999'),original.replace('Activity_choose','Activity_accept'),
                 original.replace('"UNIQUE"','"FIRST"'),original.replace('"hit_policy":','"hit_policy":"UNIQUE","hit_policy":')]:
        doc.text=text
        with pytest.raises(BPMNImportError):import_bpmn(ET.tostring(root))
    doc.text=original;node.append(deepcopy(doc))
    with pytest.raises(BPMNImportError):import_bpmn(ET.tostring(root))


def test_saved_revisions_review_and_declared_facts_audits_both_databases(store):
    saved=store.save(person(),SharedProcessWrite(title='Purchase process',process_model=graph(),user_confirmed=True,organization_sharing_confirmed=True))
    r=saved['process'];wid=r['workflow_id'];original=deepcopy(r)
    report=store.check_declared_rules(person('reader'),wid,1,RuleCaseBatch.model_validate({'as_of_date':'2026-10-26','cases':[{'case_id':'case_1','facts':{'amount':100,'PRIVATE':'PRIVATE_FACT'}}]}))
    assert report['cases'][0]['decision_tables'][0]['selected_target_activity_id']=='escalate'
    assert store.detail(person(),wid)['process']==original
    with pytest.raises(ProcessCollaborationError):store.check_declared_rules(person(org='other'),wid,1,RuleCaseBatch(cases=[RuleCase(case_id='case_1',facts={})]))
    with store.db.connect() as c:
        audit=store.db._execute(c,"SELECT details_json FROM audit_log WHERE action=?",('process.declared_rules.checked',)).fetchone()[0]
        assert 'PRIVATE' not in audit and 'amount' not in audit and 'purchase' not in audit
    grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}])
    store.review(person('bob'),wid,review(r))
    assert store.detail(person(),wid)['review']['status']=='reviewed_design'
    changed=graph();changed['decision_tables'][0]['source_basis']['document_sha256']='c'*64
    next_revision=store.save(person(),SharedProcessWrite(title='Purchase process',process_model=changed,expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)
    assert next_revision['process']['content_sha256']!=r['content_sha256']
    assert store.detail(person(),wid,revision=1)['process']==original
    assert next_revision['review']['status']=='needs_review'


def test_gateway_case_transfer_and_implementation_refusal_are_explicit(tmp_path):
    application=app(tmp_path)
    with TestClient(application) as client:
        headers={'Authorization':'Bearer alice'}
        saved=client.post('/v1/human/processes',headers=headers,json={'title':'Purchase process','process_model':graph(),'user_confirmed':True,'organization_sharing_confirmed':True})
        assert saved.status_code==200,saved.text
        r=saved.json()['process'];base='/v1/human/processes/'+r['workflow_id']
        body={'as_of_date':'2026-10-26','cases':[{'case_id':'case_1','facts':{'amount':100}}]}
        assert client.post(base+'/check-rules?revision=1',headers=headers,json=body).status_code==403
        authorized={**headers,'X-OWG-Case-Facts-Authorized':'true'}
        result=client.post(base+'/check-rules?revision=1',headers=authorized,json=body)
        assert result.status_code==200 and result.json()['cases'][0]['decision_tables'][0]['selected_target_activity_id']=='escalate'
        # The independent design review does not implement unsupported semantics.
        client.post(base+'/access',headers=headers,json={'expected_access_revision':1,'members':[{'actor_id':'bob','role':'reviewer'}],'user_confirmed':True})
        reviewed=client.post(base+'/reviews',headers={'Authorization':'Bearer bob'},json=review(r).model_dump())
        assert reviewed.status_code==200,reviewed.text
        refused=client.get(base+'/export?revision=1&format=agent-project',headers=headers)
        assert refused.status_code==409 and 'Decision tables' in refused.text
        assert client.get(base+'/export?revision=1&format=bpmn',headers=headers).status_code==200
        script=client.get('/process-decision-editor.js')
        assert script.status_code==200 and script.headers['cache-control']=='no-store'
