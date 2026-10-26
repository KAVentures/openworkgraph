"""Independent bounded-query oracles and shared definition lifecycle on both databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from shared.analysis_case_query import Query, execute
from gateway.analysis_case_queries import CaseQueries, Evaluate, Save, Remove
from gateway.analysis_publications import PublicationError, Delete
from gateway.analysis_case_data import CaseDelete, READ, WRITE
from test_shared_analysis_cases import cases, parent_store, process_store, p, dataset, attach, member, grant
from test_shared_analysis_publications import person, app as app_factory, publication


def spec(**extra):return {'measures':[{'fn':'count'},{'fn':'avg','of':'duration_seconds'}],**extra}


def test_query_oracles_grouping_filtering_and_percentile():
    packet=dataset().model_dump(mode='json')
    r=execute(packet,spec(measures=[{'fn':'count'},{'fn':'sum','of':'events'},{'fn':'median','of':'duration_seconds'},{'fn':'p90','of':'duration_seconds'}]))
    assert r['rows'][0]['values']==pytest.approx([4,9,2,4.1])
    assert r['matching_cases']==4 and r['matching_events']==9
    r=execute(packet,spec(group_by='repeated_activity',measures=[{'fn':'count'},{'fn':'share'}]))
    assert [x['values'] for x in r['rows']]==[[3,.75],[1,.25]]
    r=execute(packet,spec(where={'min_duration_seconds':3,'repeated_activity':True,'activity':'A2','min_events':3}))
    assert r['matching_cases']==1 and r['rows'][0]['values']==[1,5]
    variant=execute(packet,spec(group_by='variant',order='group_asc',limit=1))
    assert variant['groups']==2 and variant['rows_truncated']
    only=execute(packet,spec(where={'variant':variant['rows'][0]['group']}))
    assert only['matching_cases']==variant['rows'][0]['cases']


def test_time_between_recurrence_missing_and_incomplete_checks_are_not_passes():
    packet=dataset().model_dump(mode='json')
    r=execute(packet,spec(measures=[{'fn':'avg','of':'time_between','from_activity':'A1','to_activity':'A1'},
                                  {'fn':'sum','of':'activity_count','activity':'A2'}],check={'comparison':'gte','threshold':0}))
    assert r['rows'][0]['values']==[5,4]
    assert r['coverage'][0]=={'with_value':1,'without_value':3}
    assert r['rows'][0]['check_state']=='incomplete'
    assert not r['check_is_live_monitoring'] and not r['execution_authorized']
    r=execute(packet,spec(measures=[{'fn':'max','of':'time_between','from_activity':'A2','to_activity':'A99'}],check={'comparison':'eq','threshold':0}))
    assert r['rows'][0]['values']==[None] and r['rows'][0]['check_state']=='not_evaluable'
    r=execute(packet,spec(where={'activity':'A99'},measures=[{'fn':'count'},{'fn':'share'},{'fn':'sum','of':'events'}]))
    assert r['matching_cases']==0 and r['rows'][0]['values']==[0,None,None]


def test_check_states_and_zero_time_ties():
    packet=dataset().model_dump(mode='json');packet['cases'][0]['events'][1]['elapsed_microseconds']=0
    r=execute(packet,spec(measures=[{'fn':'avg','of':'time_between','from_activity':'A1','to_activity':'A2'}],check={'comparison':'gte','threshold':1}))
    assert r['rows'][0]['values']==[1.5] and r['rows'][0]['check_state']=='passes'
    r=execute(packet,spec(group_by='repeated_activity',limit=1,check={'measure_index':0,'comparison':'lt','threshold':1}))
    assert r['rows'][0]['check_state']=='fails' and r['check_coverage']['rows_not_returned']==1
    assert 'ties do not establish causality' in r['time_between_basis']


@pytest.mark.parametrize('extra',[
    {'sql':'SELECT * FROM secrets'}, {'group_by':'person'}, {'limit':501}, {'limit':True},
    {'where':{'min_duration_seconds':5,'max_duration_seconds':1}}, {'where':{'min_duration_seconds':float('inf')}},
    {'where':{'case_id':'secret'}}, {'measures':[{'fn':'avg'}]}, {'measures':[{'fn':'count','of':'events'}]},
    {'measures':[{'fn':'avg','of':'time_between','from_activity':'A1'}]},
    {'measures':[{'fn':'avg','of':'events','activity':'A1'}]}, {'measures':[{'fn':'avg','of':'events','from_activity':'A1'}]},
    {'check':{'measure_index':3,'comparison':'gt','threshold':0}}, {'check':{'comparison':'gt','threshold':float('nan')}},
    {'check':{'comparison':'gt','threshold':True}}, {'check':{'comparison':'gt','threshold':2**60}},
])
def test_typed_contract_refuses_code_hidden_fields_and_unsafe_values(extra):
    with pytest.raises(ValidationError):Query.model_validate(spec(**extra))


@pytest.fixture
def queries(cases):
    wid,_=attach(cases);return CaseQueries(cases),wid


def save_body(q,wid,expected=0,**extra):
    info=q.cases.read(p(),wid,1)
    return Save(parent_revision=1,dataset_content_sha256=info['content_sha256'],expected_parent_access_revision=info['parent_access_revision'],
        expected_case_access_revision=info['access_revision'],expected_revision=expected,name='Elapsed case checks',spec=spec(),user_confirmed=True,**extra)


def test_definitions_history_evaluation_exports_and_no_automatic_case_grants(queries):
    q,wid=queries;body=save_body(q,wid);first=q.save(p(),wid,body);qid=first['query_id']
    member(q.cases,wid);grant(q.cases,wid)
    assert q.inventory(p('bob'),wid,1)['queries'][0]['name']==body.name
    assert not q.inventory(p('bob'),wid,1)['can_write']
    with pytest.raises(PublicationError):q.save(p('bob'),wid,save_body(q,wid,1),qid)
    second=q.save(p(),wid,save_body(q,wid,1).model_copy(update={'name':'New title'}),qid)
    assert second['revision']==2
    old=q.detail(p('bob'),wid,1,qid,1,exporting=True)
    assert old['query']['name']=='Elapsed case checks' and old['latest_revision']==2
    assert old['result']['rows'][0]['values']==[4,2.75]
    for who in [person(),person('bob'),p('root',extra={'org:analysis:admin'}),p('alice','other')]:
        with pytest.raises(PublicationError):q.inventory(who,wid,1)
    grant(q.cases,wid,actors=(),expected=2)
    with pytest.raises(PublicationError):q.detail(p('bob'),wid,1,qid,1,exporting=True)
    audit=json.dumps(q.parent.db.audit_rows('acme',100))
    assert 'analysis.query.exported' in audit and 'Elapsed case checks' not in audit and 'duration_seconds' not in audit


def test_save_binds_dataset_audience_and_both_access_counters(queries):
    q,wid=queries;old=save_body(q,wid)
    member(q.cases,wid)
    with pytest.raises(PublicationError,match='access changed'):q.save(p(),wid,old)
    old=save_body(q,wid);grant(q.cases,wid)
    with pytest.raises(PublicationError,match='access changed'):q.save(p(),wid,old)
    with pytest.raises(PublicationError):q.save(p(),wid,save_body(q,wid).model_copy(update={'dataset_content_sha256':'e'*64}))
    with pytest.raises(PublicationError):q.save(p(),wid,save_body(q,wid).model_copy(update={'user_confirmed':False}))
    assert q.inventory(p(),wid,1)['queries']==[]


def test_editor_still_needs_case_grant_and_only_owner_can_delete(queries):
    from gateway.analysis_publications import Access
    q,wid=queries
    q.parent.access(p(),wid,Access(expected_access_revision=1,members=[{'actor_id':'bob','role':'editor'}],user_confirmed=True))
    with pytest.raises(PublicationError):q.save(p('bob'),wid,save_body(q,wid))
    grant(q.cases,wid)
    made=q.save(p('bob'),wid,save_body(q,wid));qid=made['query_id']
    with pytest.raises(PublicationError):q.delete(p('bob'),wid,qid,Remove(parent_revision=1,dataset_content_sha256=save_body(q,wid).dataset_content_sha256,expected_revision=1,user_confirmed=True))
    q.delete(p(),wid,qid,Remove(parent_revision=1,dataset_content_sha256=save_body(q,wid).dataset_content_sha256,expected_revision=1,user_confirmed=True))
    assert q.inventory(p(),wid,1)['queries']==[]


def test_concurrent_revisions_and_capacities_preserve_history(queries,monkeypatch):
    import gateway.analysis_case_queries as module
    q,wid=queries;made=q.save(p(),wid,save_body(q,wid));qid=made['query_id'];body=save_body(q,wid,1)
    def write(_):
        try:q.save(p(),wid,body,qid);return 'ok'
        except PublicationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(write,range(2)))==['conflict','ok']
    monkeypatch.setattr(module,'MAX_QUERIES',1)
    with pytest.raises(PublicationError,match='capacity'):q.save(p(),wid,save_body(q,wid))
    monkeypatch.setattr(module,'MAX_REVISIONS',2)
    with pytest.raises(PublicationError,match='ten revisions'):q.save(p(),wid,save_body(q,wid,2),qid)
    assert q.detail(p(),wid,1,qid,1)['query']['revision']==1


@pytest.mark.parametrize('mode',['case','parent','expiry'])
def test_dependent_metadata_is_erased_with_case_or_parent_lifecycle(queries,mode):
    q,wid=queries;made=q.save(p(),wid,save_body(q,wid));info=q.cases.access(p(),wid,1)
    if mode=='case':q.cases.delete(p(),wid,CaseDelete(revision=1,content_sha256=info['content_sha256'],expected_access_revision=1,expected_parent_access_revision=1,user_confirmed=True))
    elif mode=='parent':q.parent.delete(p(),wid,Delete(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    else:
        now=q.parent.now();q.parent.now=lambda:now+timedelta(days=8);q.parent.purge_expired()
    with q.parent.db.connect() as c:assert q.parent.q(c,'SELECT count(*) FROM gateway_analysis_case_queries').fetchone()[0]==0
    with pytest.raises(PublicationError):q.detail(p(),wid,1,made['query_id'])


def test_corruption_refuses_execution_but_owner_can_erase_and_audit_is_atomic(queries,monkeypatch):
    q,wid=queries;body=save_body(q,wid)
    def fail(*a,**kw):raise RuntimeError('synthetic audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(q.parent,'audit',fail)
        with pytest.raises(RuntimeError):q.save(p(),wid,body)
    assert q.inventory(p(),wid,1)['queries']==[]
    made=q.save(p(),wid,body);qid=made['query_id']
    with q.parent.db.connect() as c:q.parent.q(c,"UPDATE gateway_analysis_case_queries SET payload='corrupt' WHERE query_id=?",(qid,))
    assert not q.inventory(p(),wid,1)['queries'][0]['integrity_ok']
    with pytest.raises(PublicationError,match='integrity'):q.detail(p(),wid,1,qid,1,exporting=True)
    q.delete(p(),wid,qid,Remove(parent_revision=1,dataset_content_sha256=body.dataset_content_sha256,expected_revision=1,user_confirmed=True))
    assert q.inventory(p(),wid,1)['queries']==[]


def test_editor_can_save_metadata_without_raw_case_write_permission(queries):
    from dataclasses import replace
    from gateway.analysis_publications import Access
    q,wid=queries;q.parent.access(p(),wid,Access(expected_access_revision=1,members=[{'actor_id':'bob','role':'editor'}],user_confirmed=True));grant(q.cases,wid)
    editor=replace(p('bob'),scopes=p('bob').scopes-{WRITE})
    made=q.save(editor,wid,save_body(q,wid))
    assert q.detail(editor,wid,1,made['query_id'])['result']['matching_cases']==4
    with pytest.raises(PublicationError):q.cases.preflight(editor,wid)


def test_real_http_preflight_bounded_query_and_shared_revision_export(tmp_path):
    from gateway.analysis_case_data import Attach
    from test_shared_analysis_cases import dataset
    app=app_factory(tmp_path);app.state.human_access_settings.group_scope_map['analysis']|={READ,WRITE}
    h=lambda actor:{'Authorization':'Bearer '+actor}
    with TestClient(app) as c:
        parent=c.post('/v1/human/analyses',headers=h('alice'),json=publication().model_dump(mode='json')).json();wid=parent['view_id'];base='/v1/human/analyses/'+wid+'/case-data'
        c.post(base,headers=h('alice'),json=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True).model_dump(mode='json'))
        q=app.state.analysis_case_queries;body=save_body(q,wid).model_dump(mode='json')
        assert c.post(base+'/query?parent_revision=1',headers=h('read-only'),content=b'not JSON').status_code==403
        assert c.post(base+'/queries?parent_revision=1',headers=h('bob'),content=b'not JSON').status_code==404
        for token in ['admin','process-only','foreign']:
            assert c.get(base+'/queries?parent_revision=1',headers=h(token)).status_code in {401,403,404}
        r=c.post(base+'/query?parent_revision=1',headers=h('alice'),json={'parent_revision':1,'dataset_content_sha256':body['dataset_content_sha256'],'spec':spec()})
        assert r.status_code==200,r.text
        assert r.headers['cache-control']=='no-store' and r.json()['rows'][0]['values']==[4,2.75]
        assert c.post(base+'/query?parent_revision=1',headers={**h('alice'),'Content-Type':'application/json'},content=b'x'*17000).status_code==413
        assert c.post(base+'/query?parent_revision=1',headers=h('alice'),json={'parent_revision':1,'dataset_content_sha256':body['dataset_content_sha256'],'spec':{'sql':'select secret'}}).status_code==422
        r=c.post(base+'/queries?parent_revision=1',headers=h('alice'),json=body);assert r.status_code==200,r.text;qid=r.json()['query_id']
        assert c.post(base+'/queries/'+qid+'?parent_revision=1',headers=h('alice'),json=body).status_code==409
        r=c.get(base+'/queries/'+qid+'/export?parent_revision=1&revision=1',headers=h('alice'));assert r.status_code==200 and r.headers['content-disposition'].startswith('attachment')
        assert c.get('/analysis-case-queries-client.js').headers['x-content-type-options']=='nosniff'
        assert c.post(base+'/queries?parent_revision=2',headers=h('alice'),json=body).status_code==404
        assert c.post(base+'/queries?parent_revision=1',headers=h('alice'),json={**body,'parent_revision':2}).status_code==422


def test_full_budget_and_truncated_group_shares_use_the_complete_population():
    from shared.analysis_case_dataset import CaseDataset
    doc=dataset().model_dump(mode='json');doc['cases']=[]
    for i in range(500):
        path=['A1']+['A3' if (i>>bit)&1 else 'A2' for bit in range(9)]
        doc['cases'].append({'code':'C'+str(i+1),'events':[{'activity':a,'elapsed_microseconds':k*1000000} for k,a in enumerate(path)]})
    packet=CaseDataset.model_validate(doc).model_dump(mode='json')
    r=execute(packet,spec(measures=[{'fn':'count'},{'fn':'sum','of':'events'},{'fn':'avg','of':'time_between','from_activity':'A1','to_activity':'A2'},{'fn':'p90','of':'duration_seconds'}]))
    assert r['matching_cases']==500 and r['matching_events']==5000 and r['rows'][0]['values'][0:2]==[500,5000]
    assert r['rows'][0]['values'][3]==9
    r=execute(packet,spec(group_by='variant',limit=100,measures=[{'fn':'share'}],check={'comparison':'gt','threshold':0}))
    assert r['groups']==500 and r['rows_truncated'] and len(r['rows'])==100
    assert all(row['values']==[.002] for row in r['rows'])
    assert r['check_coverage']['rows_not_returned']==400


def test_failed_audit_refuses_export_and_rolls_back_definition_deletion(queries,monkeypatch):
    q,wid=queries;body=save_body(q,wid);qid=q.save(p(),wid,body)['query_id']
    def fail(*a,**kw):raise RuntimeError('synthetic audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(q.parent,'audit',fail)
        with pytest.raises(RuntimeError):q.detail(p(),wid,1,qid,1,exporting=True)
        with pytest.raises(RuntimeError):q.delete(p(),wid,qid,Remove(parent_revision=1,dataset_content_sha256=body.dataset_content_sha256,expected_revision=1,user_confirmed=True))
    assert q.detail(p(),wid,1,qid)['query']['revision']==1
