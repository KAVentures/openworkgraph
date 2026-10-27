"""Declared OCEL object assertions survive independently of co-observed events."""
from copy import deepcopy
from io import BytesIO,StringIO
import json
import pytest
from shared import event_log_formats as formats
from server import event_log_workspace as workspace,event_log_quality as quality

FORMAT='openworkgraph.supplied-ocel-object-relationships.v1'

@pytest.fixture(autouse=True)
def local_store(tmp_path,monkeypatch):monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def document():
    return {'objectTypes':[{'name':'order','attributes':[]},{'name':'item','attributes':[]}],
        'eventTypes':[{'name':'Open','attributes':[]}],
        'objects':[{'id':'order1','type':'order','attributes':[],
                    'relationships':[{'objectId':'item1','qualifier':'contains'}]},
                   {'id':'item1','type':'item','attributes':[],'relationships':[]}],
        'events':[{'id':'event1','type':'Open','time':'2026-01-01T00:00:00Z','attributes':[],
                   'relationships':[{'objectId':'order1','qualifier':''}]}]}


def encoded(payload):return BytesIO(json.dumps(payload).encode())
def imported(payload=None):return workspace.import_log(encoded(payload or document()),name='Declared relationships',source_format='ocel')
def appended(log,payload):return workspace.append_log(log,encoded(payload),source_format='ocel')
def edges(log):
    conn=workspace._connect()
    try:return set(conn.execute('SELECT source_object_id,target_object_id,qualifier FROM object_relationships WHERE log_id=?',[log]).fetchall())
    finally:conn.close()


def snapshot():
    conn=workspace._connect()
    try:return {table:conn.execute('SELECT * FROM '+table+' ORDER BY ALL').fetchall() for table in
        ['logs','events','objects','event_objects','object_attribute_values','object_relationships','log_appends','log_quality']}
    finally:conn.close()


def test_failure_after_relationship_copy_rolls_back_every_table(monkeypatch):
    from server import event_log_object_relationships as relationships
    made=imported();before=snapshot();original=relationships.copy_append
    def fail(*args):
        original(*args)
        raise RuntimeError('Injected relationship copy failure')
    monkeypatch.setattr(relationships,'copy_append',fail)
    payload=document();payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'reverse'}]
    with pytest.raises(RuntimeError,match='Injected'):appended(made['log_id'],payload)
    assert snapshot()==before


def test_selected_unobserved_object_remains_inspectable_with_no_matching_edges():
    from server import event_log_object_relationships as relationships
    made=imported()
    result=relationships.query(made['log_id'],{'object_id':'item1','direction':'outgoing'})
    assert result['rows']==[] and result['matching_relationships']==0
    assert result['selected_object']=={'object_id':'item1','object_type':'item','observed_events':0}


def test_concurrent_relationship_replays_are_idempotent_and_leave_no_staging_rows():
    from concurrent.futures import ThreadPoolExecutor
    made=imported();payload=document();payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'reverse'}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:appended(made['log_id'],deepcopy(payload)),range(2)))
    assert sorted(result['added_object_relationships'] for result in results)==[0,1]
    assert all(result['added_events']==0 for result in results)
    assert len(edges(made['log_id']))==2
    conn=workspace._connect()
    try:assert conn.execute('SELECT count(DISTINCT log_id) FROM object_relationships').fetchone()[0]==1
    finally:conn.close()


def test_coded_case_disclosure_does_not_include_object_relationships_or_labels():
    from server import analysis_case_export as sharing
    payload=document();payload['objects'][0]['relationships'][0]['qualifier']='PRIVATE_RELATIONSHIP'
    log=imported(payload)['log_id']
    packet=sharing.prepare(log,{'level':'cases','case_type':'order','dimensions':[],'measures':[{'fn':'count'}]})
    text=json.dumps(packet)
    for secret in ['PRIVATE_RELATIONSHIP','order1','item1','object_relationships']:assert secret not in text
    assert packet['case_dataset']['cases']==[{'code':'C1','events':[{'activity':'A1','elapsed_microseconds':0}]}]


def test_real_http_auth_consent_strict_queries_roundtrip_and_stale_cursors(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
payload={'objectTypes':[],'eventTypes':[],'objects':[
 {'id':'o','type':'order','attributes':[],'relationships':[
  {'objectId':'i','qualifier':'contains'},{'objectId':'i','qualifier':''}]},
 {'id':'i','type':'item','attributes':[],'relationships':[]}],
 'events':[{'id':'e','type':'Open','time':'2026-01-01T00:00:00Z','attributes':[],
  'relationships':[{'objectId':'o','qualifier':''}]}]}
with TestClient(app) as client:
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    stored={**headers,'X-OWG-Local-Storage-Confirmed':'true'}
    url='/v1/event-logs/import?name=Relationships&format=ocel'
    assert client.post(url,json=payload,headers=headers).status_code==403
    made=client.post(url,json=payload,headers=stored);assert made.status_code==200,made.text
    path='/v1/event-logs/'+made.json()['log_id'];query=path+'/object-relationships/query'
    for auth in ({},{'Authorization':'OWG-Session wrong'},
                 {'Authorization':'Bearer '+local_auth.ensure_api_token()}):
        assert client.get(path+'/object-relationships',headers=auth).status_code in (401,403)
        assert client.post(query,json={},headers=auth).status_code in (401,403)
    overview=client.get(path+'/object-relationships',headers=headers)
    assert overview.status_code==200 and overview.json()['retained_relationships']==2
    assert overview.headers['cache-control']=='no-store'
    first=client.post(query,json={'limit':1},headers=headers)
    assert first.status_code==200 and first.json()['has_more']
    cursor=first.json()['next_cursor']
    second=client.post(query,json={'limit':1,'cursor':cursor},headers=headers)
    assert second.status_code==200 and not second.json()['has_more']
    assert first.json()['rows'][0]['qualifier']!=second.json()['rows'][0]['qualifier']
    assert client.post(query,json={'object_id':'i','direction':'outgoing'},headers=headers).json()['selected_object']['observed_events']==0
    for body in (b'{"limit":1,"limit":2}',b'{"limit":NaN}',b'{"limit":Infinity}',b'[]',b'{"cursor":{"revision":0,"revision":1}}'):
        assert client.post(query,content=body,headers={**headers,'Content-Type':'application/json'}).status_code==422
    assert client.post(query,content=b'{}',headers=headers).status_code==415
    assert client.post(query,content=b' '*65537,headers={**headers,'Content-Type':'application/json'}).status_code==413
    assert client.post(query,json={'object_id':'i','cursor':cursor},headers=headers).status_code==422
    # Appending zero events changes revision, not case membership.
    replay=client.post(path+'/append?format=ocel',json=payload,headers=stored)
    assert replay.status_code==200 and replay.json()['added_events']==0
    assert client.post(query,json={'limit':1,'cursor':cursor},headers=headers).status_code==422
    for format in ('ocel','ocelxml'):
        exported=client.get(path+'/export?format='+format,headers=headers)
        assert exported.status_code==200
        copied=client.post('/v1/event-logs/import?name=Roundtrip&format='+format,content=exported.content,headers=stored)
        assert copied.status_code==200 and copied.json()['object_relationships']['retained_relationships']==2,copied.text
    assert client.get(path+'/summary?case_type=order',headers=headers).json()['events']==1
    assert client.get(path+'/summary?case_type=item',headers=headers).json()['events']==0
    assert client.post(path+'/delete',headers=headers).status_code==200
    assert client.get(path+'/object-relationships',headers=headers).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr


def test_json_reader_retains_forward_object_references_even_without_target_events():
    rows=list(formats.read_ocel_json(encoded(document())))
    objects=[row for row in rows if isinstance(row,formats.ObjectRecord)]
    assert objects[0].relationships==[('item1','contains')] and objects[1].relationships==[]
    assert len([row for row in rows if isinstance(row,formats.EventRecord)])==1


def test_xml_reader_retains_the_original_20_specification_relationship_layout():
    xml=b'''<log><object-types/><event-types/><objects>
    <object id="order1" type="order"><attributes/><objects><relationship object-id="item1" qualifier="contains"/></objects></object>
    <object id="item1" type="item"><attributes/></object></objects><events>
    <event id="event1" type="Open" time="2026-01-01T00:00:00Z"><attributes/><objects><relationship object-id="order1" qualifier=""/></objects></event>
    </events></log>'''
    objects=[row for row in formats.read_ocel_xml(BytesIO(xml)) if isinstance(row,formats.ObjectRecord)]
    assert objects[0].relationships==[('item1','contains')]
    made=workspace.import_log(BytesIO(xml),name='Canonical XML',source_format='ocelxml')
    assert edges(made['log_id'])=={('order1','item1','contains')} and made['events']==1


def test_retained_assertions_have_separate_basis_and_do_not_create_events_or_case_steps():
    made=imported();log=made['log_id']
    assert made['events']==1 and made['objects']==2 and edges(log)=={('order1','item1','contains')}
    profile=made['object_relationships'];assert profile['format']==FORMAT and profile['retained_relationships']==1
    assert profile['basis']=='caller_supplied_ocel_object_relationships' and profile['independently_verified'] is False
    assert workspace.summary(log,'order')['events']==1
    assert len(list(workspace.case_events(log,'item')))==0
    from server import event_log_objects
    graph=event_log_objects.object_graph(log)
    assert graph['links_are_shared_events_not_verified_relationships'] is True
    assert graph['relations']==[]


@pytest.mark.parametrize('format',['ocel','ocelxml'])
def test_ocel_roundtrip_preserves_literal_directed_multiquallifier_cycles_and_self_edges(format):
    payload=document()
    payload['objects'][0]['relationships'] += [{'objectId':'item1','qualifier':''},{'objectId':'item1','qualifier':' '},
        {'objectId':'item1','qualifier':' contains '},{'objectId':'order1','qualifier':'self'}]
    payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'part-of'}]
    made=imported(payload);log=made['log_id'];expected={('order1','item1','contains'),('order1','item1',''),
        ('order1','item1',' '),('order1','item1',' contains '),('order1','order1','self'),('item1','order1','part-of')}
    assert edges(log)==expected
    out=StringIO();workspace.export_log(log,format,out)
    copy=workspace.import_log(BytesIO(out.getvalue().encode()),name='Roundtrip',source_format=format)
    assert edges(copy['log_id'])==expected and copy['events']==made['events']==1
    a=quality.quality(log)['current']['object_relationships'];b=quality.quality(copy['log_id'])['current']['object_relationships']
    assert a['relationships_sha256']==b['relationships_sha256']


def test_append_adds_relationship_sets_even_when_no_event_is_new_and_retries_are_idempotent():
    made=imported();payload=document();payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'part-of'}]
    result=appended(made['log_id'],payload)
    assert result['added_events']==0 and result['added_object_relationships']==1
    assert edges(made['log_id'])=={('order1','item1','contains'),('item1','order1','part-of')}
    again=appended(made['log_id'],payload)
    assert again['added_events']==0 and again['added_object_relationships']==0
    assert again['log']['events']==1 and len(workspace.append_history(made['log_id'])['appends'])==2


@pytest.mark.parametrize('relationships',[
    None,{},'bad',[None],[{}],[{'objectId':'item1'}],[{'qualifier':'contains'}],
    [{'objectId':'missing','qualifier':'contains'}],[{'objectId':12,'qualifier':'contains'}],
    [{'objectId':'item1','qualifier':None}],[{'objectId':'item1','qualifier':'x'*81}],
    [{'objectId':'item1','qualifier':'line\nbreak'}],[{'objectId':'item1','qualifier':'contains','validFrom':'2026-01-01T00:00:00Z'}],
])
def test_invalid_dangling_or_unsupported_relationships_refuse_the_entire_import(relationships):
    payload=document();payload['objects'][0]['relationships']=relationships
    with pytest.raises(workspace.EventLogWorkspaceError):imported(payload)
    assert workspace.list_logs()['logs']==[]


def test_conflicting_object_type_with_relationships_rolls_back_all_tables():
    made=imported();before=snapshot();payload=document();payload['objects'][1]['type']='another-type'
    with pytest.raises(workspace.EventLogWorkspaceError):appended(made['log_id'],payload)
    assert snapshot()==before


def test_edge_only_changes_have_a_separate_digest_without_changing_legacy_event_content_hash():
    bare=document();bare['objects'][0]['relationships']=[]
    plain=imported(bare);linked=imported(document())
    a=quality.quality(plain['log_id']);b=quality.quality(linked['log_id'])
    assert a['current']['content_sha256']==b['current']['content_sha256']
    assert 'object_relationships' not in a['current']
    assert b['current']['object_relationships']['relationships_sha256']
    assert 'object_relationships' not in plain and linked['object_relationships']['retained_relationships']==1


def test_deletion_removes_only_the_selected_logs_relationships():
    first=imported();second=imported();workspace.delete_log(first['log_id'])
    assert edges(first['log_id'])==set() and len(edges(second['log_id']))==1


def test_corrupt_endpoint_refuses_partial_export_and_append():
    made=imported();conn=workspace._connect()
    try:conn.execute('DELETE FROM objects WHERE log_id=? AND object_id=?',[made['log_id'],'item1'])
    finally:conn.close()
    out=StringIO()
    with pytest.raises(workspace.EventLogWorkspaceError):workspace.export_log(made['log_id'],'ocel',out)
    assert out.getvalue()==''
    before=snapshot()
    with pytest.raises(workspace.EventLogWorkspaceError):appended(made['log_id'],document())
    assert snapshot()==before


def test_queries_keep_direct_direction_and_zero_event_objects_and_bound_pages_to_current_revision():
    from server import event_log_object_relationships as relationships
    payload=document();payload['objects'][0]['relationships'].append({'objectId':'item1','qualifier':'second-role'})
    payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'part-of'}]
    made=imported(payload);log=made['log_id']
    profile=relationships.overview(log)
    assert profile['retained_relationships']==3 and profile['groups_total']==3 and not profile['groups_truncated']
    assert profile['shared_event_associations_are_separate']
    outgoing=relationships.query(log,{'object_id':'order1','direction':'outgoing'})
    assert len(outgoing['rows'])==2 and all(r['target_observed_events']==0 and r['source_observed_events']==1 for r in outgoing['rows'])
    incoming=relationships.query(log,{'object_id':'order1','direction':'incoming'})
    assert [r['qualifier'] for r in incoming['rows']]==['part-of']
    assert relationships.query(log,{'qualifier':'second-role'})['matching_relationships']==1
    first=relationships.query(log,{'limit':1});second=relationships.query(log,{'limit':1,'cursor':first['next_cursor']})
    third=relationships.query(log,{'limit':1,'cursor':second['next_cursor']})
    assert len({(r['source_id'],r['target_id'],r['qualifier']) for page in [first,second,third] for r in page['rows']})==3
    assert not third['has_more'] and third['next_cursor'] is None
    with pytest.raises(workspace.EventLogWorkspaceError):relationships.query(log,{'qualifier':'other','cursor':first['next_cursor']})
    appended(log,payload)
    with pytest.raises(workspace.EventLogWorkspaceError):relationships.query(log,{'cursor':first['next_cursor']})


@pytest.mark.parametrize('filters',[
    {'direction':'reverse'},{'direction':'incoming'},{'limit':0},{'limit':201},{'limit':True},
    {'object_id':' '},{'qualifier':'x'*81},{'object_id':'line\nbreak'},{'where':'DROP TABLE objects'},
    {'cursor':{'revision':0,'after':['x','y','z'],'query_sha256':'bad'}},
])
def test_invalid_or_unbounded_relationship_queries_are_refused(filters):
    from server import event_log_object_relationships as relationships
    made=imported()
    with pytest.raises(workspace.EventLogWorkspaceError):relationships.query(made['log_id'],filters)


def test_appended_union_relationship_limits_refuse_atomically(monkeypatch):
    from server import event_log_object_relationships as relationships
    made=imported();before=snapshot();monkeypatch.setattr(relationships,'MAX_RELATIONSHIPS',1)
    payload=document();payload['objects'][0]['relationships']=[]
    payload['objects'][1]['relationships']=[{'objectId':'order1','qualifier':'part-of'}]
    with pytest.raises(workspace.EventLogWorkspaceError,match='limit'):appended(made['log_id'],payload)
    assert snapshot()==before


def test_duplicate_assertions_are_sets_and_quality_failure_does_not_undo_the_graph(monkeypatch):
    payload=document();payload['objects'][0]['relationships']*=2
    monkeypatch.setattr(quality,'record',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('Synthetic diagnostics failure')))
    made=imported(payload)
    assert made['quality'] is None and edges(made['log_id'])=={('order1','item1','contains')}
    assert quality.quality(made['log_id'])['batches_without_report']==[0]


def test_group_overview_discloses_its_limit_without_losing_query_rows():
    from server import event_log_object_relationships as relationships
    payload=document();payload['objects'][0]['relationships']=[{'objectId':'item1','qualifier':str(i)} for i in range(201)]
    log=imported(payload)['log_id'];result=relationships.overview(log)
    assert result['groups_total']==201 and len(result['groups'])==200 and result['groups_truncated']
    first=relationships.query(log,{'limit':200})
    last=relationships.query(log,{'limit':200,'cursor':first['next_cursor']})
    assert len(first['rows'])==200 and len(last['rows'])==1
    assert len({r['qualifier'] for r in first['rows']+last['rows']})==201


def test_per_object_import_and_retained_union_caps_are_atomic(monkeypatch):
    from server import event_log_object_relationships as relationships
    made=imported();before=snapshot()
    monkeypatch.setattr(formats,'MAX_OBJECT_RELATIONSHIPS',1)
    payload=document();payload['objects'][0]['relationships']*=2
    with pytest.raises(workspace.EventLogWorkspaceError,match='bounded'):imported(payload)
    assert snapshot()==before
    monkeypatch.setattr(relationships,'MAX_OBJECT_RELATIONSHIPS',1)
    payload['objects'][0]['relationships']=[{'objectId':'item1','qualifier':'new-role'}]
    with pytest.raises(workspace.EventLogWorkspaceError,match='limit'):appended(made['log_id'],payload)
    assert snapshot()==before


@pytest.mark.parametrize('content',[
    '<objects><relationship object-id="item1" qualifier="a" valid-from="2026-01-01"/></objects>',
    '<objects><object object-id="item1" qualifier="a"/></objects>',
    '<objects/><objects/>',
    '<objects><relationship qualifier="a"/></objects>',
    '<objects><relationship object-id="missing" qualifier="a"/></objects>',
    '<objects validity="current"><relationship object-id="item1" qualifier="a"/></objects>',
    '<objects><relationship object-id="item1" qualifier="a"><valid-from>2026-01-01</valid-from></relationship></objects>',
    '<objects><relationship object-id="item1" qualifier="a">business-rule</relationship></objects>',
])
def test_unsupported_xml_links_are_not_silently_lost(content):
    xml=('<log><object-types/><event-types/><objects><object id="order1" type="order"><attributes/>'+content+
         '</object><object id="item1" type="item"><attributes/></object></objects><events>'
         '<event id="e" type="Open" time="2026-01-01T00:00:00Z"><attributes/><objects>'
         '<relationship object-id="order1" qualifier=""/></objects></event></events></log>')
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.import_log(BytesIO(xml.encode()),name='Unsupported XML',source_format='ocelxml')
    assert workspace.list_logs()['logs']==[]
