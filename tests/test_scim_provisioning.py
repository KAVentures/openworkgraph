"""Bounded SCIM Users over the existing roster, never OIDC subject attribution."""
import pytest
from gateway import scim_provisioning as scim,employees as roster
from gateway.human_access import init_human_access_schema
from test_process_collaboration import store

@pytest.fixture
def service(store):
    db=store.db;roster.init_employee_schema(db);init_human_access_schema(db);scim.init_schema(db)
    return scim.Provisioning(db)

@pytest.fixture
def provider(service):
    made=service.create_provider('acme','Owned synthetic IdP','test-admin')
    service.set_enabled(made['provider']['provider_id'],1,True,'test-admin')
    return made['provider']['provider_id'],made['token']


def user(**changes):return {'schemas':[scim.CORE],'userName':'person@acme.test','displayName':'Person','externalId':'provider-person','active':True,**changes}


def test_provider_is_disabled_until_explicitly_enabled_and_credential_is_not_an_admin_grant(service):
    made=service.create_provider('acme','Owned provider','test-admin');p=made['provider']['provider_id'];token=made['token']
    assert not made['provider']['enabled']
    with pytest.raises(scim.Error) as e:service.list_users(p,token)
    assert e.value.status==401
    assert token not in str(service.list_providers('acme'))
    service.set_enabled(p,1,True,'test-admin')
    assert service.list_users(p,token)['totalResults']==0
    service.revoke(p,2,'test-admin')
    with pytest.raises(scim.Error) as e:service.list_users(p,token)
    assert e.value.status==401


def test_create_uses_stable_roster_actor_and_does_not_link_external_id_to_oidc(service,provider):
    p,token=provider;made=service.create_user(p,token,user())
    assert made['userName']=='person@acme.test' and made['active'] and made['externalId']=='provider-person'
    assert made['id'].startswith('emp_') and made['meta']['version']
    assert roster.get_employee(service.db,'acme',made['id'])['actor_id']=='person@acme.test'
    with service.db.connect() as c:
        assert roster._one(service.db,c,'SELECT sso_subject FROM gateway_employees WHERE employee_id=?',(made['id'],))['sso_subject']==''
    assert service.get_user(p,token,made['id'])==made
    with pytest.raises(scim.Error) as e:service.create_user(p,token,user())
    assert e.value.status==409


def test_create_cannot_adopt_or_reactivate_a_preexisting_employee(service,provider):
    existing,_=roster.upsert_employee(service.db,'acme',email='person@acme.test')
    roster.offboard_employee(service.db,'acme',existing['employee_id'])
    with pytest.raises(scim.Error) as e:service.create_user(*provider,user())
    assert e.value.status==409 and roster.get_employee(service.db,'acme',existing['employee_id'])['status']=='offboarded'


def test_conditional_patch_deactivation_and_rehire_preserve_epoch(service,provider):
    made=service.create_user(*provider,user());uid=made['id']
    body={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    with pytest.raises(scim.Error) as e:service.patch_user(*provider,uid,'stale',body)
    assert e.value.status==412
    changed=service.patch_user(*provider,uid,made['meta']['version'],body)
    assert not changed['active']
    with service.db.connect() as c:
        epoch=roster._one(service.db,c,'SELECT generation FROM gateway_employee_access_epochs WHERE employee_id=?',(uid,))['generation']
    body['Operations'][0]['value']=True
    rehired=service.patch_user(*provider,uid,changed['meta']['version'],body)
    assert rehired['active'] and rehired['id']==uid
    with service.db.connect() as c:
        assert roster._one(service.db,c,'SELECT generation FROM gateway_employee_access_epochs WHERE employee_id=?',(uid,))['generation']==epoch


def test_patch_is_atomic_and_noop_does_not_change_version_or_timestamp(service,provider):
    made=service.create_user(*provider,user());uid=made['id']
    noop={'schemas':[scim.PATCH],'Operations':[{'op':'RePlAcE','path':'DISPLAYNAME','value':'Person'}]}
    assert service.patch_user(*provider,uid,made['meta']['version'],noop)==made
    bad={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False},{'op':'replace','path':'password','value':'NEVER_STORE'}]}
    with pytest.raises(scim.Error):service.patch_user(*provider,uid,made['meta']['version'],bad)
    assert service.get_user(*provider,uid)==made
    assert roster.get_employee(service.db,'acme',uid)['status']=='active'


def test_admin_changes_invalidate_scim_etag_and_username_rename_is_refused(service,provider):
    made=service.create_user(*provider,user());uid=made['id']
    roster.upsert_employee(service.db,'acme',email=made['userName'],display_name='Administrator edit')
    with pytest.raises(scim.Error) as e:service.replace_user(*provider,uid,made['meta']['version'],user())
    assert e.value.status==412
    current=service.get_user(*provider,uid)
    with pytest.raises(scim.Error) as e:service.replace_user(*provider,uid,current['meta']['version'],user(userName='renamed@acme.test'))
    assert e.value.scim_type=='mutability'
    assert service.get_user(*provider,uid)==current


def test_delete_is_a_tombstone_not_evidence_or_identity_erasure(service,provider):
    made=service.create_user(*provider,user());service.delete_user(*provider,made['id'],made['meta']['version'])
    with pytest.raises(scim.Error) as e:service.get_user(*provider,made['id'])
    assert e.value.status==404
    assert service.list_users(*provider)['totalResults']==0
    assert roster.get_employee(service.db,'acme',made['id'])['status']=='offboarded'
    with pytest.raises(scim.Error) as e:service.create_user(*provider,user())
    assert e.value.status==409


def test_scoped_filter_pagination_and_cross_provider_isolation(service,provider):
    first=service.create_user(*provider,user());service.create_user(*provider,user(userName='z@acme.test',externalId='second'))
    page=service.list_users(*provider,start_index=2,count=1)
    assert page['totalResults']==2 and page['itemsPerPage']==1 and page['Resources'][0]['userName']=='z@acme.test'
    assert service.list_users(*provider,filter_text='userName eq "PERSON@acme.test"')['Resources']==[first]
    assert service.list_users(*provider,filter_text='externalId eq "provider-person"')['Resources']==[first]
    with pytest.raises(scim.Error):service.list_users(*provider,filter_text='active eq true or userName pr')
    other=service.create_provider('other','Other IdP','test-admin');pid=other['provider']['provider_id']
    service.set_enabled(pid,1,True,'test-admin')
    with pytest.raises(scim.Error) as e:service.get_user(pid,other['token'],first['id'])
    assert e.value.status==404
    with pytest.raises(scim.Error) as e:service.get_user(pid,provider[1],first['id'])
    assert e.value.status==401


@pytest.mark.parametrize('token',[None,'','not-a-provider-token',False,{},'x'*201])
def test_missing_or_invalid_provider_credentials_never_fall_back_to_admin(service,provider,token):
    with pytest.raises(scim.Error) as error:service.list_users(provider[0],token)
    assert error.value.status==401


def test_audit_failure_rolls_back_roster_link_and_offboard_epoch(service,provider,monkeypatch):
    made=service.create_user(*provider,user())
    def fail(*args):raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(service,'audit',fail)
    with pytest.raises(RuntimeError):service.create_user(*provider,user(userName='new@acme.test',externalId='new'))
    assert len(roster.list_employees(service.db,'acme',include_offboarded=True))==1
    with pytest.raises(RuntimeError):service.patch_user(*provider,made['id'],made['meta']['version'],
        {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]})
    assert service.get_user(*provider,made['id'])==made
    with service.db.connect() as c:
        assert not roster._one(service.db,c,'SELECT * FROM gateway_employee_access_epochs WHERE employee_id=?',(made['id'],))


def test_two_simultaneous_same_version_updates_accept_one(service,provider):
    from concurrent.futures import ThreadPoolExecutor
    made=service.create_user(*provider,user())
    def update(name):
        try:return service.replace_user(*provider,made['id'],made['meta']['version'],user(displayName=name))
        except scim.Error as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(update,['One','Two']))
    assert sum(isinstance(value,dict) for value in results)==1 and results.count(412)==1


def test_optional_attribute_null_remove_and_repeated_removal_are_unassigned_noops(service,provider):
    made=service.create_user(*provider,user())
    clear={'schemas':[scim.PATCH],'Operations':[{'op':'remove','path':'externalId'},{'op':'replace','path':'displayName','value':None}]}
    result=service.patch_user(*provider,made['id'],made['meta']['version'],clear)
    assert result['displayName']=='' and 'externalId' not in result
    remove={'schemas':[scim.PATCH],'Operations':[{'op':'remove','path':'displayName'},{'op':'remove','path':'externalId'}]}
    assert service.patch_user(*provider,made['id'],result['meta']['version'],remove)==result
    with pytest.raises(scim.Error) as error:
        service.patch_user(*provider,made['id'],result['meta']['version'],{'schemas':[scim.PATCH],'Operations':[{'op':'remove'}]})
    assert error.value.scim_type=='noTarget'


def test_duplicate_external_identity_refuses_all_intermediate_deactivation(service,provider):
    first=service.create_user(*provider,user())
    second=service.create_user(*provider,user(userName='other@acme.test',externalId='other'))
    change={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False},{'op':'replace','path':'externalId','value':'other'}]}
    with pytest.raises(scim.Error) as error:service.patch_user(*provider,first['id'],first['meta']['version'],change)
    assert error.value.status==409
    assert service.get_user(*provider,first['id'])==first and service.get_user(*provider,second['id'])==second


@pytest.mark.parametrize('changes',[
    {'active':0},{'active':'false'},{'active':None},{'password':'NEVER_STORE'},
    {'displayName':'x'*121},{'displayName':'has\ncontrol'},{'externalId':'x'*201},
    {'USERNAME':'duplicate@acme.test'},{'schemas':[]},{'emails':[{'value':'other@acme.test'}]},
])
def test_unsupported_or_ambiguous_user_values_are_refused_without_roster_changes(service,provider,changes):
    with pytest.raises(scim.Error):service.create_user(*provider,user(**changes))
    assert service.list_users(*provider)['totalResults']==0
    assert roster.list_employees(service.db,'acme',include_offboarded=True)==[]


def test_provider_scope_and_cas_guard_configuration_and_audit_failure(service,provider,monkeypatch):
    pid,_=provider
    with pytest.raises(scim.Error) as error:service.set_enabled(pid,2,False,'admin',organization='other')
    assert error.value.status==404
    with pytest.raises(scim.Error) as error:service.set_enabled(pid,1,False,'admin')
    assert error.value.status==412
    def fail(*args):raise RuntimeError('Synthetic provider audit failure')
    monkeypatch.setattr(service,'audit',fail)
    with pytest.raises(RuntimeError):service.revoke(pid,2,'admin')
    assert service.list_users(*provider)['totalResults']==0
    with pytest.raises(RuntimeError):service.create_provider('acme','Will roll back','admin')
    assert len(service.list_providers('acme'))==1


def test_replacement_omitting_active_cannot_implicitly_rehire_and_readonly_ids_cannot_retarget(service,provider):
    made=service.create_user(*provider,user(active=False));other=service.create_user(*provider,user(userName='other@acme.test',externalId='other'))
    replacement=user(displayName='New name');replacement.pop('active');replacement['id']=other['id'];replacement['meta']={'version':'not-authority'}
    result=service.replace_user(*provider,made['id'],made['meta']['version'],replacement)
    assert result['active'] is False and result['id']==made['id'] and result['displayName']=='New name'
    assert service.get_user(*provider,other['id'])==other


def test_two_providers_cannot_race_to_adopt_one_roster_identity(service,provider):
    from concurrent.futures import ThreadPoolExecutor
    second=service.create_provider('acme','Other provisioning domain','admin');pid=second['provider']['provider_id'];service.set_enabled(pid,1,True,'admin')
    def create(credentials):
        try:return service.create_user(*credentials,user())
        except scim.Error as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,[provider,(pid,second['token'])]))
    assert sum(isinstance(result,dict) for result in results)==1 and results.count(409)==1
    assert len(roster.list_employees(service.db,'acme',include_offboarded=True))==1
    assert service.list_users(*provider)['totalResults']+service.list_users(pid,second['token'])['totalResults']==1


def test_provider_capacity_is_atomic_across_simultaneous_creates(service):
    from concurrent.futures import ThreadPoolExecutor
    for i in range(19):service.create_provider('acme','Provider '+str(i),'admin')
    def create(label):
        try:return service.create_provider('acme',label,'admin')
        except scim.Error as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,['One','Two']))
    assert sum(isinstance(result,dict) for result in results)==1 and results.count(409)==1
    assert len(service.list_providers('acme'))==20


def test_credential_revocation_serializes_before_a_queued_user_creation(service,provider,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered,release,started=Event(),Event(),Event();audit=service.audit
    def hold(conn,org,actor,action,details):
        audit(conn,org,actor,action,details)
        if action=='scim.provider.revoked':
            entered.set();assert release.wait(5)
    monkeypatch.setattr(service,'audit',hold)
    def create():
        started.set()
        try:return service.create_user(*provider,user())
        except scim.Error as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        revocation=pool.submit(service.revoke,provider[0],2,'admin')
        try:
            assert entered.wait(5);creation=pool.submit(create);assert started.wait(5)
            assert not creation.done()
        finally:release.set()
        assert revocation.result()['revoked_at'] and creation.result()==401
    assert roster.list_employees(service.db,'acme',include_offboarded=True)==[]
