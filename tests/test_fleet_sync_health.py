"""Content-free evidence-sync health in the company fleet view (roadmap #264 Stage 8 rollout diagnostics)."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from gateway.employees import init_employee_schema,people_and_devices
from gateway.fleet_health import sync_health
from gateway_identity_helpers import make_app,bootstrap_owner,add_employee,personal_invite,enroll,ingest
from test_process_collaboration import store

NOW=datetime(2026,10,23,12,tzinfo=timezone.utc)
ENROLLED=(NOW-timedelta(hours=1)).isoformat()
ROOT=Path(__file__).resolve().parents[1]


def report(**changes):
    return {'received_at':NOW.isoformat(),'sync_state':'connected','unprocessed_local_events':3,
            'quarantined_events':0,'last_success_age_seconds':10,'sync_error_present':0,**changes}


@pytest.mark.parametrize('changes,elapsed,status',[
    ({},0,'in_sync_reported'),
    ({'unprocessed_local_events':0,'last_success_age_seconds':None},0,'in_sync_reported'),
    ({'unprocessed_local_events':500},0,'backlog_reported'),
    ({'last_success_age_seconds':899},0,'in_sync_reported'),
    ({'last_success_age_seconds':899},5,'backlog_reported'),  # receipt age also ages the claim
    ({'last_success_age_seconds':None},0,'backlog_reported'),
    ({'sync_error_present':1},0,'sync_error_reported'),
    ({'sync_state':'error'},0,'sync_error_reported'),
    ({'quarantined_events':2},0,'quarantined_events_reported'),
    ({'sync_state':'stopped','unprocessed_local_events':0},0,'sync_not_running_reported'),
    ({},121,'unknown'),
    ({'sync_state':'PRIVATE_INPUT'},0,'unknown'),
    ({'unprocessed_local_events':-1},0,'unknown'),
    ({'unprocessed_local_events':True},0,'unknown'),
    ({'received_at':(NOW+timedelta(seconds=1)).isoformat()},0,'unknown'),
    ({'received_at':(NOW-timedelta(hours=2)).isoformat()},0,'unknown'),
    ({'received_at':'bad PRIVATE_INPUT'},0,'unknown'),
])
def test_only_fresh_valid_reports_produce_bounded_statuses(changes,elapsed,status):
    result=sync_health(report(**changes),enrolled_at=ENROLLED,now=NOW+timedelta(seconds=elapsed))
    assert result['status']==status
    assert result['evidence_content_received'] is False
    assert result['all_captured_evidence_shared_verified'] is False
    assert 'PRIVATE_INPUT' not in json.dumps(result)
    if status!='unknown':
        assert result['report_status']=='current_report' and result['fresh_authenticated_device_report'] is True
        assert result['status_expires_at']==(NOW+timedelta(seconds=120)).isoformat()


def test_every_problem_is_listed_and_missing_or_reassigned_reports_are_unknown():
    result=sync_health(report(sync_error_present=1,quarantined_events=1,unprocessed_local_events=900),
                       enrolled_at=ENROLLED,now=NOW)
    assert result['problems']==['sync_error_reported','quarantined_events_reported','backlog_reported']
    assert sync_health(None,enrolled_at=ENROLLED,now=NOW)['report_status']=='no_report'
    moved=sync_health(report(),enrolled_at=ENROLLED,identity_linked_at=(NOW+timedelta(seconds=1)).isoformat(),
                      now=NOW+timedelta(seconds=2))
    assert moved['report_status']=='predates_enrollment' and moved['unprocessed_local_events'] is None


def test_roster_uses_only_the_current_active_credentials_report(store):
    db=store.db;init_employee_schema(db)
    for token,org,actor in [('current','acme','alice'),('tenant','other','alice'),('actor','acme','bob')]:
        db.put_token(token_id=token,token=token,token_type='device',organization_id=org,actor_id=actor,
            device_id='same-device',scopes={'evidence:write'})
    write=lambda token,**kw:db.report_sync_health(db.authenticate(token),**{
        'sync_state':'connected','unprocessed_local_events':0,'quarantined_events':0,
        'last_success_age_seconds':5,'sync_error_present':False,**kw})
    for token in ('tenant','actor'):
        write(token,sync_error_present=True)
    alice=lambda:next(r for r in people_and_devices(db,'acme')['unlinked_devices'] if r['actor_id']=='alice')
    assert alice()['sync_health']['report_status']=='no_report'
    write('current',quarantined_events=4)
    assert alice()['sync_health']['status']=='quarantined_events_reported'
    assert alice()['sync_health']['quarantined_events']==4
    # A replacement credential must report again; the old row cannot speak for it.
    stale=db.authenticate('current')
    db.put_token(token_id='new',token='new',token_type='device',organization_id='acme',actor_id='alice',
        device_id='same-device',scopes={'evidence:write'},replace_device=True)
    assert alice()['sync_health']['report_status']=='no_report'
    write('new')
    assert alice()['sync_health']['status']=='in_sync_reported'
    # A replaced credential is rejected, and an in-flight write with it is fenced out.
    assert db.authenticate('current') is None
    before=alice()['sync_health']['reported_at']
    db.report_sync_health(stale,sync_state='error',unprocessed_local_events=0,quarantined_events=0,
                          last_success_age_seconds=None,sync_error_present=True)
    assert alice()['sync_health']['status']=='in_sync_reported' and alice()['sync_health']['reported_at']==before
    # Existing recorder status is unchanged by sync reports.
    assert alice()['recorder_status']['report_status']=='no_report'


def test_device_route_validates_and_admin_view_shows_sync_counts(tmp_path):
    app,db=make_app(tmp_path)
    with TestClient(app) as client:
        admin,_=bootstrap_owner(client)
        employee=add_employee(client,admin,'operator@acme.se')
        invitation=personal_invite(client,admin,employee['employee_id'])
        token=enroll(client,invitation['token'],'owned-computer').json()['token']
        headers={'Authorization':'Bearer '+token}
        ingest(client,token,'event-1')
        item=lambda:client.get('/v1/admin/people/acme',headers=admin).json()['employees'][0]['devices'][0]
        assert item()['sync_health']['status']=='unknown'
        body={'sync_state':'connected','unprocessed_local_events':700,'quarantined_events':0,
              'last_success_age_seconds':3,'sync_error_present':False}
        response=client.post('/v1/device/sync-health',headers=headers,json=body)
        assert response.status_code==200 and response.json()=={'status':'accepted','contents_received':False}
        health=item()['sync_health']
        assert health['status']=='backlog_reported' and health['unprocessed_local_events']==700
        assert item()['events']==1  # gateway-side evidence counts stay separate
        for bad in ({**body,'sync_state':'PRIVATE_INPUT'},{**body,'quarantined_events':-1},
                    {**body,'unprocessed_local_events':10**9}):
            assert client.post('/v1/device/sync-health',headers=headers,json=bad).status_code in (400,422)
        db.put_token(token_id='ordinary',token='ordinary',token_type='integration',organization_id='acme',
            actor_id='operator@acme.se',scopes={'evidence:write'})
        assert client.post('/v1/device/sync-health',headers={'Authorization':'Bearer ordinary'},json=body).status_code==403
        assert client.post('/v1/device/sync-health',json=body).status_code in (401,403)
        with db.connect() as conn:
            db._execute(conn,"UPDATE device_sync_health SET received_at = ?",
                        ((datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(),))
        assert item()['sync_health']['status']=='unknown'


def test_connector_reports_counts_only(tmp_path):
    from connector.state import SyncState
    from connector.sync import _local_sync_health
    db_path=tmp_path/'workflow_observer.db'
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, window_title TEXT)')
        conn.executemany('INSERT INTO events(window_title) VALUES (?)',[('PRIVATE TITLE',)]*12)
    state=SyncState(tmp_path/'gateway_sync_state.db')
    state.set_int('last_local_event_id',4);state.set('status','error')
    state.set('last_error','Gateway said PRIVATE ERROR TEXT')
    state.set('last_success_at',(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat())
    state.quarantine(3,'event-3','PRIVATE REASON')
    health=_local_sync_health(state,db_path)
    assert health['sync_state']=='error' and health['unprocessed_local_events']==8
    assert health['quarantined_events']==1 and health['sync_error_present'] is True
    assert 29<=health['last_success_age_seconds']<=60
    assert set(health)=={'sync_state','unprocessed_local_events','quarantined_events',
                         'last_success_age_seconds','sync_error_present'}
    assert 'PRIVATE' not in json.dumps(health)
    empty=_local_sync_health(SyncState(tmp_path/'other.db'),tmp_path/'missing.db')
    assert empty=={'sync_state':'not_started','unprocessed_local_events':0,'quarantined_events':0,
                   'last_success_age_seconds':None,'sync_error_present':False}
    assert not (tmp_path/'missing.db').exists()


def test_admin_console_renders_sync_badges_with_their_own_expiry():
    html=(ROOT/'gateway'/'admin_console.html').read_text(encoding='utf-8')
    assert 'const syncLabel=d=>' in html and '${syncLabel(d)}' in html
    assert "querySelectorAll('[data-sync-expires]')" in html and 'Sync status needs refresh' in html
    assert "['Sync problems reported'," in html
    assert 'not proof that all captured evidence was shared' in html


def test_connector_posts_counts_and_tolerates_an_older_gateway(tmp_path):
    import httpx
    from connector.state import SyncState
    from connector.sync import _report_sync_health
    seen=[]
    def handler(request):
        seen.append((request.url.path,json.loads(request.content)))
        return httpx.Response(404 if 'old' in str(request.url) else 200,json={'status':'accepted'})
    state=SyncState(tmp_path/'state.db');state.set('status','connected')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _report_sync_health(client,'https://new.gw',state,tmp_path/'missing.db') is True
        assert _report_sync_health(client,'https://old.gw',state,tmp_path/'missing.db') is False
    assert [path for path,_ in seen]==['/v1/device/sync-health']*2
    assert seen[0][1]['sync_state']=='connected' and seen[0][1]['unprocessed_local_events']==0
