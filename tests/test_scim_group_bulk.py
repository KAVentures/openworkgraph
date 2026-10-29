"""Mixed provisioning uses typed references and existing operation authority."""
from copy import deepcopy
import json
import pytest

from gateway import scim_bulk as bulk, scim_groups as groups, scim_provisioning as scim, employees as roster
from test_scim_bulk import request, create, patch, run, BASE
from test_scim_provisioning import service, provider, user
from test_process_collaboration import store
from test_scim_routes import http


def group_create(key='group', members=(), **changes):
    return {'method':'POST','path':'/Groups','bulkId':key,
            'data':{'schemas':[groups.CORE],'displayName':'Owned group',
                    'members':[{'value':value,'type':'User'} for value in members],**changes}}


def group_patch(identifier, version=None, *operations):
    return {'method':'PATCH','path':'/Groups/'+identifier,
            **({'version':version} if version is not None else {}),
            'data':{'schemas':[scim.PATCH],'Operations':list(operations)}}


def group_get(service, provider, row):
    return groups.Groups(service).get(*provider,row['location'].rsplit('/',1)[1])


def test_mixed_create_links_only_successful_prior_users_and_preserves_literal_fields(service,provider):
    body=request(create(displayName='bulkId:one'),group_create(members=['bulkId:one'],
        displayName='bulkId:one',externalId='bulkId:one'))
    before=deepcopy(body);rows=run(service,provider,body)
    assert body==before and [r['status'] for r in rows]==['201','201']
    uid=rows[0]['location'].rsplit('/',1)[1];saved=group_get(service,provider,rows[1])
    assert saved['members']==[{'value':uid,'type':'User'}]
    assert saved['displayName']==saved['externalId']=='bulkId:one'
    person=service.get_user(*provider,uid)
    assert person['displayName']=='bulkId:one' and person['groups']==[
        {'value':saved['id'],'display':'bulkId:one','type':'direct'}]
    # Versions are snapshots at each operation, not auto-refreshed batch grants.
    assert person['meta']['version']!=rows[0]['version']
    assert rows[1]['version']==saved['meta']['version']
    assert all('data' not in row for row in rows)


@pytest.mark.parametrize('kind',['forward','failed','cross_request','group_as_user','user_as_group','bad_reference'])
def test_reference_resolution_is_request_local_success_only_and_collection_typed(service,provider,kind):
    if kind=='forward':
        operations=[group_create(members=['bulkId:one']),create()];expected=['400','201']
    elif kind=='failed':
        service.create_user(*provider,user())
        operations=[create(),group_create(members=['bulkId:one'])];expected=['409','400']
    elif kind=='cross_request':
        run(service,provider,request(create()))
        operations=[group_create(members=['bulkId:one'])];expected=['400']
    elif kind=='group_as_user':
        operations=[group_create(),group_create('two',['bulkId:group'])];expected=['201','400']
    elif kind=='user_as_group':
        operations=[create(),group_patch('bulkId:one','guessed',{'op':'remove','path':'members'})];expected=['201','400']
    else:
        operations=[create(),group_create(members=['bulkId:one/../x'])];expected=['201','400']
    rows=run(service,provider,request(*operations))
    assert [r['status'] for r in rows]==expected
    retained=groups.Groups(service).list(*provider)['Resources']
    assert all(not g['members'] for g in retained)


@pytest.mark.parametrize('verb',['add','replace'])
def test_member_value_references_in_patch_and_exact_versions(service,provider,verb):
    catalog=groups.Groups(service);g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Before'})
    member={'value':'bulkId:one','type':'User'}
    value=member if verb=='add' else [member]
    body=request(create(),group_patch(g['id'],g['meta']['version'],{'op':verb,'path':'MeMbErS','value':value}))
    snapshot=deepcopy(body);rows=run(service,provider,body)
    assert body==snapshot and [r['status'] for r in rows]==['201','200']
    uid=rows[0]['location'].rsplit('/',1)[1];after=catalog.get(*provider,g['id'])
    assert after['members']==[{'value':uid,'type':'User'}]
    stale=run(service,provider,request(group_patch(g['id'],g['meta']['version'],{'op':'remove','path':'members'})))
    blind=run(service,provider,request(group_patch(g['id'],None,{'op':'remove','path':'members'})))
    assert stale[0]['status']=='412' and blind[0]['status']=='428'
    assert catalog.get(*provider,g['id'])==after


def test_selector_reference_removes_only_target_and_noops_keep_version(service,provider):
    catalog=groups.Groups(service);a=service.create_user(*provider,user())
    g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Keep other','members':[{'value':a['id']}]})
    operations=[{'op':'add','path':'members','value':[{'value':'bulkId:one'}]},
                {'op':'remove','path':'members[value eq "bulkId:one"]'}]
    body=request(create('one',userName='second@acme.test',externalId='second'),group_patch(g['id'],g['meta']['version'],*operations))
    before=deepcopy(body);rows=run(service,provider,body)
    assert body==before and [r['status'] for r in rows]==['201','200']
    assert catalog.get(*provider,g['id'])==g and rows[1]['version']==g['meta']['version']


def test_group_resource_reference_never_supplies_a_version(service,provider):
    rows=run(service,provider,request(group_create(),group_patch('bulkId:group',None,{'op':'remove','path':'members'})))
    assert [r['status'] for r in rows]==['201','428']
    assert rows[0]['location']==rows[1]['location']


def test_group_put_echo_and_delete_reuse_explicit_versions(service,provider):
    catalog=groups.Groups(service);g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Before'})
    body={**g,'id':'grp_'+'0'*32,'meta':{'version':'untrusted'},'displayName':'After'}
    result=run(service,provider,request({'method':'PUT','path':'/Groups/'+g['id'],
        'version':g['meta']['version'],'data':body}))[0]
    assert result['status']=='200';saved=catalog.get(*provider,g['id']);assert saved['displayName']=='After'
    assert saved['id']==g['id'] and saved['meta']['version']!=g['meta']['version']
    deleted=run(service,provider,request({'method':'DELETE','path':'/Groups/'+g['id'],'version':result['version']}))[0]
    assert deleted['status']=='204' and 'version' not in deleted
    with pytest.raises(scim.Error) as error:catalog.get(*provider,g['id'])
    assert error.value.status==404


def test_failed_patch_is_atomic_and_prior_successes_remain(service,provider):
    catalog=groups.Groups(service);g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Before'})
    operations=[{'op':'replace','path':'displayName','value':'Uncommitted'},
                {'op':'add','path':'members','value':[{'value':'bulkId:missing'}]}]
    rows=run(service,provider,request(create(),group_patch(g['id'],g['meta']['version'],*operations)))
    assert [r['status'] for r in rows]==['201','400']
    assert catalog.get(*provider,g['id'])==g and service.list_users(*provider)['totalResults']==1


@pytest.mark.parametrize('mode',['offboard','delete','revoke','disable'])
def test_current_authority_and_member_lifecycle_are_rechecked_between_operations(service,provider,monkeypatch,mode):
    original=service.create_user
    def change_after_create(*args):
        person=original(*args)
        if mode=='offboard':roster.offboard_employee(service.db,'acme',person['id'])
        elif mode=='delete':service.delete_user(*provider,person['id'],person['meta']['version'])
        elif mode=='revoke':service.revoke(provider[0],2,'owned-admin')
        else:service.set_enabled(provider[0],2,False,'owned-admin')
        return person
    monkeypatch.setattr(service,'create_user',change_after_create)
    rows=run(service,provider,request(create(),group_create(members=['bulkId:one']),group_create('later')))
    if mode in {'revoke','disable'}:
        assert [r['status'] for r in rows]==['201','401']
    else:
        assert rows[0]['status']=='201' and rows[1]['status'] in {'400','404'} and rows[2]['status']=='201'
        assert all(not g['members'] for g in groups.Groups(service).list(*provider)['Resources'])


def test_group_audit_failure_rolls_back_without_rewriting_or_leaking_input(service,provider,monkeypatch):
    original=service.audit
    def fail_group(conn,org,actor,action,details):
        if action=='scim.group.created':raise RuntimeError('PRIVATE_AUDIT_TOKEN')
        return original(conn,org,actor,action,details)
    monkeypatch.setattr(service,'audit',fail_group)
    body=request(create(),group_create(members=['bulkId:one']),group_patch('bulkId:group','guessed',{'op':'remove','path':'members'}))
    before=deepcopy(body);rows=run(service,provider,body)
    assert body==before and [r['status'] for r in rows]==['201','500','400']
    assert groups.Groups(service).list(*provider)['totalResults']==0
    assert service.list_users(*provider)['totalResults']==1 and 'PRIVATE' not in json.dumps(rows)


def test_lost_create_ack_never_establishes_a_successful_reference(service,provider,monkeypatch):
    original=service.create_user
    def lost_ack(*args):original(*args);raise RuntimeError('PRIVATE_COMMIT_ACK')
    monkeypatch.setattr(service,'create_user',lost_ack)
    rows=run(service,provider,request(create(),group_create(members=['bulkId:one'])))
    assert [r['status'] for r in rows]==['500','400'] and 'location' not in rows[0]
    assert 'PRIVATE' not in json.dumps(rows) and service.list_users(*provider)['totalResults']==1
    assert groups.Groups(service).list(*provider)['totalResults']==0


def test_failure_threshold_preserves_commits_and_does_not_run_later_groups(service,provider):
    rows=run(service,provider,request(create(),group_create(members=['bulkId:missing']),group_create('skipped'),failOnErrors=1))
    assert [r['status'] for r in rows]==['201','400']
    assert service.list_users(*provider)['totalResults']==1 and groups.Groups(service).list(*provider)['totalResults']==0


def test_cross_provider_user_and_group_cannot_be_adopted(service,provider):
    other=service.create_provider('acme','Other same-organization provider','owned-admin')
    foreign=other['provider']['provider_id'],other['token'];service.set_enabled(foreign[0],1,True,'owned-admin')
    person=service.create_user(*foreign,user());g=groups.Groups(service).create(*foreign,{'schemas':[groups.CORE],'displayName':'Foreign'})
    rows=run(service,provider,request(group_create(members=[person['id']]),group_patch(g['id'],g['meta']['version'],{'op':'remove','path':'members'})))
    assert [r['status'] for r in rows]==['404','404']
    assert groups.Groups(service).get(*foreign,g['id'])==g
    assert service.get_user(*foreign,person['id'])==person


@pytest.mark.parametrize('path',['/Groups/../id','/Groups/%67rp_id','https://other.test/Groups','/Groups/id?x=1','/Groups/id#x','/Groups/id/extra','//Groups/id'])
def test_malformed_group_paths_refuse_the_entire_envelope_before_writes(service,provider,path):
    with pytest.raises(scim.Error):run(service,provider,request(create(),{'method':'DELETE','path':path}))
    assert service.list_users(*provider)['totalResults']==0


def test_bulk_ids_are_unique_across_resource_types_before_dispatch(service,provider):
    with pytest.raises(scim.Error):run(service,provider,request(create(),group_create('one')))
    assert service.list_users(*provider)['totalResults']==0


def test_maximum_mixed_batch_matches_independent_membership_oracle(service,provider):
    operations=[]
    for i in range(25):
        operations.extend([create('u'+str(i),userName='person'+str(i)+'@acme.test',externalId=str(i)),
                           group_create('g'+str(i),['bulkId:u'+str(i)],displayName='Group '+str(i))])
    rows=run(service,provider,request(*operations));assert len(rows)==50 and all(r['status']=='201' for r in rows)
    expected={rows[2*i+1]['location'].rsplit('/',1)[1]:rows[2*i]['location'].rsplit('/',1)[1] for i in range(25)}
    saved=groups.Groups(service).list(*provider)['Resources']
    assert {g['id']:g['members'][0]['value'] for g in saved}==expected
    assert service.list_users(*provider)['totalResults']==25


def test_mixed_http_bulk_uses_existing_guarded_endpoint_and_read_only_membership(http):
    client,db,owner,headers,base,pid=http
    result=client.post(base+'/Bulk',headers=headers,json=request(create(),group_create(members=['bulkId:one'])))
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    rows=result.json()['Operations'];assert [r['status'] for r in rows]==['201','201']
    saved=client.get(rows[1]['location'],headers=headers).json()
    assert saved['members']==[{'value':rows[0]['location'].rsplit('/',1)[1],'type':'User'}]
    assert client.get('/v1/human/processes',headers=headers).status_code==401
    encoded=result.text
    assert 'person@acme.test' not in encoded and 'Owned group' not in encoded
    assert client.post(base+'/Bulk',headers=owner,json=request(group_create('refused'))).status_code==401
    assert client.get(base+'/Groups',headers=headers).json()['totalResults']==1


@pytest.mark.parametrize('members',[
    [{'value':'bulkId:one','VALUE':'bulkId:one'}],
    [{'value':'bulkId:one','$ref':'https://untrusted.test/Users/one'}],
    [{'value':'bulkId:one','type':'Group'}],
    [{'value':'bulkId:one'}]*201,
    [{'value':True}],
])
def test_unsupported_members_remain_semantic_item_failures_without_partial_group(service,provider,members):
    operation=group_create();operation['data']['members']=members
    body=request(create(),operation,group_create('later'))
    before=deepcopy(body);rows=run(service,provider,body)
    assert body==before and [r['status'] for r in rows]==['201','400','201']
    saved=groups.Groups(service).list(*provider)['Resources']
    assert len(saved)==1 and saved[0]['members']==[]


def test_failed_later_group_patch_operation_rolls_back_earlier_valid_change(service,provider):
    catalog=groups.Groups(service);g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Before'})
    operations=[{'op':'replace','path':'displayName','value':'Uncommitted'},
                {'op':'replace','path':'id','value':'grp_'+'0'*32}]
    rows=run(service,provider,request(group_patch(g['id'],g['meta']['version'],*operations)))
    assert rows[0]['status']=='400' and catalog.get(*provider,g['id'])==g


def test_two_bulk_group_writers_have_one_current_version_winner(service,provider):
    from concurrent.futures import ThreadPoolExecutor
    catalog=groups.Groups(service);g=catalog.create(*provider,{'schemas':[groups.CORE],'displayName':'Before'})
    def update(name):
        return run(service,provider,request(group_patch(g['id'],g['meta']['version'],
            {'op':'replace','path':'displayName','value':name})))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(update,['One','Two']))
    assert sorted(r['status'] for r in rows)==['200','412']
    assert catalog.get(*provider,g['id'])['displayName'] in {'One','Two'}
