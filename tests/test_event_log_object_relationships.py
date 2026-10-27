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
