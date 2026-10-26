"""Pinned repository maps: model hashes, role boundaries, drift and serialized writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from gateway.process_hierarchies import ProcessHierarchies, namespace as map_namespace
from shared.process_hierarchy import Hierarchy, Save, Remove, content_digest
from gateway.process_collaboration import ProcessAccessWrite, ProcessDeleteWrite, ProcessArchiveWrite, ProcessCollaborationError
from test_process_collaboration import store, person, write, grant, SCOPES, review


def process(store,title='Root',who=None):return store.save(who or person(),write(title))['process']


def pin(record):return {k:record[k] for k in ('workflow_id','revision','content_sha256')}


def hierarchy(root,*children,**changes):
    result={'organization_id':'acme','title':'Service operations 雪','category':'Customer service','capability':'Case management',
        'nodes':[{'id':'root','parent_id':None,'process':pin(root)}]+[
            {'id':'n'+str(i),'parent_id':'root','process':pin(child)} for i,child in enumerate(children,1)]}
    return {**result,**changes}


def save_body(h,expected=0,access=1):return Save(hierarchy=h,expected_revision=expected,expected_access_revision=access,user_confirmed=True,organization_sharing_confirmed=True)


def test_same_store_exact_pins_history_export_and_unchanged_model(store):
    maps=ProcessHierarchies(store);root=process(store);child=process(store,'Child');wid=root['workflow_id'];h=hierarchy(root,child)
    assert maps.detail(person(),wid)['artifact'] is None
    made=maps.save(person(),wid,save_body(h))
    view=maps.detail(person('reader'),wid)
    assert view['all_references_current'] and view['declared_node_count']==2 and view['unique_referenced_processes']==2
    assert view['artifact']['hierarchy']['title']=='Service operations 雪'
    assert view['artifact']['author_actor_id']=='alice' and not view['can_write']
    assert all(n['independent_model_review']=='needs_review' for n in view['resolutions'])
    assert not view['execution_authorized'] and not view['model_reviews_propagated']
    assert not view['bpmn_call_semantics_included'] and not view['implementation_exports_include_hierarchy']
    assert store.detail(person(),wid)['process']==root
    assert store.implementation_state(person(),wid,revision=1)['process_reference']['content_sha256']==root['content_sha256']
    first=view['artifact'];h['category']='Finance';maps.save(person(),wid,save_body(h,1))
    assert maps.detail(person(),wid,1,exporting=True)['artifact']==first
    assert maps.detail(person(),wid)['artifact']['revision']==2
    with store.db.connect() as c:
        assert store.db._execute(c,'SELECT count(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(map_namespace('acme'),wid)).fetchone()[0]==2
        audit=[json.loads(r[0]) for r in store.db._execute(c,"SELECT details_json FROM audit_log WHERE action LIKE ?",('process.hierarchy.%',)).fetchall()]
    assert audit and all('hierarchy' not in item and 'title' not in item and 'category' not in item for item in audit)


def test_reusable_tree_four_levels_and_parent_links(store):
    records=[process(store,str(i)) for i in range(5)];maps=ProcessHierarchies(store);h=hierarchy(records[0],records[1],records[2])
    h['nodes'] += [{'id':'deep','parent_id':'n1','process':pin(records[3])},{'id':'leaf','parent_id':'deep','process':pin(records[4])},
                   {'id':'reused','parent_id':'n2','process':pin(records[3])}]
    maps.save(person(),records[0]['workflow_id'],save_body(h))
    view=maps.detail(person(),records[0]['workflow_id']);assert view['declared_node_count']==6 and view['unique_referenced_processes']==5
    invalid=deepcopy(h);invalid['nodes'].append({'id':'too_deep','parent_id':'leaf','process':pin(records[2])})
    with pytest.raises(ValidationError,match='four total levels'):Hierarchy.model_validate(invalid)
    invalid=deepcopy(h);invalid['nodes'][1]['parent_id']='deep'
    with pytest.raises(ValidationError):Hierarchy.model_validate(invalid)
    invalid=deepcopy(h);invalid['nodes'][3]['process']=pin(records[0])
    with pytest.raises(ValidationError,match='cycle'):Hierarchy.model_validate(invalid)


@pytest.mark.parametrize('mutate',[
    lambda h:h['nodes'].append(deepcopy(h['nodes'][1])),
    lambda h:h['nodes'][1].update(parent_id='absent'),
    lambda h:h['nodes'][1].update(parent_id=None),
    lambda h:h['nodes'].append({'id':'duplicate','parent_id':'root','process':deepcopy(h['nodes'][1]['process'])}),
    lambda h:h.update(executable=True),lambda h:h.update(benefit_rollup_included=True),
    lambda h:h.update(title='\ud800'),lambda h:h.update(category='bad\nvalue'),lambda h:h.update(title='   '),
    lambda h:h['nodes'][1]['process'].update(revision=True),
    lambda h:h['nodes'].extend([deepcopy(h['nodes'][1])]*99),
])
def test_invalid_or_executable_structure_refused_before_storage(store,mutate):
    root=process(store);h=hierarchy(root,process(store,'child'));mutate(h)
    with pytest.raises((ValidationError,UnicodeError)):save_body(h)
    assert ProcessHierarchies(store).detail(person(),root['workflow_id'])['artifact'] is None


def test_scope_org_root_ownership_edit_and_live_revocation(store):
    maps=ProcessHierarchies(store);root=process(store);child=process(store,'child');wid=root['workflow_id'];h=hierarchy(root,child)
    for principal in [person('bob'),person('alice',scopes=frozenset({'org:process:read'})),person(org='other')]:
        with pytest.raises(ProcessCollaborationError):maps.save(principal,wid,save_body(h))
    for principal in [person(org='other'),person(scopes=frozenset())]:
        with pytest.raises(ProcessCollaborationError):maps.detail(principal,wid)
    grant(store,wid,members=[{'actor_id':'bob','role':'editor'}])
    with pytest.raises(ProcessCollaborationError):maps.save(person('bob'),wid,save_body(h))  # stale access counter
    maps.save(person('bob'),wid,save_body(h,access=2))
    assert maps.detail(person(),wid)['artifact']['author_actor_id']=='bob'
    with pytest.raises(ProcessCollaborationError):maps.delete(person('bob'),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    grant(store,wid,members=[],expected=2)
    with pytest.raises(ProcessCollaborationError):maps.save(person('bob'),wid,save_body(h,1,3))
    assert maps.detail(person('bob'),wid)['artifact']  # existing tenant-wide reading, not per-process view ACL
    foreign=deepcopy(h);foreign['organization_id']='other'
    with pytest.raises(ProcessCollaborationError):maps.save(person(),wid,save_body(foreign,1,3))
    foreign_child=process(store,'elsewhere',person(org='other'));foreign=hierarchy(root,foreign_child)
    with pytest.raises(ProcessCollaborationError):maps.save(person(),wid,save_body(foreign,1,3))


def test_child_revision_review_archive_delete_and_exact_history(store):
    maps=ProcessHierarchies(store);root=process(store);child=process(store,'Child');wid=root['workflow_id'];cid=child['workflow_id'];h=hierarchy(root,child)
    maps.save(person(),wid,save_body(h));old=maps.detail(person(),wid)['artifact']
    grant(store,cid,members=[{'actor_id':'bob','role':'reviewer'}]);store.review(person('bob'),cid,review(child))
    assert maps.detail(person(),wid)['resolutions'][1]['active_latest_independently_reviewed_model']
    store.save(person(),write('Changed child',1),cid)
    view=maps.detail(person(),wid);info=view['resolutions'][1]
    assert info['state']=='revision_changed' and info['title']=='Child' and info['latest_revision']==2
    assert info['independent_model_review']=='reviewed_design' and not info['active_latest_independently_reviewed_model']
    assert view['artifact']==old and not view['all_references_current']
    store.archive(person(),cid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    assert maps.detail(person(),wid)['resolutions'][1]['state']=='archived'
    with pytest.raises(ProcessCollaborationError):maps.save(person(),wid,save_body(h,1))
    store.delete(person(),cid,ProcessDeleteWrite(expected_revision=2,expected_access_revision=3,user_confirmed=True))
    view=maps.detail(person(),wid,1,exporting=True);info=view['resolutions'][1]
    assert info['state']=='unavailable' and 'title' not in info and view['artifact']==old


def test_root_change_requires_fresh_pin_and_root_delete_erases_only_owned_map(store):
    maps=ProcessHierarchies(store);root=process(store);child=process(store,'Child');wid=root['workflow_id'];cid=child['workflow_id']
    maps.save(person(),wid,save_body(hierarchy(root,child)))
    maps.save(person(),cid,save_body(hierarchy(child)))
    updated=store.save(person(),write('Root2',1),wid)['process']
    with pytest.raises(ProcessCollaborationError,match='Root process changed'):maps.save(person(),wid,save_body(hierarchy(root,child),1))
    maps.save(person(),wid,save_body(hierarchy(updated,child),1))
    store.delete(person(),wid,ProcessDeleteWrite(expected_revision=2,expected_access_revision=1,user_confirmed=True))
    with store.db.connect() as c:
        assert store.db._execute(c,'SELECT count(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(map_namespace('acme'),wid)).fetchone()[0]==0
    assert maps.detail(person(),cid)['artifact']


def test_corruption_diagnostics_export_denial_and_owner_erasure(store):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];maps.save(person(),wid,save_body(hierarchy(root)))
    with store.db.connect() as c:store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"broken":true}',map_namespace('acme'),wid))
    view=maps.detail(person(),wid)
    assert not view['integrity_ok'] and view['artifact'] is None and view['can_delete'] and not view['can_write']
    assert view['declared_node_count'] is None
    with pytest.raises(ProcessCollaborationError):maps.detail(person(),wid,1,exporting=True)
    item=maps.inventory(person())['maps'][0];assert not item['integrity_ok'] and 'category' not in item
    maps.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert maps.detail(person(),wid)['latest_revision']==0 and store.detail(person(),wid)['process']==root


def test_child_corruption_withholds_labels_without_erasing_pins(store):
    from gateway.process_collaboration import namespace
    maps=ProcessHierarchies(store);root=process(store);child=process(store,'SECRET');wid=root['workflow_id'];maps.save(person(),wid,save_body(hierarchy(root,child)))
    with store.db.connect() as c:store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"broken":true}',namespace('acme'),child['workflow_id']))
    info=maps.detail(person(),wid)['resolutions'][1]
    assert info['state']=='integrity_failed' and 'title' not in info


def test_compare_and_swap_and_twenty_revisions_without_pruning(store):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];h=hierarchy(root)
    def save(_):
        try:maps.save(person(),wid,save_body(h));return 'saved'
        except ProcessCollaborationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(save,range(2)))==['conflict','saved']
    for revision in range(1,20):maps.save(person(),wid,save_body(h,revision))
    with pytest.raises(ProcessCollaborationError,match='twenty revisions'):maps.save(person(),wid,save_body(h,20))
    assert maps.detail(person(),wid,1)['artifact']['revision']==1
    with pytest.raises(ProcessCollaborationError):maps.delete(person(),wid,Remove(expected_revision=19,expected_access_revision=1,user_confirmed=True))


def test_shared_children_lock_order_no_deadlock(store):
    maps=ProcessHierarchies(store);a=process(store,'A');b=process(store,'B')
    def run(pair):
        root,child=pair
        return maps.save(person(),root['workflow_id'],save_body(hierarchy(root,child)))['revision']
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(run,[(a,b),(b,a)]))==[1,1]
    # Maps are independent decompositions, not implicit recursively expanded dependencies.
    assert maps.detail(person(),a['workflow_id'])['declared_node_count']==2


def test_audit_failure_rolls_back_save_delete_and_withholds_export(store,monkeypatch):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];h=hierarchy(root)
    def fail(*args,**kwargs):raise RuntimeError('audit unavailable')
    original=store._audit;monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(RuntimeError):maps.save(person(),wid,save_body(h))
    monkeypatch.setattr(store,'_audit',original)
    assert maps.detail(person(),wid)['artifact'] is None
    maps.save(person(),wid,save_body(h));monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(RuntimeError):maps.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    monkeypatch.setattr(store,'_audit',original)
    assert maps.detail(person(),wid)['artifact']
    monkeypatch.setattr(store.db,'audit',fail)
    with pytest.raises(RuntimeError):maps.detail(person(),wid,1,exporting=True)


def test_archived_root_can_erase_map_but_cannot_edit(store):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];h=hierarchy(root);maps.save(person(),wid,save_body(h))
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    view=maps.detail(person(),wid);assert view['can_delete'] and not view['can_write']
    with pytest.raises(ProcessCollaborationError):maps.save(person(),wid,save_body(h,1,2))
    maps.preflight(person(),wid,writing=True,owner=True)
    maps.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert maps.detail(person(),wid)['artifact'] is None


def test_inventory_keyset_pages_and_corrupt_history_do_not_hide_old_valid_revision(store):
    maps=ProcessHierarchies(store);a=process(store,'A');b=process(store,'B')
    for root in [a,b]:maps.save(person(),root['workflow_id'],save_body(hierarchy(root)))
    first=maps.inventory(person(),limit=1);assert len(first['maps'])==1 and first['has_more']
    second=maps.inventory(person(),after=first['next_after'],limit=1)
    assert not second['has_more'] and first['maps'][0]['workflow_id']!=second['maps'][0]['workflow_id']
    wid=a['workflow_id'];maps.save(person(),wid,save_body(hierarchy(a),1));old=maps.detail(person(),wid,1)['artifact']
    with store.db.connect() as c:store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=? AND revision=2',('{}',map_namespace('acme'),wid))
    view=maps.detail(person(),wid,1);assert view['artifact']==old and view['latest_revision']==2


def test_real_http_authorization_bounded_json_history_export_and_lifecycle(tmp_path):
    from fastapi.testclient import TestClient
    from test_process_collaboration import app
    with TestClient(app(tmp_path)) as client:
        headers=lambda who:{'Authorization':'Bearer '+who}
        make=lambda title:client.post('/v1/human/processes',headers=headers('alice'),json=write(title).model_dump()).json()['process']
        root=make('Root');child=make('Child');base='/v1/human/processes/'+root['workflow_id']+'/hierarchy';body=save_body(hierarchy(root,child)).model_dump(mode='json')
        for who,status in [('no-process',403),('reader',403),('bob',403),('foreign',404),('admin',401)]:
            r=client.post(base,headers={**headers(who),'Content-Type':'application/json'},content=b'bad JSON')
            assert r.status_code==status,(who,r.text)
        assert client.post(base,headers={**headers('alice'),'Content-Type':'application/json'},content=b'x'*65537).status_code==413
        assert client.post(base,headers={**headers('alice'),'Content-Type':'application/json'},content=b'{"user_confirmed":true,"user_confirmed":false}').status_code==422
        assert client.post(base,headers=headers('alice'),json={**body,'sql':'PRIVATE'}).status_code==422
        response=client.post(base,headers=headers('alice'),json=body);assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        for suffix in ['', '?revision=1','/export?revision=1']:
            assert client.get(base+suffix,headers=headers('foreign')).status_code==404
            assert client.get(base+suffix,headers=headers('reader')).status_code==200
            assert client.get(base+suffix,headers=headers('admin')).status_code==401
        exported=client.get(base+'/export?revision=1',headers=headers('reader'));assert 'attachment' in exported.headers['content-disposition']
        assert exported.json()['artifact']['hierarchy']['nodes'][1]['process']==pin(child)
        assert client.get('/v1/human/process-hierarchies',headers=headers('reader')).json()['maps'][0]['title']=='Service operations 雪'
        assert client.post(base,headers=headers('alice'),json=body).status_code==409
        assert client.post(base+'/delete',headers=headers('reader'),json={}).status_code==403
        assert client.post(base+'/delete',headers=headers('alice'),json=Remove(expected_revision=1,expected_access_revision=1,user_confirmed=True).model_dump()).json()=={'deleted':True}
        assert client.get(base+'/export?revision=1',headers=headers('alice')).status_code==409
        assert client.get('/process-hierarchy-client.js').headers['x-content-type-options']=='nosniff'
        page=client.get('/processes');assert 'process-hierarchy-client.js' in page.text and "'unsafe-inline'" not in page.headers['content-security-policy']


def test_maximum_hundred_node_map_has_exact_coverage_and_101_is_refused(store):
    maps=ProcessHierarchies(store);records=[process(store,'Boundary '+str(i)) for i in range(100)]
    root=records[0];h=hierarchy(root,*records[1:]);maps.save(person(),root['workflow_id'],save_body(h))
    view=maps.detail(person(),root['workflow_id'],1,exporting=True)
    assert view['declared_node_count']==100 and view['unique_referenced_processes']==100
    assert len(view['resolutions'])==100 and all(n['current'] for n in view['resolutions'])
    assert {n['content_sha256'] for n in view['resolutions']}=={r['content_sha256'] for r in records}
    h['nodes'].append({'id':'extra','parent_id':'root','process':pin(records[1])})
    with pytest.raises(ValidationError):save_body(h,1)
    assert maps.detail(person(),root['workflow_id'])['artifact']['revision']==1


def test_namespace_cannot_be_addressed_as_a_personal_two_component_owner(store):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];maps.save(person(),wid,save_body(hierarchy(root)))
    personal_owner=json.dumps(['organization-process-hierarchies','acme'],separators=(',',':'))
    assert store.knowledge.list(personal_owner)['workflows']==[]
    assert store.knowledge.history(personal_owner,wid)['workflows']==[]
    assert maps.detail(person(),wid)['artifact']['revision']==1


def test_unattributed_process_revision_is_not_accepted_as_a_shared_model(store):
    from gateway.process_collaboration import namespace
    from shared.workflow_knowledge import KnowledgeWrite
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id']
    # Simulate a legacy-store write that was not published through the shared-process role/audit path.
    inserted=store.knowledge.save(namespace('acme'),KnowledgeWrite(workflow_id=wid,title='Unattributed',procedure='Declared only',
        process_model=root['process_model'],expected_revision=1,user_confirmed=True,source_client='fixture'))
    h=hierarchy(inserted)
    with pytest.raises(ProcessCollaborationError,match='available active'):maps.save(person(),wid,save_body(h))


def test_legacy_shared_namespace_migration_preserves_exact_exports_and_reviews(store):
    from gateway.process_collaboration import namespace, OrganizationProcessStore
    from shared.process_catalog import ProcessCatalog
    root=process(store);wid=root['workflow_id'];grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}]);store.review(person('bob'),wid,review(root))
    before=ProcessCatalog(store.knowledge,namespace('acme')).export(wid,1)
    legacy=json.dumps(['organization-process-catalog','acme'],separators=(',',':'))
    with store.db.connect() as c:
        store.db._execute(c,'UPDATE workflow_knowledge SET owner=? WHERE owner=? AND workflow_id=?',(legacy,namespace('acme'),wid))
        store.db._execute(c,'DELETE FROM gateway_process_storage_migrations')
    assert store.knowledge.history(legacy,wid)['workflows']
    upgraded=OrganizationProcessStore(store.db);upgraded.initialize();upgraded.initialize()
    assert upgraded.detail(person(),wid)['process']==root
    assert upgraded.detail(person(),wid)['review']['status']=='reviewed_design'
    assert ProcessCatalog(upgraded.knowledge,namespace('acme')).export(wid,1)==before
    assert upgraded.knowledge.history(legacy,wid)['workflows']==[]
    with store.db.connect() as c:
        assert store.db._execute(c,'SELECT moved_revisions FROM gateway_process_storage_migrations').fetchone()[0]==1


def test_personal_collision_cannot_read_write_or_forget_new_shared_records(store):
    from gateway.process_collaboration import namespace, OrganizationProcessStore
    from shared.workflow_knowledge import KnowledgeWrite
    root=process(store);wid=root['workflow_id'];legacy=json.dumps(['organization-process-catalog','acme'],separators=(',',':'))
    with store.db.connect() as c:
        store.db._execute(c,'UPDATE workflow_knowledge SET owner=? WHERE owner=? AND workflow_id=?',(legacy,namespace('acme'),wid))
        store.db._execute(c,'DELETE FROM gateway_process_storage_migrations')
    personal=store.knowledge.save(legacy,KnowledgeWrite(workflow_id=wid,title='Personal only',procedure='My own explanation',process_model=root['process_model'],
        source_client='personal fixture',expected_revision=1,user_confirmed=True))
    upgraded=OrganizationProcessStore(store.db);upgraded.initialize()
    assert upgraded.detail(person(),wid)['process']==root
    assert upgraded.knowledge.history(legacy,wid,revision=1)['workflows']==[]
    assert upgraded.knowledge.history(legacy,wid,revision=2)['workflows'][0]==personal
    upgraded.knowledge.forget(legacy,wid)
    assert upgraded.detail(person(),wid)['process']==root
    upgraded.knowledge.save(legacy,KnowledgeWrite(workflow_id=wid,title='Personal new',procedure='Still personal',source_client='fixture',user_confirmed=True))
    assert upgraded.detail(person(),wid)['process']==root
    upgraded.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert upgraded.knowledge.history(legacy,wid)['workflows'][0]['title']=='Personal new'


def test_namespace_conflict_aborts_migration_without_overwriting_or_completing(store):
    from gateway.process_collaboration import namespace, OrganizationProcessStore
    root=process(store);wid=root['workflow_id'];legacy=json.dumps(['organization-process-catalog','acme'],separators=(',',':'))
    with store.db.connect() as c:
        payload=store.db._execute(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace('acme'),wid)).fetchone()[0]
        store.db._execute(c,'INSERT INTO workflow_knowledge VALUES (?,?,?,?)',(legacy,wid,1,'conflicting bytes'))
        store.db._execute(c,'DELETE FROM gateway_process_storage_migrations')
    with pytest.raises(RuntimeError,match='Conflicting shared process namespace'):OrganizationProcessStore(store.db).initialize()
    with store.db.connect() as c:
        assert store.db._execute(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace('acme'),wid)).fetchone()[0]==payload
        assert store.db._execute(c,'SELECT count(*) FROM gateway_process_storage_migrations').fetchone()[0]==0
        assert store.db._execute(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(legacy,wid)).fetchone()[0]=='conflicting bytes'


def test_review_for_different_content_never_approves_current_record(store):
    root=process(store);wid=root['workflow_id'];grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}]);store.review(person('bob'),wid,review(root))
    assert store.detail(person(),wid)['review']['status']=='reviewed_design'
    with store.db.connect() as c:store.db._execute(c,'UPDATE gateway_process_reviews SET content_sha256=? WHERE organization_id=? AND workflow_id=?',('0'*64,'acme',wid))
    assert store.detail(person(),wid)['review']['status']=='needs_review'
    assert not store.implementation_state(person(),wid,revision=1)['current_independent_design_review']


def test_process_admin_still_requires_read_write_and_current_tenant(store):
    maps=ProcessHierarchies(store);root=process(store);wid=root['workflow_id'];h=hierarchy(root)
    admin=person('admin',scopes=SCOPES|{'org:process:admin'})
    for scopes in [{'org:process:admin'},{'org:process:admin','org:process:read'}]:
        with pytest.raises(ProcessCollaborationError):maps.save(person('admin',scopes=frozenset(scopes)),wid,save_body(h))
    with pytest.raises(ProcessCollaborationError):maps.save(person('admin',org='other',scopes=admin.scopes),wid,save_body(h))
    maps.save(admin,wid,save_body(h));assert maps.detail(admin,wid)['can_delete']
    maps.delete(admin,wid,Remove(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert maps.detail(person(),wid)['artifact'] is None


def test_unattributed_legacy_root_does_not_become_shared_by_migration(store):
    from gateway.process_collaboration import namespace, OrganizationProcessStore
    root=process(store);wid=root['workflow_id'];legacy=json.dumps(['organization-process-catalog','acme'],separators=(',',':'))
    with store.db.connect() as c:
        store.db._execute(c,'UPDATE workflow_knowledge SET owner=? WHERE owner=? AND workflow_id=?',(legacy,namespace('acme'),wid))
        store.db._execute(c,'DELETE FROM gateway_process_authors WHERE organization_id=? AND workflow_id=?',('acme',wid))
        store.db._execute(c,'DELETE FROM gateway_process_storage_migrations')
    with pytest.raises(RuntimeError,match='lacks publication attribution'):OrganizationProcessStore(store.db).initialize()
    assert store.knowledge.history(legacy,wid)['workflows'][0]==root
    with store.db.connect() as c:assert store.db._execute(c,'SELECT count(*) FROM gateway_process_storage_migrations').fetchone()[0]==0
