"""Versioned graph persistence, owner boundaries, structural alignment and HTTP lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from gateway.db import GatewayDB
from server.process_catalog import CatalogWrite, ProcessCatalog, process_bpmn, process_document
from server.process_mining_csv import mine_csv_events
from shared.process_model import ProcessModel
from shared.process_conformance import compare_path
from shared.workflow_knowledge import KnowledgeStore, KnowledgeWrite

ROOT = Path(__file__).resolve().parents[1]


def model():
    return ProcessModel(goal='Resolve the selected case', baseline_source='selected_csv_variant',
        baseline_path=['Open', 'Review', 'Close'], activities=[
            {'id': 'a', 'label': 'Open'}, {'id': 'b', 'label': 'Review'}, {'id': 'c', 'label': 'Close'},
        ], transitions=[{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'c'}],
        entry_activity_id='a', terminal_activity_ids=['c'])


def branched_model():
    value = model().model_dump()
    value['activities'].insert(2, {'id': 'decision', 'label': 'Choose outcome', 'kind': 'decision'})
    value['activities'].append({'id': 'reject', 'label': 'Reject'})
    value['business_rules'] = [{'id': 'pass', 'text': 'Evidence is sufficient', 'user_confirmed': True},
                               {'id': 'fail', 'text': 'Evidence is insufficient', 'user_confirmed': False}]
    value['transitions'] = [{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'decision'},
        {'source': 'decision', 'target': 'c', 'rule_id': 'pass'},
        {'source': 'decision', 'target': 'reject', 'rule_id': 'fail'}]
    value['terminal_activity_ids'] = ['c', 'reject']
    return ProcessModel.model_validate(value)


def test_catalog_reuses_knowledge_revisions_isolates_owners_and_pins_exports(tmp_path):
    store = KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "knowledge.db"}'))
    catalog = ProcessCatalog(store, 'alice')
    request = CatalogWrite(workflow_id='review', title='Case review', process_model=model())
    with pytest.raises(ValueError, match='review'):
        catalog.save(request)
    first = catalog.save(request.model_copy(update={'user_confirmed': True}))
    assert first['revision'] == 1
    assert store.list('alice')['workflows'][0]['process_model'] == model().model_dump()
    assert store.list('bob')['workflows'] == []
    with pytest.raises(LookupError):
        ProcessCatalog(store, 'bob').get('review')
    second = catalog.save(CatalogWrite(workflow_id='review', title='Updated', process_model=branched_model(),
                                       expected_revision=1, user_confirmed=True))
    with pytest.raises(ValueError, match='Revision'):
        catalog.save(CatalogWrite(workflow_id='review', title='Stale', process_model=model(),
                                  expected_revision=1, user_confirmed=True))
    assert catalog.get('review', 1) == first
    assert catalog.get('review') == second
    page = store.history('alice', 'review', limit=1)
    assert page['workflows'] == [second]
    assert page['next_before_revision'] == 2
    assert store.history('alice', 'review', before_revision=2)['workflows'] == [first]
    assert store.history('bob', 'review')['workflows'] == []
    packet = catalog.export('review', 1)
    assert packet['revision'] == 1 and packet['content_sha256'] == first['content_sha256']
    assert packet['execution_authorization'] is False
    assert 'revision 1' in process_document(packet)
    root = ET.fromstring(process_bpmn(catalog.export('review', 2)))
    ns = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}
    assert root.find('bpmn:process', ns).attrib['isExecutable'] == 'false'
    assert len(root.findall('.//bpmn:exclusiveGateway', ns)) == 1
    assert len(root.findall('.//bpmn:sequenceFlow', ns)) == 4
    assert store.forget('alice', 'review')['versions_deleted'] == 2
    with pytest.raises(LookupError):
        catalog.get('review', 1)


def test_process_catalog_never_overwrites_generic_knowledge_and_paginates(tmp_path):
    store = KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "knowledge.db"}'))
    store.save('alice', KnowledgeWrite(workflow_id='ordinary', title='Procedure', procedure='Ask the owner',
                                      source_client='test', user_confirmed=True))
    catalog = ProcessCatalog(store, 'alice')
    with pytest.raises(ValueError, match='without a process model'):
        catalog.save(CatalogWrite(workflow_id='ordinary', title='Overwrite', process_model=model(),
                                  user_confirmed=True, expected_revision=1))
    catalog.save(CatalogWrite(workflow_id='review', title='Review', process_model=model(), user_confirmed=True))
    page = store.page('alice', limit=1)
    assert page['next_after'] == 'ordinary'
    assert store.page('alice', after='ordinary')['workflows'][0]['workflow_id'] == 'review'
    assert store.page('bob')['workflows'] == []


@pytest.mark.parametrize('mutate', [
    lambda m: m['activities'].append({'id': 'a', 'label': 'Duplicate'}),
    lambda m: m['activities'].append({'id': 'isolated', 'label': 'Isolated'}),
    lambda m: m['transitions'].append({'source': 'missing', 'target': 'a'}),
    lambda m: m['transitions'].append({'source': 'c', 'target': 'a'}),
    lambda m: m['transitions'].append({'source': 'a', 'target': 'b'}),
    lambda m: m['transitions'].append({'source': 'a', 'target': 'c'}),
    lambda m: m.update(entry_activity_id='missing'),
    lambda m: m.update(execution_authorized=True),
    lambda m: m.update(outcomes_verified=True),
    lambda m: m.update(unknown_field='unreviewed'),
    lambda m: m['activities'][0].update(label='<script>'),
])
def test_invalid_graphs_fail_closed(mutate):
    value = model().model_dump()
    mutate(value)
    with pytest.raises(ValidationError):
        ProcessModel.model_validate(value)


def test_structural_alignment_branches_prefixes_repetition_and_unknown_rules():
    graph = branched_model()
    assert graph.linear_path() is None
    for ending in ('Close', 'Reject'):
        result = compare_path(('Open', 'Review', ending), graph)
        assert result['exact_structural_match']
        assert not result['business_rules_evaluated']
        assert not result['policy_compliance_verified']
    assert model().linear_path() == ['Open', 'Review', 'Close']
    prefix = compare_path(('Open', 'Review'), graph)
    assert not prefix['exact_structural_match']
    assert prefix['could_be_incomplete_log_prefix']
    extra = compare_path(('Open', 'Review', 'Review', 'Close'), graph)
    assert extra['minimum_activity_edit_count'] == 1
    assert [op['type'] for op in extra['alignment']].count('log_only') == 1
    missing = compare_path(('Open', 'Close'), graph)
    assert missing['minimum_activity_edit_count'] == 1
    assert any(op['type'] == 'model_only' for op in missing['alignment'])


def test_cycles_and_routing_nodes_terminate_and_preserve_structural_meaning():
    value = branched_model().model_dump()
    value['transitions'][-1] = {'source': 'decision', 'target': 'b', 'rule_id': 'fail'}
    value['activities'] = [a for a in value['activities'] if a['id'] != 'reject']
    value['terminal_activity_ids'] = ['c']
    graph = ProcessModel.model_validate(value)
    assert compare_path(('Open', 'Review', 'Review', 'Close'), graph)['exact_structural_match']
    assert compare_path(tuple(['Open'] + ['Review'] * 200 + ['Close']), graph)['alignment_truncated']
    value['transitions'][1] = {'source': 'b', 'target': 'b'}
    with pytest.raises(ValidationError, match='reachable|terminal'):
        ProcessModel.model_validate(value)


def test_comparison_covers_omitted_variants_and_cases():
    lines = ['case_id,activity,timestamp']
    for i in range(35):
        # 35 variants, only 20 displayed. None can be mistaken for an approved policy.
        lines += [f'private-{i},Open,2026-10-20T10:00:00Z',
                  f'private-{i},Activity_{i},2026-10-20T10:01:00Z']
    report = mine_csv_events('\n'.join(lines).encode(), reference_model=model())
    assert report['conformance']['case_count'] == 35
    assert report['conformance']['structurally_deviating_cases'] == 35
    assert report['variants_truncated'] and report['cases_truncated']
    assert all('conformance' in row for row in report['variants'])
    assert 'private-' not in json.dumps(report)


def test_real_authenticated_catalog_save_reopen_conflict_export_compare_delete(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from shared.process_model import ProcessModel
m=ProcessModel(goal='Resolve the case',baseline_source='selected_csv_variant',baseline_path=['Open','Close'],
 activities=[{'id':'a','label':'Open'},{'id':'b','label':'Close'}],transitions=[{'source':'a','target':'b'}],
 entry_activity_id='a',terminal_activity_ids=['b']).model_dump()
body={'title':'Case process','user_confirmed':True,'process_model':m}
with TestClient(app) as client:
 assert client.post('/v1/process-catalog',json=body).status_code==401
 assert client.get('/v1/process-catalog').status_code==401
 headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 first=client.post('/v1/process-catalog',json=body,headers=headers)
 assert first.status_code==200,first.text
 assert first.headers['cache-control']=='no-store'
 first=first.json(); wid=first['workflow_id']
 path='/v1/process-catalog/revision?workflow_id='+wid
 assert client.get(path,headers=headers).json()==first
 body.update(workflow_id=wid,expected_revision=1,title='Changed')
 second=client.post('/v1/process-catalog',json=body,headers=headers)
 assert second.json()['revision']==2,second.text
 assert client.post('/v1/process-catalog',json=body,headers=headers).status_code==409
 assert client.get(path+'&revision=1',headers=headers).json()==first
 assert len(client.get('/v1/process-catalog/history?workflow_id='+wid,headers=headers).json()['workflows'])==2
 assert client.get('/v1/process-catalog',headers=headers).json()['processes'][0]['revision']==2
 for fmt in ['json','bpmn','pdd']:
  r=client.get('/v1/process-catalog/export',params={'workflow_id':wid,'revision':1,'format':fmt},headers=headers)
  assert r.status_code==200,r.text
  assert r.headers['cache-control']=='no-store'
 csv=b'case_id,activity,timestamp\nsecret,Open,2026-10-20T10:00:00Z\nsecret,Close,2026-10-20T10:00:10Z\n'
 comparison=client.post('/v1/process-mining/preview',params={'workflow_id':wid,'revision':1},content=csv,
  headers={**headers,'Content-Type':'text/csv'})
 assert comparison.status_code==200,comparison.text
 c=comparison.json()['conformance']
 assert c['exact_structural_match_cases']==1 and c['reference']['content_sha256']==first['content_sha256']
 assert 'secret' not in comparison.text
 project=client.get('/v1/process-catalog/agent-project',params={'workflow_id':wid,'revision':1},headers=headers)
 assert project.status_code==200 and project.headers['content-type']=='application/zip'
 from zipfile import ZipFile
 from io import BytesIO
 with ZipFile(BytesIO(project.content)) as z:
  assert json.loads(z.read('workflow.spec.json'))['revision']==1
 assert client.get('/v1/process-catalog/opportunities?currency=USD',headers=headers).json()['opportunities']==[]
 assert client.post('/v1/process-mining/preview?workflow_id='+wid,content=csv,headers=headers).status_code==422
 assert client.post('/v1/process-catalog',content=b'x'*131073,headers={**headers,'Content-Type':'application/json'}).status_code==413
 assert client.post('/v1/process-catalog',json={**body,'user_confirmed':False},headers=headers).status_code==409
 assert client.post('/v1/process-catalog',json={**body,'unknown':'private'},headers=headers).status_code==422
 for raw in [b'{"user_confirmed":false,"user_confirmed":true}',b'{"number":NaN}',b'['*2000+b']'*2000]:
  assert client.post('/v1/process-catalog',content=raw,headers={**headers,'Content-Type':'application/json'}).status_code==422
 assert client.get(path+'&revision=999',headers=headers).status_code==404
 assert client.post('/v1/process-catalog/forget',json={'workflow_id':wid,'user_confirmed':False},headers=headers).status_code==422
 assert client.post('/v1/process-catalog/forget',json={'workflow_id':wid,'user_confirmed':True},headers=headers).json()['versions_deleted']==2
 assert client.get(path,headers=headers).status_code==404
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth'), 'WORKFLOW_OBSERVER_CONFIG': str(tmp_path / 'config.json')}
    run = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


def test_opportunity_backlog_keeps_currencies_and_assumptions_separate(tmp_path):
    from shared.process_opportunities import OpportunityScenario
    store = KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "knowledge.db"}'))
    catalog = ProcessCatalog(store, 'alice')
    values = dict(currency='USD',monthly_cases=120,minutes_saved_per_case=10,eligible_fraction_percent=50,
                  loaded_hourly_cost=60,recurring_monthly_cost=100,one_time_implementation_cost=1000,sensitivity_percent=25)
    scenario = OpportunityScenario(**values)
    projection = scenario.projection()
    assert projection['assumed_hours_saved_per_month'] == 10
    assert projection['net_monthly_value_before_other_costs'] == 500
    assert projection['simple_payback_months'] == 2
    assert projection['sensitivity_net_monthly_low'] == 350
    assert projection['sensitivity_net_monthly_high'] == 650
    for wid,currency,cost in [('a','USD',100),('b','USD',1000),('c','EUR',0)]:
        graph = model().model_dump()
        graph['opportunity_scenario'] = {**values,'currency':currency,'recurring_monthly_cost':cost}
        catalog.save(CatalogWrite(workflow_id=wid,title=wid,process_model=ProcessModel.model_validate(graph),user_confirmed=True))
    catalog.save(CatalogWrite(workflow_id='no_scenario',title='No assumptions',process_model=model(),user_confirmed=True))
    report = catalog.opportunities('USD')
    assert [row['workflow_id'] for row in report['opportunities']] == ['a','b']
    assert report['opportunities'][1]['projection']['simple_payback_months'] is None
    assert report['processes_without_scenarios_in_scanned_records'] == 1
    assert report['labor_savings_measured'] is False
    assert ProcessCatalog(store,'bob').opportunities('USD')['opportunities'] == []
    for bad in [float('inf'), float('nan'), -1]:
        with pytest.raises(ValidationError):
            OpportunityScenario(**{**values,'monthly_cases':bad})


def test_structural_bpmn_ids_cannot_collide_with_model_ids(tmp_path):
    value = model().model_dump()
    value['activities'][0]['id']='Flow_1'
    value['entry_activity_id']='Flow_1'
    value['transitions'][0]['source']='Flow_1'
    value['activities'][1]['id']='Process_OWG'
    value['transitions'][0]['target']='Process_OWG'
    value['transitions'][1]['source']='Process_OWG'
    catalog=ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "knowledge.db"}')),'alice')
    saved=catalog.save(CatalogWrite(title='Collision test',process_model=ProcessModel.model_validate(value),user_confirmed=True))
    root=ET.fromstring(process_bpmn(catalog.export(saved['workflow_id'],1)))
    ids=[node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    assert len(ids)==len(set(ids))


def test_timestamp_ties_and_repeated_entries_remain_visible_and_not_deduplicated():
    raw=b'case_id,activity,timestamp\nprivate,Review,2026-10-20T10:00:00Z\nprivate,Open,2026-10-20T09:00:00Z\nprivate,Review,2026-10-20T10:00:00Z\nprivate,Close,2026-10-20T10:00:00Z\n'
    report=mine_csv_events(raw,reference_model=model())
    quality=report['data_quality']
    assert quality['cases_with_tied_timestamps']==1
    assert quality['tied_timestamp_adjacent_pairs']==2
    assert quality['same_case_activity_timestamp_repeats']==1
    assert quality['cases_sorted_from_nonchronological_input']==1
    assert quality['repeated_rows_are_verified_duplicate_events'] is False
    assert report['input_rows']==4
    assert report['variants'][0]['observed_path']==['Open','Review','Review','Close']
    assert report['variants'][0]['ambiguous_order_case_count']==1
    assert report['cases'][0]['ordering_basis']=='timestamp_then_supplied_row_order'
    assert 'private' not in json.dumps(report)
