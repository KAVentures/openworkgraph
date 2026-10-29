"""Bulk orchestration uses existing provider/roster/CAS operations, never grants."""
from copy import deepcopy
import json
import pytest
from gateway import scim_bulk as bulk, scim_provisioning as scim, employees as roster
from test_process_collaboration import store
from test_scim_provisioning import service, provider, user
from test_scim_routes import http

BASE='https://owned.test/scim/v2/owned'


def request(*operations,**changes):return {'schemas':[bulk.REQUEST],'Operations':list(operations),**changes}
def create(key='one',**changes):return {'method':'POST','path':'/Users','bulkId':key,'data':user(**changes)}
def patch(resource,version=None,**changes):
    return {'method':'PATCH','path':'/Users/'+resource,**({'version':version} if version is not None else {}),
        'data':{'schemas':[scim.PATCH],'Operations':[{'op':'replace','value':changes}]}}
def run(service,provider,body):return bulk.execute(service,*provider,body,BASE)['Operations']


def test_mixed_success_errors_and_retry_have_per_operation_commits(service,provider):
    rows=run(service,provider,request(create(),create('duplicate'),create('two',userName='two@acme.test',externalId='two')))
    assert [r['status'] for r in rows]==['201','409','201']
    assert rows[0]['bulkId']=='one' and rows[0]['location'].startswith(BASE+'/Users/emp_')
    assert rows[1]['response']['schemas']==[scim.ERROR] and 'location' not in rows[1]
    assert service.list_users(*provider)['totalResults']==2
    assert all('data' not in r for r in rows) and 'person@acme.test' not in json.dumps(rows)
    assert run(service,provider,request(create()))[0]['status']=='409'


def test_fail_on_errors_stops_without_rolling_back_prior_success(service,provider):
    rows=run(service,provider,request(create(),create('duplicate'),create('unprocessed',userName='skip@acme.test'),failOnErrors=1))
    assert [r['status'] for r in rows]==['201','409']
    assert service.list_users(*provider)['totalResults']==1


def test_exact_versions_deactivate_rehire_replace_delete_and_stale_retry(service,provider):
    made=service.create_user(*provider,user());uid=made['id'];etag=made['meta']['version']
    changed=run(service,provider,request(patch(uid,etag,active=False)))[0]
    assert changed['status']=='200' and changed['version']!=etag
    assert roster.get_employee(service.db,'acme',uid)['status']=='offboarded'
    rows=run(service,provider,request(patch(uid,etag,active=True),patch(uid,None,active=True)))
    assert [r['status'] for r in rows]==['412','428']
    rehired=run(service,provider,request(patch(uid,changed['version'],active=True)))[0]
    replacement={'method':'PUT','path':'/Users/'+uid,'version':rehired['version'],'data':user(displayName='New name')}
    replaced=run(service,provider,request(replacement))[0];assert replaced['status']=='200'
    removed=run(service,provider,request({'method':'DELETE','path':'/Users/'+uid,'version':replaced['version']}))[0]
    assert removed['status']=='204' and 'version' not in removed
    assert run(service,provider,request(replacement))[0]['status']=='404'
    assert roster.get_employee(service.db,'acme',uid)['status']=='offboarded'


def test_post_reference_is_request_local_and_cannot_guess_version_or_rewrite_values(service,provider):
    rows=run(service,provider,request(create(displayName='bulkId:literal'),patch('bulkId:one',None,active=False)))
    assert [r['status'] for r in rows]==['201','428']
    uid=rows[0]['location'].rsplit('/',1)[1]
    assert rows[1]['location']==rows[0]['location']
    assert service.get_user(*provider,uid)['displayName']=='bulkId:literal'
    rows=run(service,provider,request(patch('bulkId:one','guessed',active=False)))
    assert rows[0]['status']=='400' and service.get_user(*provider,uid)['active']


def test_cross_provider_and_tenant_ids_never_resolve(service,provider):
    other=service.create_provider('other','Other provider','test-admin')
    service.set_enabled(other['provider']['provider_id'],1,True,'test-admin')
    foreign=service.create_user(other['provider']['provider_id'],other['token'],user())
    rows=run(service,provider,request(patch(foreign['id'],foreign['meta']['version'],active=False)))
    assert rows[0]['status']=='404'
    assert service.get_user(other['provider']['provider_id'],other['token'],foreign['id'])['active']


def test_revocation_between_operations_prevents_later_mutation(service,provider,monkeypatch):
    original=service.create_user
    def create_then_revoke(*args):
        made=original(*args);service.revoke(provider[0],2,'test-admin');return made
    monkeypatch.setattr(service,'create_user',create_then_revoke)
    rows=run(service,provider,request(create(),create('two',userName='two@acme.test',externalId='two'),create('three')))
    assert [r['status'] for r in rows]==['201','401']
    assert len(roster.list_employees(service.db,'acme'))==1


def test_failed_operation_audit_rolls_back_without_leaking_private_exception(service,provider,monkeypatch):
    original=service.audit;calls=[]
    def fail_second(*args):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('PRIVATE_EXCEPTION person@acme.test SECRET_TOKEN')
        return original(*args)
    monkeypatch.setattr(service,'audit',fail_second)
    rows=run(service,provider,request(create(),create('two',userName='two@acme.test',externalId='two'),create('three',userName='three@acme.test',externalId='three')))
    assert [r['status'] for r in rows]==['201','500','201']
    assert 'PRIVATE' not in json.dumps(rows) and 'SECRET' not in json.dumps(rows)
    assert service.list_users(*provider)['totalResults']==2
    with service.db.connect() as conn:
        audits=service.db._execute(conn,'SELECT details_json FROM audit_log').fetchall()
    assert 'person@acme.test' not in str(audits) and 'provider-person' not in str(audits)


@pytest.mark.parametrize('bad',[
    {},{'schemas':[bulk.RESPONSE],'Operations':[create()]},request(),request(*[create(str(i)) for i in range(51)]),
    request(create(),failOnErrors=True),request(create(),failOnErrors=0),request(create(),failOnErrors=51),
    request(create(),failOnErrors=None),request(create(),OperationsAgain=[]),
    request(create(),{**create('two'),'method':'GET'}),request(create(),{**create('two'),'method':'post'}),
    request(create(),{**create('two'),'path':'/Roles'}),request(create(),{**create('two'),'path':'https://other.test/Users'}),
    request(create(),{**create('two'),'version':'guessed'}),request(create(),create()),
    request(create(),{**create('two'),'bulkId':'with/slash'}),request(create(),{'method':'PATCH','path':'/Users/id','data':[]}),
    request(create(),{'method':'DELETE','path':'/Users/id','data':{}}),
    *[request(create(),{**patch('id','version',active=False),'path':path}) for path in
      ['/Users/../id','/Users/%2e%2e','/Users/id?x=1','/Users/id#x','/Users/id/','//Users/id','/Users/%69d','/Users/id/extra']],
    request(create(),{**patch('id','version',active=False),'bulkId':'wrong'}),
    request(create(),{**patch('id','version',active=False),'version':False}),
    request(create(),{**patch('id','version',active=False),'VERSION':'duplicate'}),
])
def test_envelope_refused_before_first_write(service,provider,bad):
    with pytest.raises(scim.Error):run(service,provider,bad)
    assert service.list_users(*provider)['totalResults']==0


def test_http_discovery_response_body_guard_and_authenticate_before_parse(http):
    client,db,owner,headers,base,pid=http
    config=client.get(base+'/ServiceProviderConfig',headers=headers).json()
    assert config['bulk']=={'supported':True,'maxOperations':50,'maxPayloadSize':65536}
    response=client.post(base+'/Bulk',headers=headers,json=request(create()))
    assert response.status_code==200 and response.headers['content-type'].startswith('application/scim+json')
    assert response.headers['cache-control']=='no-store'
    result=response.json();assert result['schemas']==[bulk.RESPONSE]
    item=result['Operations'][0];assert item['status']=='201'
    assert client.get(item['location'],headers=headers).headers['etag']==item['version']
    bads=['{"schemas":[],"schemas":[]}', '{"schemas":[],"Operations":[NaN]}', 'x'*65537]
    for data in bads:
        invalid=client.post(base+'/Bulk',headers=headers,content=data)
        assert invalid.status_code in {400,413}
        unauthorized=client.post(base+'/Bulk',headers={**headers,'Authorization':'Bearer invalid'},content=data)
        assert unauthorized.status_code==401
    assert client.post(base+'/Bulk?unexpected=1',headers=headers,json=request(create('other'))).status_code==400
    assert client.get(base+'/Users',headers=headers).json()['totalResults']==1


def test_missing_authority_is_checked_before_malformed_envelope(service,provider):
    for token in [None,'bad',False,{},'x'*201]:
        with pytest.raises(scim.Error) as error:bulk.execute(service,provider[0],token,{},BASE)
        assert error.value.status==401
    service.set_enabled(provider[0],2,False,'test-admin')
    with pytest.raises(scim.Error) as error:run(service,provider,request(create()))
    assert error.value.status==401
    assert roster.list_employees(service.db,'acme')==[]


def test_bulk_stale_writers_use_existing_single_winner_cas(service,provider):
    from concurrent.futures import ThreadPoolExecutor
    made=service.create_user(*provider,user());etag=made['meta']['version']
    def update(name):return run(service,provider,request(patch(made['id'],etag,displayName=name)))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(update,['One','Two']))
    assert sorted(r['status'] for r in rows)==['200','412']
    assert service.get_user(*provider,made['id'])['displayName'] in {'One','Two'}


def test_semantic_failure_is_an_item_error_not_whole_envelope_rollback(service,provider):
    rows=run(service,provider,request(create(),create('invalid',userName='two@acme.test',password='PRIVATE_PASSWORD'),create('three',userName='three@acme.test',externalId='three')))
    assert [r['status'] for r in rows]==['201','400','201']
    assert 'PRIVATE_PASSWORD' not in json.dumps(rows)
    assert service.list_users(*provider)['totalResults']==2


def test_failed_and_forward_references_never_adopt_existing_identity(service,provider):
    made=service.create_user(*provider,user())
    rows=run(service,provider,request(patch('bulkId:later',made['meta']['version'],active=False),create('later')))
    assert [r['status'] for r in rows]==['400','409']
    assert service.get_user(*provider,made['id'])['active']


def test_maximum_bulk_matches_independent_roster_oracle(service,provider):
    operations=[create('bulk_'+str(i),userName='person'+str(i)+'@acme.test',externalId='native_'+str(i),active=i%2==0) for i in range(50)]
    rows=run(service,provider,request(*operations))
    assert len(rows)==50 and all(r['status']=='201' for r in rows)
    retained=service.list_users(*provider,count=200)['Resources']
    assert {(r['userName'],r['active']) for r in retained}=={('person'+str(i)+'@acme.test',i%2==0) for i in range(50)}
    assert len({r['location'] for r in rows})==50


def test_each_operation_rechecks_authority_before_user_attribute_validation(service,provider,monkeypatch):
    original=service.create_user
    def create_then_disable(*args):
        made=original(*args);service.set_enabled(provider[0],2,False,'test-admin');return made
    monkeypatch.setattr(service,'create_user',create_then_disable)
    rows=run(service,provider,request(create(),create('invalid',password='PRIVATE_PASSWORD')))
    assert [r['status'] for r in rows]==['201','401']
    assert len(roster.list_employees(service.db,'acme'))==1
