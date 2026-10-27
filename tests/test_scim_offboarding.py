"""Actual owned RSA/OIDC sessions and device credentials through SCIM lifecycle."""
import pytest
from gateway import employees as roster,scim_provisioning as scim
from test_offboarded_human_access import portal,process_signed_portal,analysis_signed_portal
import test_process_signin as process_flow
import test_analysis_signin as analysis_flow


def test_scim_http_deactivation_revokes_browser_device_and_invitation_and_rehire_does_not_restore_them(portal):
    kind,(client,app,db,idp,settings)=portal
    flow=process_flow if kind=='process' else analysis_flow
    service=app.state.scim_provisioning
    provision=service.create_provider('acme','Owned test IdP','fixture-admin');pid=provision['provider']['provider_id'];token=provision['token']
    service.set_enabled(pid,1,True,'fixture-admin')
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
    source='/scim/v2/'+pid+'/Users/'+uid
    current=client.get(source,headers={'Authorization':'Bearer '+token})
    change={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    disabled=client.patch(source,headers={'Authorization':'Bearer '+token,'If-Match':current.headers['etag'],'Content-Type':'application/scim+json'},json=change)
    assert disabled.status_code==200,disabled.text
    assert client.get(path,headers=headers).status_code==401
    assert db.authenticate(device['token']) is None and roster.peek_personal_invite(db,invite['token']) is None
    change['Operations'][0]['value']=True
    active=client.patch(source,headers={'Authorization':'Bearer '+token,'If-Match':disabled.headers['etag'],'Content-Type':'application/scim+json'},json=change)
    assert active.status_code==200 and active.json()['id']==uid
    assert client.get(path,headers=headers).status_code==401
    assert db.authenticate(device['token']) is None and roster.peek_personal_invite(db,invite['token']) is None
    _,fresh=flow.credentials(flow.callback(client,idp,flow.start(client)))
    assert client.get(path,headers=fresh).status_code==200
    assert service.get_user(pid,token,uid)['externalId']=='NOT_AN_OIDC_SUBJECT'
