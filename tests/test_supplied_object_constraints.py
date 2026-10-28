"""Retained supplied graph degree constraints must not become co-event joins."""
from io import BytesIO
import hashlib
import json
import pytest
from server import event_log_workspace as workspace,event_log_object_constraints as constraints,event_log_quality as quality


@pytest.fixture(autouse=True)
def local(tmp_path,monkeypatch):monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def payload():
    objects=[{'id':name,'type':kind,'attributes':[],'relationships':[]} for kind,names in
             [('order',['o1','o2','o3']),('item',['i1','i2','i3','i4'])] for name in names]
    links={'o1':[('i1','contains'),('i1','alternative'),('i2',''),('i2',' ')],
           'o3':[('i3','contains')],'i1':[('o1','reverse')],'i2':[('i2','self')]}
    for o in objects:o['relationships']=[{'objectId':t,'qualifier':q} for t,q in links.get(o['id'],[])]
    return {'objectTypes':[],'eventTypes':[],'objects':objects,'events':[
        {'id':'e1','type':'Open','time':'2026-01-01T00:00:00Z','attributes':[],
         'relationships':[{'objectId':'o1','qualifier':''}]},
        {'id':'e2','type':'Open','time':'2026-01-01T00:01:00Z','attributes':[],
         'relationships':[{'objectId':o,'qualifier':''} for o in ['o2','i4']]}]}


def imported(doc=None):return workspace.import_log(BytesIO(json.dumps(doc or payload()).encode()),name='Owned graph degree',source_format='ocel')['log_id']
def rule(**changes):return {'template':'supplied_object_cardinality','object_type':'order','related_type':'item','min':1,'max':1,**changes}
def checked(log,*rules):return constraints.check(log,{'rules':list(rules)})
def details(result):return {r['unit']:r['detail'] for r in result['violating_examples']}


def test_supplied_shape_differs_from_coevent_pairs_and_includes_zero_event_objects():
    log=imported();r=checked(log,rule())
    result=r['results'][0]
    assert (result['units'],result['activated'],result['violating'],result['satisfied'])==(3,3,2,1)
    assert details(result)=={'o1':'2 related','o2':'0 related'}
    legacy=checked(log,{'template':'object_cardinality','object_type':'order','related_type':'item','min':1,'max':1})
    assert details(legacy['results'][0])=={'o1':'0 related','o3':'0 related'}
    assert 'supplied_relationship_basis' not in legacy
    basis=r['supplied_relationship_basis']
    assert basis['retained_relationships']==7 and basis['revision']==0
    assert basis['relationships_sha256']==quality.quality(log)['current']['object_relationships']['relationships_sha256']
    assert r['execution_authorized'] is False and result['independently_verified'] is False
    assert result['business_compliance']=='unknown' and result['relationship_validity']=='unknown'
    assert result['violation_rate_ci95'] is None
    assert workspace.summary(log,'order')['events']==2
    assert len(list(workspace.case_events(log,'item')))==1


@pytest.mark.parametrize('direction,expected',[
    ('outgoing',{'o1':'2 related','o2':'0 related'}),
    ('incoming',{'o2':'0 related','o3':'0 related'}),
    ('both',{'o1':'2 related','o2':'0 related'}),
])
def test_direction_is_literal_and_multiple_roles_or_opposite_edges_do_not_duplicate_neighbors(direction,expected):
    assert details(checked(imported(),rule(direction=direction))['results'][0])==expected


@pytest.mark.parametrize('qualifier,expected',[
    ('contains',{'o2':'0 related'}),('',{'o2':'0 related','o3':'0 related'}),
    (' ',{'o2':'0 related','o3':'0 related'}),('absent',{'o1':'0 related','o2':'0 related','o3':'0 related'}),
])
def test_exact_qualifiers_including_empty_and_whitespace_are_not_normalized(qualifier,expected):
    assert details(checked(imported(),rule(qualifier=qualifier))['results'][0])==expected


def test_same_type_self_links_are_one_distinct_neighbor():
    result=checked(imported(),rule(object_type='item',related_type='item',min=0,max=0,direction='both'))['results'][0]
    assert result['units']==4 and details(result)=={'i2':'1 related'}


def test_empty_graph_has_a_real_empty_set_digest_not_source_completeness():
    doc=payload()
    for o in doc['objects']:o['relationships']=[]
    result=checked(imported(doc),rule())
    assert result['results'][0]['violating']==3
    assert result['supplied_relationship_basis']['retained_relationships']==0
    assert result['supplied_relationship_basis']['relationships_sha256']==hashlib.sha256(b'').hexdigest()
    assert result['results'][0]['source_completeness']=='unknown'


def test_absent_types_are_disclosed_without_fabricated_units():
    result=checked(imported(),rule(object_type='missing'))['results'][0]
    assert result['object_type_in_log'] is False and result['units']==0 and result['violation_rate'] is None


def test_literal_type_labels_are_not_trimmed_into_the_same_population():
    doc=payload()
    for o in doc['objects']:
        if o['id'] in ['o1','o3']:o['type']=' order '
    result=checked(imported(doc),rule(object_type=' order '))['results'][0]
    assert result['units']==2 and result['violating']==1
    assert repr(' order ') in result['description']


def test_appended_assertion_changes_graph_pin_without_changing_event_digest():
    doc=payload();log=imported(doc);before=checked(log,rule())['supplied_relationship_basis']
    content=quality.quality(log)['current']['content_sha256']
    doc['objects'][1]['relationships']=[{'objectId':'i4','qualifier':'contains'}]
    appended=workspace.append_log(log,BytesIO(json.dumps(doc).encode()),source_format='ocel')
    assert appended['added_events']==0 and appended['added_object_relationships']==1
    after=checked(log,rule())['supplied_relationship_basis']
    assert after['revision']==1 and before['relationships_sha256']!=after['relationships_sha256']
    assert quality.quality(log)['current']['content_sha256']==content


def test_assertion_free_object_append_pins_changed_focus_population_separately():
    doc=payload();log=imported(doc);before=checked(log,rule())
    doc['objects'].append({'id':'new-unobserved-order','type':'order','attributes':[],'relationships':[]})
    appended=workspace.append_log(log,BytesIO(json.dumps(doc).encode()),source_format='ocel')
    assert appended['added_events']==0 and appended['added_object_relationships']==0
    after=checked(log,rule());a=before['supplied_relationship_basis'];b=after['supplied_relationship_basis']
    assert a['relationships_sha256']==b['relationships_sha256']
    assert a['object_index_sha256']!=b['object_index_sha256'] and b['retained_objects']==a['retained_objects']+1
    assert b['revision']==a['revision']+1
    assert after['results'][0]['units']==before['results'][0]['units']+1


def test_work_budget_is_unknown_not_partial_or_zero_and_other_templates_still_run(monkeypatch):
    log=imported();monkeypatch.setattr(constraints,'MAX_RELATION_PAIRS',1)
    result=checked(log,rule(),{'template':'event_cardinality','object_type':'order','activity':'Open','min':1})
    assert result['results'][0]['complete'] is False and 'violating' not in result['results'][0]
    assert result['results'][1]['complete'] is True and result['rules_unknown']==1


def test_corrupt_retained_graph_refuses_the_whole_check():
    log=imported();conn=workspace._connect()
    conn.execute('INSERT INTO object_relationships VALUES (?,?,?,?)',[log,'o1','missing','role']);conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match='missing'):checked(log,rule())


def test_supplied_rule_memory_failure_is_unknown_without_partial_counts_or_private_sql(monkeypatch):
    import duckdb
    log=imported()
    def exhausted(*args):raise duckdb.OutOfMemoryException('PRIVATE_SQL_AND_LABELS')
    monkeypatch.setattr(constraints,'_supplied_cardinality',exhausted)
    result=checked(log,rule(),{'template':'event_cardinality','activity':'Open','object_type':'order','min':1})
    assert result['results'][0]['complete'] is False
    assert result['results'][0]['unknown_reason']=='analysis_memory_limit'
    assert 'units' not in result['results'][0] and 'violating' not in result['results'][0]
    assert result['results'][1]['complete'] is True and 'PRIVATE' not in json.dumps(result)


def test_unpinnable_graph_memory_failure_refuses_the_whole_analysis(monkeypatch):
    import duckdb
    from server import event_log_object_relationships as relationships
    log=imported()
    def exhausted(*args):raise duckdb.OutOfMemoryException('PRIVATE_SQL_AND_LABELS')
    monkeypatch.setattr(relationships,'snapshot',exhausted)
    with pytest.raises(workspace.EventLogWorkspaceError,match='No partial result') as error:checked(log,rule())
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('changes',[
    {'direction':'transitive'},{'direction':1},{'qualifier':1},{'qualifier':'x'*81},{'qualifier':'\u0000'},
    {'object_type':' '},{'related_type':'\ud800'},{'via_activity':'Open'},{'activity':'Open'},
    {'min':True},{'max':-1},{'min':3,'max':1},{'min':0,'max':None},
])
def test_new_rule_has_strict_bounds_and_refuses_unsupported_event_or_temporal_semantics(changes):
    with pytest.raises(workspace.EventLogWorkspaceError):constraints.parse({'rules':[rule(**changes)]})


def test_real_http_new_template_has_session_consent_strict_json_and_graph_pin(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code='import json\npayload=json.loads('+repr(json.dumps(payload()))+')\n'+r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    stored={**headers,'X-OWG-Local-Storage-Confirmed':'true'}
    url='/v1/event-logs/import?format=ocel&name=OwnedDegree'
    assert client.post(url,json=payload,headers=headers).status_code==403
    made=client.post(url,json=payload,headers=stored);assert made.status_code==200,made.text
    path='/v1/event-logs/'+made.json()['log_id']+'/object-constraints'
    rule={'template':'supplied_object_cardinality','object_type':'order','related_type':'item','min':1,'max':1}
    for auth in ({},{'Authorization':'Bearer '+local_auth.ensure_api_token()}):
        assert client.post(path,json={'rules':[rule]},headers=auth).status_code in (401,403)
    response=client.post(path,json={'rules':[rule]},headers=headers)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    data=response.json();assert data['results'][0]['violating']==2
    assert data['supplied_relationship_basis']['retained_relationships']==7
    assert data['execution_authorized'] is False
    raw=json.dumps({'rules':[rule]}).replace('"min": 1','"min": 1, "min": 0')
    assert client.post(path,content=raw,headers={**headers,'Content-Type':'application/json'}).status_code==422
    raw=json.dumps({'rules':[rule]}).replace('"min": 1','"min": NaN')
    assert client.post(path,content=raw,headers={**headers,'Content-Type':'application/json'}).status_code==422
    assert client.post(path,json={'rules':[{**rule,'min':1.0}]},headers=headers).status_code==422
    changed={**rule,'qualifier':"' OR 1=1 --"}
    exact=client.post(path,json={'rules':[changed]},headers=headers)
    assert exact.status_code==200 and exact.json()['results'][0]['violating']==3
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
