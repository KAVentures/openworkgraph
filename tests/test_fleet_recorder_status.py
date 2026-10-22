"""Fleet report freshness, current device identity and existing admin boundaries."""
from datetime import datetime,timedelta,timezone
import json

import pytest
from fastapi.testclient import TestClient

from gateway.employees import init_employee_schema,people_and_devices
from gateway.fleet_health import recorder_status
from gateway_identity_helpers import make_app,bootstrap_owner,add_employee,personal_invite,enroll,ingest
from test_process_collaboration import store

NOW=datetime(2026,10,22,12,tzinfo=timezone.utc)
ENROLLED=(NOW-timedelta(hours=1)).isoformat()


def heartbeat(**changes):
    return {'received_at':NOW.isoformat(),'collector_state':'recording','capture_policy':'recording',
            'collector_age_seconds':2,**changes}


@pytest.mark.parametrize('changes,elapsed,status',[
    ({},0,'recording_reported'),({},44,'recording_signal_stale'),({},61,'unknown'),
    ({'collector_state':'paused','capture_policy':'paused'},15,'paused_reported'),
    ({'collector_state':'stopped','capture_policy':'stopped'},15,'stopped_reported'),
    ({'collector_state':'no_recent_collector_heartbeat'},15,'collector_unavailable'),
    ({'collector_age_seconds':True},0,'recording_signal_stale'),
    ({'capture_policy':'paused'},0,'recording_signal_stale'),
    ({'received_at':'bad PRIVATE_INPUT'},0,'unknown'),
    ({'received_at':(NOW+timedelta(seconds=1)).isoformat()},0,'unknown'),
    ({'received_at':(NOW-timedelta(hours=2)).isoformat()},0,'unknown'),
    ({'collector_state':'PRIVATE_INPUT'},0,'unknown'),
])
def test_only_recent_valid_reports_support_bounded_statuses(changes,elapsed,status):
    result=recorder_status(heartbeat(**changes),enrolled_at=ENROLLED,now=NOW+timedelta(seconds=elapsed))
    assert result['status']==status
    assert not any(result[key] for key in ('evidence_age_used_for_status','connector_health_checked',
        'evidence_sync_health_checked','recording_is_proof_of_work'))
    assert 'PRIVATE_INPUT' not in json.dumps(result)
    if status=='recording_reported':
        assert result['status_expires_at']==(NOW+timedelta(seconds=43)).isoformat()
    if elapsed>60:assert result['report_status']=='stale_report'


def test_missing_report_and_identity_reassignment_are_unknown():
    result=recorder_status(None,enrolled_at=ENROLLED,now=NOW)
    assert result['report_status']=='no_report' and result['recorder_state']=='unknown'
    result=recorder_status(heartbeat(),enrolled_at=ENROLLED,
        identity_linked_at=(NOW+timedelta(seconds=1)).isoformat(),now=NOW+timedelta(seconds=2))
    assert result['report_status']=='predates_enrollment' and result['reported_at'] is None


def test_device_join_matches_organization_actor_and_current_credential(store):
    db=store.db;init_employee_schema(db)
    for token,org,actor in [('current','acme','alice'),('tenant','other','alice'),('actor','acme','bob')]:
        db.put_token(token_id=token,token=token,token_type='device',organization_id=org,actor_id=actor,
            device_id='same-device',scopes={'evidence:write'})
    for token in ('tenant','actor'):
        db.report_recorder_heartbeat(db.authenticate(token),collector_state='recording',capture_policy='recording',collector_age_seconds=0)
    devices=people_and_devices(db,'acme')['unlinked_devices']
    alice=next(row for row in devices if row['actor_id']=='alice')
    assert alice['recorder_status']['report_status']=='no_report'
    db.report_recorder_heartbeat(db.authenticate('current'),collector_state='paused',capture_policy='paused',collector_age_seconds=None)
    alice=next(row for row in people_and_devices(db,'acme')['unlinked_devices'] if row['actor_id']=='alice')
    assert alice['recorder_status']['status']=='paused_reported'
    db.put_token(token_id='new',token='new',token_type='device',organization_id='acme',actor_id='alice',
        device_id='same-device',scopes={'evidence:write'},replace_device=True)
    alice=next(row for row in people_and_devices(db,'acme')['unlinked_devices'] if row['actor_id']=='alice')
    assert alice['recorder_status']['report_status']=='no_report'
    db.report_recorder_heartbeat(db.authenticate('new'),collector_state='stopped',capture_policy='stopped',collector_age_seconds=None)
    assert people_and_devices(db,'acme')['unlinked_devices'][0]['recorder_status']['status']=='stopped_reported'


def test_admin_api_keeps_device_and_ordinary_integration_tokens_out_and_uses_reports_not_events(tmp_path):
    app,db=make_app(tmp_path)
    with TestClient(app) as client:
        admin,_=bootstrap_owner(client)
        employee=add_employee(client,admin,'operator@acme.se')
        invitation=personal_invite(client,admin,employee['employee_id'])
        joined=enroll(client,invitation['token'],'owned-computer').json()
        token=joined['token'];headers={'Authorization':'Bearer '+token}
        ingest(client,token,'event-1')
        def item():
            response=client.get('/v1/admin/people/acme',headers=admin)
            assert response.headers['Cache-Control']=='no-store'
            return response.json()['employees'][0]['devices'][0]
        assert item()['events']==1 and item()['recorder_status']['status']=='unknown'
        response=client.post('/v1/device/recorder-heartbeat',headers=headers,json={
            'collector_state':'paused','local_capture_policy':'paused','collector_heartbeat_age_seconds':None})
        assert response.status_code==200
        assert item()['recorder_status']['status']=='paused_reported'
        assert client.get('/v1/admin/people/acme',headers=headers).status_code in (401,403)
        db.put_token(token_id='ordinary',token='ordinary',token_type='integration',organization_id='acme',
            actor_id='operator@acme.se',scopes={'evidence:read'})
        assert client.get('/v1/admin/people/acme',headers={'Authorization':'Bearer ordinary'}).status_code in (401,403)
        with db.connect() as conn:
            db._execute(conn,"UPDATE recorder_heartbeats SET received_at = ?",((datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(),))
        assert item()['recorder_status']['status']=='unknown'
        assert client.delete('/v1/admin/devices/acme/owned-computer',headers=admin).status_code==200
        assert not client.get('/v1/admin/people/acme',headers=admin).json()['employees'][0]['devices']


def test_hosted_status_requires_active_current_identity_and_does_not_bury_another_report(store):
    from gateway.public_plugin_mcp import _reported_recorder_state
    db=store.db
    for name,org,actor in [('alice','acme','alice'),('other','other','alice'),('bob','acme','bob')]:
        db.put_token(token_id=name,token=name,token_type='device',organization_id=org,
            actor_id=actor,device_id='same',scopes={'evidence:write'})
        db.report_recorder_heartbeat(db.authenticate(name),collector_state='recording',capture_policy='recording',collector_age_seconds=0)
    def state():return _reported_recorder_state(db,organization_id='acme',actor_id='alice')
    assert state()['desktop_collector_heartbeat_verified']
    assert db.revoke_token('alice')
    assert state()['local_recorder_state']=='unknown_to_hosted_connector'
    assert not state()['fresh_authenticated_device_report']
    db.put_token(token_id='replacement',token='replacement',token_type='device',organization_id='acme',
        actor_id='alice',device_id='same',scopes={'evidence:write'})
    assert state()['local_recorder_state']=='unknown_to_hosted_connector'
    # A valid second device is still eligible when the newest prior-device
    # report cannot establish status for its replacement credential.
    db.put_token(token_id='second',token='second',token_type='device',organization_id='acme',
        actor_id='alice',device_id='second',scopes={'evidence:write'})
    db.report_recorder_heartbeat(db.authenticate('second'),collector_state='paused',capture_policy='paused',collector_age_seconds=None)
    with db.connect() as conn:
        db._execute(conn,"UPDATE access_tokens SET created_at = ? WHERE token_id = 'second'",
                    ((datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),))
        db._execute(conn,"UPDATE recorder_heartbeats SET received_at = ? WHERE organization_id = 'acme' AND device_id = 'second'",
                    ((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),))
    assert state()['local_recorder_state']=='paused'
    db.report_recorder_heartbeat(db.authenticate('replacement'),collector_state='recording',capture_policy='recording',collector_age_seconds=0)
    assert state()['desktop_collector_heartbeat_verified']


def test_hosted_status_counts_elapsed_collector_age_and_rejects_future_reports(store):
    from gateway.public_plugin_mcp import _reported_recorder_state
    db=store.db
    db.put_token(token_id='device',token='device',token_type='device',organization_id='acme',
        actor_id='alice',device_id='owned',scopes={'evidence:write'})
    db.report_recorder_heartbeat(db.authenticate('device'),collector_state='recording',capture_policy='recording',collector_age_seconds=40)
    with db.connect() as conn:
        db._execute(conn,"UPDATE access_tokens SET created_at = ?",((datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),))
        db._execute(conn,"UPDATE recorder_heartbeats SET received_at = ?",((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),))
    value=_reported_recorder_state(db,organization_id='acme',actor_id='alice')
    assert value['fresh_authenticated_device_report'] and value['local_recorder_state']=='recording'
    assert not value['desktop_collector_heartbeat_verified']
    with db.connect() as conn:
        db._execute(conn,"UPDATE recorder_heartbeats SET received_at = ?",((datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),))
    value=_reported_recorder_state(db,organization_id='acme',actor_id='alice')
    assert value['local_recorder_state']=='unknown_to_hosted_connector'
    assert not value['fresh_authenticated_device_report'] and not value['desktop_collector_heartbeat_verified']


def test_equal_timestamps_and_inflight_old_requests_cannot_reuse_replacement_identity(store):
    from gateway.public_plugin_mcp import _reported_recorder_state
    db=store.db;init_employee_schema(db)
    db.put_token(token_id='old',token='old',token_type='device',organization_id='acme',
        actor_id='alice',device_id='owned',scopes={'evidence:write'})
    old=db.authenticate('old')
    db.report_recorder_heartbeat(old,collector_state='recording',capture_policy='recording',collector_age_seconds=0)
    db.put_token(token_id='new',token='new',token_type='device',organization_id='acme',
        actor_id='alice',device_id='owned',scopes={'evidence:write'},replace_device=True)
    stamp=datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        db._execute(conn,'UPDATE access_tokens SET created_at = ?',(stamp,))
        db._execute(conn,'UPDATE recorder_heartbeats SET received_at = ?',(stamp,))
    def hosted():return _reported_recorder_state(db,organization_id='acme',actor_id='alice')
    assert not hosted()['fresh_authenticated_device_report']
    assert people_and_devices(db,'acme')['unlinked_devices'][0]['recorder_status']['status']=='unknown'
    db.report_recorder_heartbeat(db.authenticate('new'),collector_state='paused',capture_policy='paused',collector_age_seconds=None)
    assert hosted()['local_recorder_state']=='paused'
    # A request authenticated before revocation cannot overwrite the new report.
    db.report_recorder_heartbeat(old,collector_state='recording',capture_policy='recording',collector_age_seconds=0)
    assert hosted()['local_recorder_state']=='paused'


def test_legacy_heartbeat_schema_upgrade_preserves_rows_but_requires_a_new_bound_report(store):
    from gateway.public_plugin_mcp import _reported_recorder_state
    db=store.db
    db.put_token(token_id='owned',token='owned',token_type='device',organization_id='acme',
        actor_id='alice',device_id='owned',scopes={'evidence:write'})
    with db.connect() as conn:
        db._execute(conn,'DROP TABLE recorder_heartbeats')
        db._execute(conn,'''CREATE TABLE recorder_heartbeats (
            organization_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,
            received_at TEXT NOT NULL,collector_state TEXT NOT NULL,capture_policy TEXT NOT NULL,
            collector_age_seconds INTEGER,PRIMARY KEY(organization_id,actor_id,device_id))''')
        db._execute(conn,'INSERT INTO recorder_heartbeats VALUES (?,?,?,?,?,?,?)',
            ('acme','alice','owned',datetime.now(timezone.utc).isoformat(),'recording','recording',0))
    db.init();db.init()  # Repeated startup is safe and preserves legacy metadata.
    with db.connect() as conn:
        assert db._execute(conn,'SELECT COUNT(*) FROM recorder_heartbeats').fetchone()[0]==1
        assert db._execute(conn,'SELECT device_token_id FROM recorder_heartbeats').fetchone()[0]==''
    assert not _reported_recorder_state(db,organization_id='acme',actor_id='alice')['fresh_authenticated_device_report']
    db.report_recorder_heartbeat(db.authenticate('owned'),collector_state='paused',capture_policy='paused',collector_age_seconds=None)
    assert _reported_recorder_state(db,organization_id='acme',actor_id='alice')['local_recorder_state']=='paused'
