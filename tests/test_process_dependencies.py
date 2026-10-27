"""Declared graph integrity, exact pins, concurrent cycles and current-role boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shared.process_dependencies import Declaration, Save, Remove, Saved, topology, impact, content_digest
from gateway.process_dependencies import ProcessDependencies, namespace
from gateway.process_collaboration import ProcessCollaborationError as Error, ProcessDeleteWrite, ProcessArchiveWrite
from test_process_collaboration import store, person, write, grant, review, app, SCOPES


def process(store,title='Process'):return store.save(person(),write(title))['process']
def pin(record):return {key:record[key] for key in ('workflow_id','revision','content_sha256')}
def declaration(root,*prerequisites):
    return {'organization_id':'acme','root_process':pin(root),'links':[{'id':'link_'+str(i),'prerequisite':pin(record),'relationship':'uses_output','reason':'PRIVATE_REASON shared by explicit confirmation'} for i,record in enumerate(prerequisites,1)]}
def body(root,*prerequisites,revision=0,access=1):
    return Save(dependencies=declaration(root,*prerequisites),expected_revision=revision,expected_access_revision=access,user_confirmed=True,organization_sharing_confirmed=True)
def approve(store,record):
    wid=record['workflow_id'];grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}]);store.review(person('bob'),wid,review(record))
def graph(store,focus=None):return ProcessDependencies(store).graph(person(),focus)


def test_independent_direction_distance_diamond_and_reuse_oracle():
    def d(root,*targets):return {'root_process':{'workflow_id':root},'links':[{'id':'to_'+target,'prerequisite':{'workflow_id':target},'relationship':'uses_output'} for target in targets]}
    data=topology([d('A','B','C'),d('B','D'),d('C','D'),d('D'),d('E','B')])
    assert data['prerequisite_first_order']==['D','B','C','A','E']
    result=impact(data,'D');assert {r['workflow_id']:r['distance'] for r in result['potential_consumers']}=={'B':1,'C':1,'A':2,'E':2}
    assert result['prerequisites']==[] and not result['causal_impact_verified']
    assert impact(data,'A')['potential_consumers']==[]
    assert {r['workflow_id']:r['distance'] for r in impact(data,'A')['prerequisites']}=={'B':1,'C':1,'D':2}
    with pytest.raises(ValueError,match='cycle'):topology([d('A','B'),d('B','C'),d('C','A')])
    with pytest.raises(ValueError):impact(data,'foreign')


@pytest.mark.parametrize('change',[
    lambda d:d['links'].append(deepcopy(d['links'][0])),
    lambda d:d['links'][0].update(prerequisite=deepcopy(d['root_process'])),
    lambda d:d['links'][0].update(relationship='executes'),
    lambda d:d['links'][0].update(reason='   '),
    lambda d:d['links'][0].update(reason='bad\nline'),
    lambda d:d['links'][0].update(reason='\ud800'),
    lambda d:d.update(execution_authorized=True),lambda d:d.update(propagates_process_review=True),
    lambda d:d.update(relationships_independently_verified=True),lambda d:d.update(benefit_rollup_included=True),
    lambda d:d['root_process'].update(revision=True),
])
def test_malformed_executable_or_duplicate_contracts_refused(change):
    root={'workflow_id':'A','revision':1,'content_sha256':'a'*64};child={**root,'workflow_id':'B'}
    value=declaration(root,child);change(value)
    with pytest.raises((ValueError,UnicodeError)):Declaration.model_validate(value)


def test_persistent_exact_history_and_source_models_unchanged(store):
    dep=ProcessDependencies(store);root=process(store,'Consumer');leaf=process(store,'Prerequisite');wid=root['workflow_id']
    before={r['workflow_id']:store.detail(person(),r['workflow_id'])['process'] for r in (root,leaf)}
    saved=dep.save(person(),wid,body(root,leaf));view=dep.detail(person(),wid);artifact=deepcopy(view['artifact'])
    assert saved['revision']==1 and view['all_references_current'] and view['can_write']
    assert not view['all_references_reviewed'] and not view['normal_exports_include_dependencies']
    assert not view['model_reviews_propagated'] and not view['execution_authorized']
    dep.save(person(),wid,body(root,revision=1))
    assert dep.detail(person(),wid,1,exporting=True)['artifact']==artifact
    assert dep.detail(person(),wid)['artifact']['dependencies']['links']==[]
    assert {r['workflow_id']:store.detail(person(),r['workflow_id'])['process'] for r in (root,leaf)}==before
    assert 'PRIVATE_REASON' not in json.dumps(graph(store))
    with store.db.connect() as c:
        rows=store.db._execute(c,"SELECT details_json FROM audit_log WHERE action LIKE ?",('process.dependencies.%',)).fetchall()
    assert all('PRIVATE_REASON' not in row[0] and 'uses_output' not in row[0] for row in rows)


def test_current_basis_requires_explicit_leaf_declaration_and_independent_model_reviews(store):
    dep=ProcessDependencies(store);a=process(store,'Consumer');b=process(store,'Prerequisite')
    approve(store,a);approve(store,b)
    dep.save(person(),a['workflow_id'],body(a,b,access=2))
    result=graph(store,b['workflow_id']);assert result['status']=='needs_reassessment'
    assert {r['workflow_id'] for r in result['impact']['potential_consumers']}=={a['workflow_id']}
    leaf=next(n for n in result['graph']['nodes'] if n['workflow_id']==b['workflow_id'])
    assert not leaf['dependencies_assessed'] and leaf['all_pins_match_latest']
    dep.save(person(),b['workflow_id'],body(b,access=2))
    result=graph(store);assert result['current_basis'] and result['status']=='current_declared_basis'
    assert not result['business_dependency_completeness_verified'] and not result['execution_authorized']
    exported=dep.graph(person(),expected_fingerprint=result['fingerprint'],exporting=True)
    assert exported['fingerprint']==result['fingerprint']


def test_changed_prerequisite_preserves_declared_impact_but_invalidates_current_basis_and_export(store):
    dep=ProcessDependencies(store);a=process(store,'A');b=process(store,'B')
    approve(store,a);approve(store,b)
    dep.save(person(),a['workflow_id'],body(a,b,access=2));dep.save(person(),b['workflow_id'],body(b,access=2))
    before=graph(store);artifact=dep.detail(person(),a['workflow_id'])['artifact']
    new=store.save(person(),write('B changed',expected=1),b['workflow_id'])['process']
    after=graph(store,b['workflow_id']);assert after['status']=='needs_reassessment' and before['fingerprint']!=after['fingerprint']
    assert after['impact']['potential_consumers']==[{'workflow_id':a['workflow_id'],'distance':1}]
    assert dep.detail(person(),a['workflow_id'])['artifact']==artifact
    assert not dep.detail(person(),a['workflow_id'])['all_references_current']
    with pytest.raises(Error):dep.graph(person(),expected_fingerprint=before['fingerprint'],exporting=True)
    with pytest.raises(Error):dep.save(person(),a['workflow_id'],body(a,b,revision=1,access=2))
    dep.save(person(),a['workflow_id'],body(a,new,revision=1,access=2))
    dep.save(person(),b['workflow_id'],body(new,revision=1,access=2))
    assert not graph(store)['current_basis']
    store.review(person('bob'),b['workflow_id'],review(new))
    assert graph(store)['current_basis']


def test_cross_root_cycle_refused_and_existing_map_groupings_remain_independent(store):
    dep=ProcessDependencies(store);a=process(store,'A');b=process(store,'B');c=process(store,'C')
    dep.save(person(),a['workflow_id'],body(a,b));dep.save(person(),b['workflow_id'],body(b,c))
    with pytest.raises(Error,match='cycle'):dep.save(person(),c['workflow_id'],body(c,a))
    assert dep.detail(person(),c['workflow_id'])['artifact'] is None
    from gateway.process_hierarchies import ProcessHierarchies
    from test_process_hierarchies import hierarchy,save_body
    maps=ProcessHierarchies(store)
    maps.save(person(),a['workflow_id'],save_body(hierarchy(a,b)))
    maps.save(person(),b['workflow_id'],save_body(hierarchy(b,a)))
    assert len(graph(store)['graph']['edges'])==2  # Groupings are not dependencies.


def test_concurrent_opposing_dependencies_cannot_commit_a_cycle(store):
    dep=ProcessDependencies(store);a=process(store,'A');b=process(store,'B');barrier=Barrier(2)
    def run(root,target):
        barrier.wait(timeout=10)
        try:dep.save(person(),root['workflow_id'],body(root,target));return 'saved'
        except Error as exc:assert exc.status==409;return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,a,b),pool.submit(run,b,a)]
        assert sorted(f.result(timeout=30) for f in futures)==['refused','saved']
    assert len(graph(store)['graph']['edges'])==1


def test_concurrent_same_root_compare_and_swap_and_independent_cross_root_saves(store):
    dep=ProcessDependencies(store);a,b,c,d=[process(store,name) for name in 'ABCD'];barrier=Barrier(2)
    def run(root,target):
        barrier.wait(timeout=10)
        try:dep.save(person(),root['workflow_id'],body(root,target));return 'saved'
        except Error:return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,a,b),pool.submit(run,a,c)]
        assert sorted(f.result(timeout=30) for f in futures)==['refused','saved']
    barrier=Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,b,c),pool.submit(run,d,c)]
        assert [f.result(timeout=30) for f in futures]==['saved','saved']


def test_scope_role_live_revocation_and_tenant_isolation(store):
    dep=ProcessDependencies(store);a=process(store);b=process(store,'B');wid=a['workflow_id']
    for principal in (person('reader'),person(scopes=frozenset({'org:process:read'})),person(org='foreign')):
        with pytest.raises(Error):dep.save(principal,wid,body(a,b))
    with pytest.raises(Error):dep.graph(person(scopes=frozenset()))
    assert dep.graph(person(org='foreign'))['status']=='empty'
    grant(store,wid,members=[{'actor_id':'bob','role':'editor'}])
    with pytest.raises(Error):dep.save(person('bob'),wid,body(a,b))
    dep.save(person('bob'),wid,body(a,b,access=2))
    assert dep.detail(person('reader'),wid)['artifact'] is not None
    grant(store,wid,expected=2,members=[])
    with pytest.raises(Error):dep.save(person('bob'),wid,body(a,b,revision=1,access=3))
    for principal in (person('bob'),person('reader')):
        with pytest.raises(Error):dep.delete(principal,wid,Remove(expected_revision=1,expected_access_revision=3,user_confirmed=True))
    with pytest.raises(Error):dep.detail(person(org='foreign'),wid)


def test_archive_and_deletion_change_diagnostics_without_erasing_other_declarations(store):
    dep=ProcessDependencies(store);a=process(store,'A');b=process(store,'B');wid=b['workflow_id']
    dep.save(person(),a['workflow_id'],body(a,b));dep.save(person(),wid,body(b))
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    result=graph(store,wid);assert not result['current_basis']
    assert next(n for n in result['graph']['nodes'] if n['workflow_id']==wid)['state']=='archived'
    with pytest.raises(Error):dep.save(person(),wid,body(b,revision=1,access=2))
    dep.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    result=graph(store,wid);node=next(n for n in result['graph']['nodes'] if n['workflow_id']==wid)
    assert not node['available'] and not node['dependencies_assessed']
    assert result['impact']['potential_consumers']==[{'workflow_id':a['workflow_id'],'distance':1}]
    assert dep.detail(person(),a['workflow_id'])['artifact']['dependencies']['links']
    with store.db.connect() as c:assert dep.latest(c,person(),wid)==0


def test_corruption_withholds_entire_impact_and_owner_can_erase_even_archived(store):
    dep=ProcessDependencies(store);a=process(store);b=process(store,'B');wid=a['workflow_id']
    dep.save(person(),wid,body(a,b))
    with store.write_transaction() as c:dep.q(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"PRIVATE_CORRUPT":true}',namespace('acme'),wid))
    view=dep.detail(person(),wid);assert not view['integrity_ok'] and view['artifact'] is None and not view['can_write'] and view['can_delete']
    result=graph(store);assert result['status']=='withheld' and result['graph'] is None and result['impact'] is None
    assert 'PRIVATE_CORRUPT' not in json.dumps(result)
    with pytest.raises(Error):dep.save(person(),b['workflow_id'],body(b))
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    dep.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert graph(store)['status']=='empty'


def test_source_integrity_failure_hides_source_title_but_retains_declared_review_candidates(store):
    dep=ProcessDependencies(store);a=process(store,'A');b=process(store,'B');dep.save(person(),a['workflow_id'],body(a,b))
    from gateway.process_collaboration import namespace as catalog_namespace
    with store.write_transaction() as c:
        row=dep.q(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(catalog_namespace('acme'),b['workflow_id'])).fetchone()
        payload=json.loads(row[0]);payload['title']='PRIVATE_TAMPERED_TITLE'
        dep.q(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',(json.dumps(payload),catalog_namespace('acme'),b['workflow_id']))
    result=graph(store,b['workflow_id']);assert not result['current_basis'] and result['impact']['potential_consumers']
    assert 'PRIVATE_TAMPERED_TITLE' not in json.dumps(result)


def test_revisions_bound_history_retained_and_confirmations_required(store):
    dep=ProcessDependencies(store);a=process(store);wid=a['workflow_id']
    for change in ('user_confirmed','organization_sharing_confirmed'):
        value=body(a);setattr(value,change,False)
        with pytest.raises(Error):dep.save(person(),wid,value)
    for i in range(20):dep.save(person(),wid,body(a,revision=i))
    first=dep.detail(person(),wid,1)['artifact']
    with pytest.raises(Error):dep.save(person(),wid,body(a,revision=20))
    assert dep.detail(person(),wid,1)['artifact']==first
    with store.db.connect() as c:assert dep.latest(c,person(),wid)==20


def test_atomic_audit_rollback_and_read_audit_failure_withholds_response(store,monkeypatch):
    dep=ProcessDependencies(store);a=process(store);wid=a['workflow_id'];original=store._audit
    def failure(*args,**kwargs):raise RuntimeError('audit unavailable')
    monkeypatch.setattr(store,'_audit',failure)
    with pytest.raises(RuntimeError):dep.save(person(),wid,body(a))
    with store.db.connect() as c:assert dep.latest(c,person(),wid)==0
    monkeypatch.setattr(store,'_audit',original);dep.save(person(),wid,body(a))
    monkeypatch.setattr(store.db,'audit',failure)
    with pytest.raises(RuntimeError):dep.graph(person())


def test_graph_scan_limit_never_returns_partial_impact(store):
    dep=ProcessDependencies(store)
    with store.write_transaction() as c:
        for i in range(101):
            wid='synthetic_'+str(i);d=Declaration(organization_id='acme',root_process={'workflow_id':wid,'revision':1,'content_sha256':'a'*64})
            raw=Saved(dependencies=d,author_actor_id='alice',saved_at='2026-10-27T00:00:00Z').model_dump(mode='json')
            dep.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',(namespace('acme'),wid,1,json.dumps({**raw,'content_sha256':content_digest(raw)})))
    result=graph(store);assert result['status']=='withheld' and not result['complete_declared_scan']
    assert result['graph'] is None and result['impact'] is None and result['blockers']==[{'code':'root_scan_limit'}]
    actual=process(store)
    with pytest.raises(Error):dep.save(person(),actual['workflow_id'],body(actual))


def test_global_node_bound_is_refused_without_partial_analysis():
    docs=[]
    for i in range(100):docs.append({'root_process':{'workflow_id':'R'+str(i)},'links':[{'id':'leaf','prerequisite':{'workflow_id':'L'+str(i)},'relationship':'uses_output'}]})
    assert len(topology(docs)['process_ids'])==200
    docs[0]['links'].append({'id':'extra','prerequisite':{'workflow_id':'one_too_many'},'relationship':'uses_policy'})
    with pytest.raises(ValueError,match='two hundred'):topology(docs)


def test_human_api_exact_export_preflight_strict_parser_and_fresh_graph_fingerprint(tmp_path):
    with TestClient(app(tmp_path)) as client:
        headers={'Authorization':'Bearer alice'}
        a=client.post('/v1/human/processes',headers=headers,json=write('A').model_dump()).json()['process']
        b=client.post('/v1/human/processes',headers=headers,json=write('B').model_dump()).json()['process']
        base='/v1/human/processes/'+a['workflow_id']+'/dependencies'
        assert client.post(base,headers={'Authorization':'Bearer reader'},content='not json').status_code==403
        assert client.post(base,headers={**headers,'Content-Type':'application/json'},content='{"expected_revision":0,"expected_revision":1}').status_code==422
        assert client.post(base,headers=headers,json=body(a,b).model_dump()).status_code==200
        current=client.get('/v1/human/process-dependency-graph',headers=headers).json()
        export='/v1/human/process-dependency-graph/export?fingerprint='+current['fingerprint']
        assert client.get(export,headers=headers).status_code==200
        assert client.get(base+'/export?revision=1',headers=headers).json()['artifact']['dependencies']['root_process']==pin(a)
        assert client.post(base,headers=headers,json=body(a,revision=1).model_dump()).status_code==200
        assert client.get(export,headers=headers).status_code==409
        script=client.get('/process-dependency-client.js');assert script.status_code==200 and script.headers['cache-control']=='no-store'
        assert client.get('/v1/human/process-dependency-graph',headers={'Authorization':'Bearer foreign'}).json()['status']=='empty'


def test_dependency_and_root_erasure_roll_back_together_with_audit_failure(store,monkeypatch):
    dep=ProcessDependencies(store);a=process(store);wid=a['workflow_id'];dep.save(person(),wid,body(a));artifact=dep.detail(person(),wid)['artifact'];original=store._audit
    def failure(*args,**kwargs):raise RuntimeError('audit unavailable')
    monkeypatch.setattr(store,'_audit',failure)
    with pytest.raises(RuntimeError):dep.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert dep.detail(person(),wid)['artifact']==artifact
    with pytest.raises(RuntimeError):store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert dep.detail(person(),wid)['artifact']==artifact
    monkeypatch.setattr(store,'_audit',original)
    assert store.detail(person(),wid)['process']==a


def test_read_snapshot_does_not_mix_model_versions_during_concurrent_commit(store):
    dep=ProcessDependencies(store);a=process(store);dep.save(person(),a['workflow_id'],body(a));approve(store,a)
    with dep.catalog.reading(person()) as c:
        first=dep.snapshot(c,person())
        store.save(person(),write('Concurrent revision',expected=1),a['workflow_id'])
        second=dep.snapshot(c,person())
        assert first==second and first['current_basis']
    assert graph(store)['fingerprint']!=first['fingerprint'] and not graph(store)['current_basis']


def test_wrong_org_root_digest_and_unknown_source_are_refused_without_rows(store):
    dep=ProcessDependencies(store);a=process(store);b=process(store,'B')
    for change in ('org','root','digest','missing'):
        value=body(a,b)
        if change=='org':value.dependencies.organization_id='foreign'
        if change=='root':value.dependencies.root_process.workflow_id=b['workflow_id']
        if change=='digest':value.dependencies.links[0].prerequisite.content_sha256='0'*64
        if change=='missing':value.dependencies.links[0].prerequisite.workflow_id='absent_process'
        with pytest.raises(Error):dep.save(person(),a['workflow_id'],value)
    assert graph(store)['status']=='empty'


def test_response_limit_withholds_whole_graph_and_impact(store,monkeypatch):
    import gateway.process_dependencies as module
    dep=ProcessDependencies(store);a=process(store);b=process(store,'B');dep.save(person(),a['workflow_id'],body(a,b))
    monkeypatch.setattr(module,'MAX_RESPONSE_BYTES',1)
    result=graph(store,b['workflow_id'])
    assert result['status']=='withheld' and result['graph'] is None and result['impact'] is None
    assert result['blockers']==[{'code':'response_byte_limit'}]


def test_separate_namespace_and_historical_binding_cannot_resolve_another_tenant(store):
    dep=ProcessDependencies(store);a=process(store);wid=a['workflow_id'];dep.save(person(),wid,body(a))
    assert namespace('acme')!=json.dumps(['acme','alice'])
    with store.db.connect() as c:
        original=dep.q(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace('acme'),wid)).fetchone()[0]
    foreign=json.loads(original);foreign['dependencies']['organization_id']='foreign';foreign.pop('content_sha256');foreign['content_sha256']=content_digest(foreign)
    with store.write_transaction() as c:dep.q(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',(json.dumps(foreign),namespace('acme'),wid))
    assert not dep.detail(person(),wid)['integrity_ok']
    assert graph(store)['status']=='withheld'
