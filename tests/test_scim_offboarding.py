"""Actual owned RSA/OIDC sessions and device credentials through SCIM lifecycle."""
import pytest
from gateway import employees as roster,scim_provisioning as scim
from test_offboarded_human_access import portal,process_signed_portal,analysis_signed_portal
import test_process_signin as process_flow
import test_analysis_signin as analysis_flow


@pytest.mark.parametrize('mode',['single','bulk','assigned','client_policy'])
def test_scim_http_deactivation_revokes_browser_device_and_invitation_and_rehire_does_not_restore_them(portal,mode):
    kind,(client,app,db,idp,settings)=portal
    flow=process_flow if kind=='process' else analysis_flow
    service=app.state.scim_provisioning
    provision=service.create_provider('acme','Owned test IdP','fixture-admin');pid=provision['provider']['provider_id'];token=provision['token']
    service.set_enabled(pid,1,True,'fixture-admin')
    if mode=='client_policy':
        from gateway.scim_client_policy import Policies
        Policies(service).set('acme',pid,{'revision':2,'enabled':True,'confirmUnversioned':True},'fixture-admin')
    if mode=='assigned':
        uid=roster.upsert_employee(db,'acme',email='person@acme.test',display_name='Owned person')[0]['employee_id']
    else:
        made=service.create_user(pid,token,{'schemas':[scim.CORE],'userName':'person@acme.test','displayName':'Owned person','externalId':'NOT_AN_OIDC_SUBJECT'})
        uid=made['id']
    # This explicit owned fixture establishes an existing verified SSO link.
    # Provisioning must never infer it from the supplied externalId.
    with db.connect() as conn:db._execute(conn,'UPDATE gateway_employees SET sso_subject=? WHERE employee_id=?',('actor-a',uid))
    invite=roster.create_personal_invite(db,'acme',uid,max_devices=2)
    device=roster.enroll_with_personal_invite(db,token=invite['token'],requested_organization_id='acme',device_id='owned-device')
    assert db.authenticate(device['token']) is not None
    _,headers=flow.credentials(flow.callback(client,idp,flow.start(client)))
    path='/v1/human/'+('processes' if kind=='process' else 'analyses')
    assert client.get(path,headers=headers).status_code==200
    if mode=='assigned':
        from gateway.scim_assignment import Assignments
        assignment=Assignments(service)
        preview=assignment.preview('acme',pid,uid)
        assignment.assign('acme',pid,uid,{'revision':preview['provider_revision'],
            'employeeVersion':preview['employee_version'],'confirmAssignment':True,
            'externalId':'NOT_AN_OIDC_SUBJECT'},'fixture-admin')
        # Assignment itself preserves the old verified identity and credentials.
        assert client.get(path,headers=headers).status_code==200
        assert db.authenticate(device['token']) is not None
        assert roster.peek_personal_invite(db,invite['token']) is not None
    source='/scim/v2/'+pid+'/Users/'+uid
    current=client.get(source,headers={'Authorization':'Bearer '+token})
    change={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    def update(version):
        auth={'Authorization':'Bearer '+token,'Content-Type':'application/scim+json'}
        if mode!='bulk':
            response=client.patch(source,headers=auth if mode=='client_policy' else {**auth,'If-Match':version},json=change)
            assert response.status_code==200,response.text
            assert response.json()['id']==uid
            return response.headers['etag']
        response=client.post('/scim/v2/'+pid+'/Bulk',headers=auth,json={
            'schemas':['urn:ietf:params:scim:api:messages:2.0:BulkRequest'],
            'Operations':[{'method':'PATCH','path':'/Users/'+uid,'version':version,'data':change}]})
        assert response.status_code==200,response.text
        result=response.json()['Operations'][0]
        assert result['status']=='200',result
        return result['version']
    disabled_version=update(current.headers['etag'])
    assert client.get(path,headers=headers).status_code==401
    assert db.authenticate(device['token']) is None and roster.peek_personal_invite(db,invite['token']) is None
    change['Operations'][0]['value']=True
    active_version=update(disabled_version)
    current_user=service.get_user(pid,token,uid)
    assert current_user['id']==uid and current_user['active'] and current_user['meta']['version']==active_version
    assert client.get(path,headers=headers).status_code==401
    assert db.authenticate(device['token']) is None and roster.peek_personal_invite(db,invite['token']) is None
    _,fresh=flow.credentials(flow.callback(client,idp,flow.start(client)))
    assert client.get(path,headers=fresh).status_code==200
    assert service.get_user(pid,token,uid)['externalId']=='NOT_AN_OIDC_SUBJECT'
