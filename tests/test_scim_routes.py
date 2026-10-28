"""Independent HTTP client contract for the advertised SCIM subset."""
from fastapi.testclient import TestClient
import pytest
from gateway_identity_helpers import bootstrap_owner,add_admin,ADMIN_TOKEN,LEGACY_ENROLL
from gateway import scim_provisioning as scim, employees as roster
from gateway.human_access import HumanAccessSettings
from gateway.hardening import PooledGatewayDB,HardeningSettings
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.settings import GatewaySettings
from test_process_collaboration import store

@pytest.fixture
def http(store):
    class RejectOtherCredentials:
        def verify(self, token):
            raise ValueError('Owned test IdP rejects provisioning credentials')
    db=PooledGatewayDB(store.db.database_url,pool_min_size=0,pool_max_size=4)
    app=create_human_enterprise_app(db=db,
        settings=GatewaySettings(database_url=store.db.database_url,admin_token=ADMIN_TOKEN,enrollment_token=LEGACY_ENROLL),
        hardening=HardeningSettings(),human_access=HumanAccessSettings(issuer='https://owned-idp.test',
        audience='api',jwks_url='https://owned-idp.test/keys'),verifier=RejectOtherCredentials())
    with TestClient(app) as client:
        owner,_=bootstrap_owner(client)
        made=client.post('/v1/admin/scim-providers/acme',headers=owner,json={'label':'Owned HTTP IdP'})
        assert made.status_code==201,made.text
        payload=made.json();pid=payload['provider']['provider_id']
        headers={'Authorization':'Bearer '+payload['token'],'Content-Type':'application/scim+json'}
        base='/scim/v2/'+pid
        assert client.get(base+'/ServiceProviderConfig',headers=headers).status_code==401
        enabled=client.patch('/v1/admin/scim-providers/acme/'+pid,headers=owner,json={'revision':1,'enabled':True})
        assert enabled.status_code==200,enabled.text
        yield client,db,owner,headers,base,pid


def user():return {'schemas':[scim.CORE],'userName':'person@acme.test','displayName':'Person','externalId':'native-person','active':True}


def test_discovery_create_query_conditional_patch_and_tombstone(http):
    client,db,owner,h,base,pid=http
    config=client.get(base+'/ServiceProviderConfig',headers=h)
    assert config.status_code==200 and config.headers['content-type'].startswith('application/scim+json')
    assert config.json()['patch']['supported'] and config.json()['bulk']['supported']
    assert client.get(base+'/Schemas',headers=h).json()['totalResults']>=1
    assert client.get(base+'/ResourceTypes',headers=h).json()['Resources'][0]['schema']==scim.CORE
    created=client.post(base+'/Users',headers=h,json=user());assert created.status_code==201,created.text
    assert created.headers['location'].endswith('/Users/'+created.json()['id'])
    assert created.headers['etag']==created.json()['meta']['version'] and created.headers['cache-control']=='no-store'
    url=created.headers['location'];version=created.headers['etag']
    matches=client.get(base+'/Users',params={'filter':'userName eq "person@acme.test"','startIndex':1,'count':1},headers=h)
    assert matches.status_code==200 and matches.json()['totalResults']==1
    patch={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    assert client.patch(url,headers=h,json=patch).status_code==428
    assert client.patch(url,headers={**h,'If-Match':'stale'},json=patch).status_code==412
    changed=client.patch(url,headers={**h,'If-Match':version},json=patch)
    assert changed.status_code==200 and changed.json()['active'] is False,changed.text
    deleted=client.delete(url,headers={**h,'If-Match':changed.headers['etag']})
    assert deleted.status_code==204 and deleted.content==b''
    assert client.get(url,headers=h).status_code==404
    assert client.get(base+'/Users',headers=h).json()['totalResults']==0
    assert roster.get_employee(db,'acme',created.json()['id'])['status']=='offboarded'


def test_credentials_have_no_cross_surface_or_cross_provider_access_and_revoke_immediately(http):
    client,db,owner,h,base,pid=http
    for bad in [{},owner,{'Authorization':'Bearer unknown'}]:
        response=client.get(base+'/Users',headers=bad)
        assert response.status_code==401 and response.json()['schemas']==[scim.ERROR]
    assert client.get('/v1/admin/people/acme',headers=h).status_code==401
    assert client.get('/v1/human/processes',headers=h).status_code==401
    assert client.get('/scim/v2/other/Users',headers=h).status_code==401
    revoked=client.post('/v1/admin/scim-providers/acme/'+pid+'/revoke',headers=owner,json={'revision':2})
    assert revoked.status_code==200,revoked.text
    assert client.get(base+'/Users',headers=h).status_code==401
    assert client.post(base+'/Users',headers=h,json=user()).status_code==401


@pytest.mark.parametrize('data',[
    b'{"schemas":[],"schemas":[]}',b'{"schemas":["bad"]}',b'{"schemas":NaN}',
    b'[]',b'{',b'"not-an-object"',b'{"a":'+b'['*1100+b'0'+b']'*1100+b'}',
],ids=['duplicates','unsupported-schema','nan','array','syntax','scalar','depth'])
def test_invalid_json_returns_standard_scim_error_without_roster_mutation(http,data):
    client,db,owner,h,base,pid=http
    response=client.post(base+'/Users',headers=h,content=data)
    assert response.status_code==400,response.text
    assert response.json()['schemas']==[scim.ERROR] and response.json()['status']=='400'
    assert client.get(base+'/Users',headers=h).json()['totalResults']==0


def test_unsupported_protocol_options_are_explicit_and_body_is_bounded(http):
    client,db,owner,h,base,pid=http
    for params in [{'sortBy':'userName'},{'attributes':'userName'},{'count':'not-a-number'},{'count':201},{'startIndex':0}]:
        r=client.get(base+'/Users',headers=h,params=params)
        assert r.status_code==400 and r.json()['schemas']==[scim.ERROR],r.text
    assert client.get(base+'/Groups',headers=h).json()['totalResults']==0
    assert client.post(base+'/Bulk',headers=h,json={}).status_code==400
    assert client.post(base+'/Users',headers=h,content=b'x'*65537).status_code==413
    assert client.post(base+'/Users',headers={**h,'Content-Type':'text/plain'},content=b'{}').status_code==415


def test_viewer_cannot_create_enable_or_revoke_provisioning_authority(http):
    client,db,owner,h,base,pid=http
    viewer,_,_=add_admin(client,owner,'viewer@acme.test','viewer')
    inventory=client.get('/v1/admin/scim-providers/acme',headers=viewer)
    assert inventory.status_code==200 and h['Authorization'][7:] not in inventory.text
    assert client.post('/v1/admin/scim-providers/acme',headers=viewer,json={'label':'Forbidden'}).status_code==403
    assert client.patch('/v1/admin/scim-providers/acme/'+pid,headers=viewer,json={'revision':2,'enabled':False}).status_code==403
    assert client.post('/v1/admin/scim-providers/acme/'+pid+'/revoke',headers=viewer,json={'revision':2}).status_code==403
    assert client.get(base+'/Users',headers=viewer).status_code==401
    assert client.get(base+'/Users',headers=h).status_code==200


def test_wrong_tenant_configuration_and_duplicate_authorization_cannot_change_current_provider(http):
    client,db,owner,h,base,pid=http
    assert client.patch('/v1/admin/scim-providers/other/'+pid,headers=owner,json={'revision':2,'enabled':False}).status_code==404
    assert client.post('/v1/admin/scim-providers/other/'+pid+'/revoke',headers=owner,json={'revision':2}).status_code==404
    duplicate=[('Authorization',h['Authorization']),('Authorization','Bearer wrong')]
    assert client.get(base+'/Users',headers=duplicate).status_code==401
    assert client.get(base+'/Users',headers=h).status_code==200
