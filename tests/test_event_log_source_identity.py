"""Opted-in supplied event identities; legacy payload dedupe remains independent."""
from io import BytesIO,StringIO
import csv
import json
import pytest
from server import event_log_workspace as workspace
from server import event_log_quality as quality

MAPPING={'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who',
    'event_id_column':'event','source_namespace':'crm'}

@pytest.fixture(autouse=True)
def data_dir(tmp_path,monkeypatch):monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)

def file(rows):
    out=StringIO(newline='');writer=csv.writer(out);writer.writerow(['event','case','activity','time','who','region']);writer.writerows(rows)
    return BytesIO(out.getvalue().encode())

def row(event='e1',case='c1',activity='Open',time='2026-01-01T00:00:00Z',who='ann',region='north'):
    return [event,case,activity,time,who,region]

def import_rows(rows,mapping=None):return workspace.import_log(file(rows),name='Source events',source_format='csv',mapping=mapping or MAPPING)
def append(log,rows,mapping=None):return workspace.append_log(log,file(rows),source_format='csv',mapping=mapping or MAPPING)

def snapshot():
    conn=workspace._connect()
    try:
        return {table:conn.execute('SELECT * FROM '+table+' ORDER BY ALL').fetchall() for table in
            ['logs','events','objects','event_objects','log_appends','object_attribute_values','event_source_keys','event_source_namespaces']}
    finally:conn.close()


def test_distinct_ids_with_identical_payload_are_retained_and_exact_retries_deduplicate():
    made=import_rows([row('e1'),row('e2')]);log=made['log_id'];assert made['events']==2
    third=append(log,[row('e3')]);assert third['added_events']==1 and third['duplicate_events_skipped']==0
    retry=append(log,[row('e1'),row('e2'),row('e3')]);assert retry['added_events']==0 and retry['duplicate_events_skipped']==3
    assert workspace.list_logs()['logs'][0]['events']==3
    info=retry['source_identity'];assert info['deduplication_basis']=='declared_source_namespace_and_supplied_event_id'
    assert not info['provider_identity_independently_verified'] and not info['case_identity_independently_verified']


def test_repeated_keys_inside_a_batch_dedupe_only_matching_full_payloads():
    made=import_rows([row('e1'),row('e1'),row('e2')]);assert made['events']==2 and made['rejected_events']==0
    assert made['source_identity']['duplicate_source_ids_skipped']==1
    result=append(made['log_id'],[row('e1'),row('e1'),row('e3'),row('e3')])
    assert result['added_events']==1 and result['duplicate_events_skipped']==3
    assert result['source_identity']['duplicate_source_ids_skipped']==3


@pytest.mark.parametrize('field,value',[(1,'another-case'),(2,'Close'),(3,'2020-01-01T00:00:00Z'),(4,'bob'),(5,'south')])
def test_conflicting_id_reuse_refuses_the_whole_batch_even_outside_overlap_time_window(field,value):
    mapping={**MAPPING,'attribute_columns':['region']};made=import_rows([row()],mapping);before=snapshot()
    changed=row();changed[field]=value
    with pytest.raises(workspace.EventLogWorkspaceError,match='source event'):
        append(made['log_id'],[row('new'),changed],mapping)
    assert snapshot()==before


def test_source_and_case_namespaces_are_explicit_and_do_not_join_legacy_cases():
    made=import_rows([row()]);log=made['log_id']
    append(log,[row()],{**MAPPING,'source_namespace':'helpdesk'})
    paths=list(workspace.case_events(log,'source_case'));assert len(paths)==2
    assert {case for case,_ in paths}=={'src:crm:c1','src:helpdesk:c1'}
    with pytest.raises(workspace.EventLogWorkspaceError):append(log,[row('e2')],{**MAPPING,'case_namespace':'common'})
    common=import_rows([row()],{**MAPPING,'case_namespace':'common'})
    append(common['log_id'],[row('e2',activity='Close')],{**MAPPING,'source_namespace':'helpdesk','case_namespace':'common'})
    assert len(list(workspace.case_events(common['log_id'],'source_case')))==1
    legacy=import_rows([row(case='src:crm:c1')],{k:v for k,v in MAPPING.items() if k not in {'event_id_column','source_namespace'}})
    before=snapshot()
    with pytest.raises(workspace.EventLogWorkspaceError):append(legacy['log_id'],[row('e2')])
    assert snapshot()==before


def test_literal_ids_are_not_trimmed_or_truncated_into_one_identity():
    made=import_rows([row('e1'),row(' e1'),row('e1 ')]);assert made['events']==3
    result=append(made['log_id'],[row(' e1'),row('e1 ')]);assert result['duplicate_events_skipped']==2
    with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row(' '*3)])
    with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row('x'*201)])
    with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row(case='x'*200)])  # Namespaced ID cannot be truncated.


@pytest.mark.parametrize('mapping',[
    {**MAPPING,'source_namespace':''},{**MAPPING,'source_namespace':"x'; DROP TABLE events;--"},
    {**MAPPING,'source_namespace':'x'*41},{**MAPPING,'case_namespace':'has spaces'},
    {**MAPPING,'event_id_column':'missing'},{**MAPPING,'keep_rejected':True},
    {**MAPPING,'source_namespace_column':'region'},
])
def test_bad_or_ambiguous_identity_mapping_is_refused_before_any_persistent_rows(mapping):
    with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row()],mapping)
    assert workspace.list_logs()['logs']==[]


def test_invalid_rows_are_not_silently_dropped_from_a_source_identity_batch():
    for bad in [row('e2',time='not a date'),row('e2',case=''),row('e2',activity='')]:
        with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row(),bad])
    assert workspace.list_logs()['logs']==[]


def test_source_csv_roundtrip_preserves_namespaces_ids_and_attributes_without_double_prefixes():
    mapping={**MAPPING,'case_namespace':'common','attribute_columns':['region']}
    made=import_rows([row(case='src:original:c1',region='north,\nwest')],mapping);log=made['log_id']
    append(log,[row('e2',case='src:original:c1',activity='Close',region='south')],{**mapping,'source_namespace':'helpdesk'})
    exported=StringIO(newline='');workspace.export_log(log,'sourcecsv',exported,'source_case')
    text=exported.getvalue();rows=list(csv.DictReader(StringIO(text)))
    assert {r['source_namespace'] for r in rows}=={'crm','helpdesk'}
    assert {r['case_id'] for r in rows}=={'src:original:c1'} and all(r['case_namespace']=='common' for r in rows)
    assert json.loads(next(r for r in rows if r['source_namespace']=='crm')['attributes'])['region']=='north,\nwest'
    exported_mapping={'case_column':'case_id','activity_column':'activity','timestamp_column':'timestamp','resource_column':'resource',
        'event_id_column':'source_event_id','source_namespace_column':'source_namespace','case_namespace_column':'case_namespace','attributes_json_column':'attributes'}
    retry=workspace.append_log(log,BytesIO(text.encode()),source_format='csv',mapping=exported_mapping)
    assert retry['added_events']==0 and retry['duplicate_events_skipped']==2
    copy=workspace.import_log(BytesIO(text.encode()),name='Reimported source events',source_format='csv',mapping=exported_mapping)
    assert copy['events']==2 and len(list(workspace.case_events(copy['log_id'],'source_case')))==1
    report=quality.quality(log)['reports'][-1]
    assert report['transform']['mapping']['source_identity']['format']=='openworkgraph.supplied-source-event-identity.v1'


def test_source_mode_does_not_change_default_payload_dedupe_or_best_effort_quality(monkeypatch):
    legacy={k:v for k,v in MAPPING.items() if k not in {'event_id_column','source_namespace'}}
    made=import_rows([row('e1'),row('e2')],legacy);result=append(made['log_id'],[row('e3')],legacy)
    assert made['events']==2 and result['added_events']==0 and result['duplicate_events_skipped']==1
    assert 'source_identity' not in made and 'source_identity' not in result
    monkeypatch.setattr(quality,'record',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('Synthetic diagnostic failure')))
    another=import_rows([row('other')]);assert another['events']==1 and another['quality'] is None


def test_log_deletion_erases_only_its_source_key_index():
    first=import_rows([row()]);second=import_rows([row()]);workspace.delete_log(first['log_id'])
    conn=workspace._connect()
    try:
        assert conn.execute('SELECT count(*) FROM event_source_keys WHERE log_id=?',[first['log_id']]).fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM event_source_keys WHERE log_id=?',[second['log_id']]).fetchone()[0]==1
    finally:conn.close()


def test_conflicting_repeated_id_within_import_rolls_back_every_table():
    before=snapshot()
    with pytest.raises(workspace.EventLogWorkspaceError,match='conflicting payloads'):
        import_rows([row(),row(activity='Close')])
    assert snapshot()==before


@pytest.mark.parametrize('value',[0,False,[],{}])
def test_direct_mapping_rejects_nonstring_identity_values(value):
    with pytest.raises(workspace.EventLogWorkspaceError):import_rows([row()],{**MAPPING,'case_namespace':value})


@pytest.mark.parametrize('attrs',['null','[]','{"a":1}','{"a":true}','{"a":null}','{"a":"x","a":"y"}','{"":"x"}',json.dumps({'a':'x'*501})])
def test_json_attributes_refuse_unsupported_payloads_without_partial_import(attrs):
    with pytest.raises(workspace.EventLogWorkspaceError):
        import_rows([row(region=attrs)],{**MAPPING,'attributes_json_column':'region'})
    assert workspace.list_logs()['logs']==[]


def test_attribute_order_does_not_change_source_identity():
    mapping={**MAPPING,'attributes_json_column':'region'}
    made=import_rows([row(region='{"a":"x","b":"y"}')],mapping)
    assert append(made['log_id'],[row(region='{"b":"y","a":"x"}')],mapping)['added_events']==0


def test_namespace_limit_is_combined_across_appends_and_atomic():
    mapping={**MAPPING,'source_namespace':'','source_namespace_column':'region'}
    made=import_rows([row(region='ns'+str(i)) for i in range(20)],mapping)
    assert made['events']==20
    assert append(made['log_id'],[row(region='ns19')],mapping)['added_events']==0
    before=snapshot()
    with pytest.raises(workspace.EventLogWorkspaceError,match='twenty'):
        append(made['log_id'],[row('new',region='ns0'),row(region='ns20')],mapping)
    assert snapshot()==before


@pytest.mark.parametrize('corruption',[
    'DELETE FROM event_source_keys', 'DELETE FROM event_source_namespaces',
    'DELETE FROM event_objects', "UPDATE objects SET object_id='wrong'",
])
def test_broken_source_index_refuses_append_and_partial_export(corruption):
    made=import_rows([row()]);conn=workspace._connect()
    try:conn.execute(corruption)
    finally:conn.close()
    before=snapshot()
    with pytest.raises(workspace.EventLogWorkspaceError):append(made['log_id'],[row('new')])
    assert snapshot()==before
    out=StringIO()
    with pytest.raises(workspace.EventLogWorkspaceError):workspace.export_log(made['log_id'],'sourcecsv',out,'source_case')
    assert out.getvalue()==''


def test_source_index_copy_failure_rolls_back_entire_append(monkeypatch):
    from server import event_log_source_identity as identity
    made=import_rows([row()]);before=snapshot();original=identity.copy_append_keys
    def fail(*args):
        original(*args)
        raise RuntimeError('Injected post-index failure')
    monkeypatch.setattr(identity,'copy_append_keys',fail)
    with pytest.raises(RuntimeError,match='Injected'):append(made['log_id'],[row('new',case='new')])
    assert snapshot()==before


def test_real_http_source_identity_import_append_export_and_auth(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
payload=b'event,case,activity,time\ne1,c1,Open,2026-01-01T00:00:00Z\ne2,c1,Open,2026-01-01T00:00:00Z\n'
q='format=csv&case_column=case&activity_column=activity&timestamp_column=time&event_id_column=event&source_namespace=crm'
with TestClient(app) as client:
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    stored={**headers,'X-OWG-Local-Storage-Confirmed':'true'}
    url='/v1/event-logs/import?name=Source&'+q
    assert client.post(url,content=payload,headers=headers).status_code==403
    assert client.post(url,content=payload,headers={'Authorization':'Bearer '+local_auth.ensure_api_token(),'X-OWG-Local-Storage-Confirmed':'true'}).status_code in (401,403)
    made=client.post(url,content=payload,headers=stored);assert made.status_code==200,made.text
    assert made.json()['events']==2 and made.json()['source_identity']['source_events']==2
    path='/v1/event-logs/'+made.json()['log_id']
    replay=client.post(path+'/append?'+q,content=payload,headers=stored)
    assert replay.status_code==200 and replay.json()['duplicate_events_skipped']==2,replay.text
    conflict=client.post(path+'/append?'+q,content=payload.replace(b'Open',b'Changed'),headers=stored)
    assert conflict.status_code==422
    export=path+'/export?format=sourcecsv&case_type=source_case'
    assert client.get(export).status_code in (401,403)
    result=client.get(export,headers=headers);assert result.status_code==200,result.text
    assert result.headers['content-type'].startswith('text/csv') and '.csv' in result.headers['content-disposition']
    assert 'source_event_id' in result.text and 'e1' in result.text and 'e2' in result.text
    assert client.get(path+'/summary',headers=headers).json()['events']==2
    assert client.post(url+'&keep_rejected=true',content=payload,headers=stored).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr


def test_source_key_digest_is_separate_from_unchanged_content_digest():
    first=import_rows([row('key-one')]);second=import_rows([row('key-two')])
    a=quality.quality(first['log_id'])['current'];b=quality.quality(second['log_id'])['current']
    assert a['content_sha256']==b['content_sha256']
    assert a['source_identity_map_sha256']!=b['source_identity_map_sha256']
    assert first['quality']['source_identity']['format']=='openworkgraph.supplied-source-event-identity.v1'
    assert 'key-one' not in json.dumps(quality.quality(first['log_id']))


def test_concurrent_replays_commit_one_source_event_and_one_batch_each():
    from concurrent.futures import ThreadPoolExecutor
    made=import_rows([row()])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:append(made['log_id'],[row('new')]),range(2)))
    assert sorted(r['added_events'] for r in results)==[0,1]
    assert len(workspace.append_history(made['log_id'])['appends'])==2
    assert workspace.summary(made['log_id'])['events']==2


def test_source_mode_refuses_non_csv_and_accepts_explicit_naive_time_decision():
    with pytest.raises(workspace.EventLogWorkspaceError,match='only for CSV'):
        workspace.import_log(BytesIO(b'<log/>'),name='Unsupported',source_format='xes',mapping=MAPPING)
    made=import_rows([row(time='2026-01-01 00:00:00')],{**MAPPING,'naive_timestamp_offset':'+02:00'})
    assert append(made['log_id'],[row(time='2025-12-31T22:00:00Z')])['added_events']==0


def test_literal_quoted_sql_shaped_column_names_and_unicode_source_ids():
    names=['n','source_id','source_ns','case_ns','event"; DROP TABLE events;--']
    out=StringIO();writer=csv.writer(out);writer.writerow(names)
    writer.writerow(['c1','Open','2026-01-01T00:00:00Z','ann','事件-雪'])
    mapping=dict(zip(['case_column','activity_column','timestamp_column','resource_column','event_id_column'],names))
    made=workspace.import_log(BytesIO(out.getvalue().encode()),name='Quoted headers',source_format='csv',mapping={**mapping,'source_namespace':'crm'})
    assert made['events']==1
    exported=StringIO();workspace.export_log(made['log_id'],'sourcecsv',exported,'source_case')
    assert '事件-雪' in exported.getvalue()


def test_idless_ocel_append_cannot_bypass_managed_source_case_keys():
    made=import_rows([row()]);before=snapshot()
    payload={'objectTypes':[{'name':'source_case','attributes':[]}],'eventTypes':[{'name':'Close','attributes':[]}],
        'objects':[{'id':'src:crm:c1','type':'source_case','attributes':[]}],
        'events':[{'id':'external','type':'Close','time':'2026-01-02T00:00:00Z','attributes':[],
                   'relationships':[{'objectId':'src:crm:c1','qualifier':''}]}]}
    with pytest.raises(workspace.EventLogWorkspaceError,match='without a supplied'):
        workspace.append_log(made['log_id'],BytesIO(json.dumps(payload).encode()),source_format='ocel')
    assert snapshot()==before


def test_shared_coded_case_disclosure_omits_supplied_source_keys_and_namespaces():
    from server import analysis_case_export as sharing
    made=import_rows([row('PRIVATE_EVENT',case='PRIVATE_CASE',activity='PRIVATE_ACTIVITY',who='PRIVATE_PERSON')],
        {**MAPPING,'source_namespace':'PRIVATE_NAMESPACE'})
    packet=sharing.prepare(made['log_id'],{'level':'cases','case_type':'source_case','dimensions':[],'measures':[{'fn':'count'}]})
    encoded=json.dumps(packet)
    for secret in ['PRIVATE_EVENT','PRIVATE_CASE','PRIVATE_ACTIVITY','PRIVATE_PERSON','PRIVATE_NAMESPACE','2026-01-01']:
        assert secret not in encoded
    assert packet['case_dataset']['cases']==[{'code':'C1','events':[{'activity':'A1','elapsed_microseconds':0}]}]
