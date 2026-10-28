"""Owned HTTP fixtures; no actual Jira site, credentials or employees."""
import copy
import csv
from io import StringIO
import json
import threading

import httpx
import pytest

from server import event_log_workspace as workspace
from server import jira_retained_profiles as profiles
from server import jira_retained_source as source

HOST = 'owned-fixture.atlassian.net'
CLOUD = '12345678-1234-1234-1234-123456789012'
REFS = ['owg:r:' + str(i) * 32 for i in (1, 2)]
ENV = {'OWG_JIRA_SOURCE_READ_ENABLED': 'true', 'OWG_JIRA_RETAINED_INGESTION_ENABLED': 'true',
       'OWG_JIRA_HOST': HOST, 'OWG_JIRA_CLOUD_ID': CLOUD, 'OWG_JIRA_OAUTH_ACCESS_TOKEN': 'owned-token-secret'}
CREATED = '2026-01-01T00:00:00Z'
UPDATED = '2026-02-01T00:00:00Z'


def change(identity='1', at='2026-01-02T00:00:00Z', before='1', after='2', label='Ready'):
    return {'id': identity, 'created': at, 'author': {'accountId': 'ACTOR_SECRET', 'displayName': 'NAME_SECRET'},
            'historyMetadata': {'description': 'META_SECRET'},
            'items': [{'fieldId': 'status', 'field': 'status', 'fieldtype': 'jira', 'from': before,
                       'to': after, 'fromString': 'BEFORE_LABEL_SECRET', 'toString': label},
                      {'fieldId': 'assignee', 'from': 'ASSIGNEE_SECRET', 'to': 'OTHER_SECRET'}]}


class Provider:
    def __init__(self):
        self.histories = {'OWG-1': [change()], 'OWG-2': [change('2')]}
        self.account = 'owned-account'
        self.calls = []
        self.page_size = 1
        self.mutate = None
        self.scopes = ['read:jira-work', 'read:jira-user']
        self.fail = None
        self.updated = UPDATED

    def request(self, request):
        assert request.url.host == 'api.atlassian.com'
        assert request.headers['authorization'] == 'Bearer owned-token-secret'
        self.calls.append((request.url.path, dict(request.url.params)))
        if self.mutate:
            result = self.mutate(request)
            if result is not None:
                return result
        if request.url.path.endswith('accessible-resources'):
            return httpx.Response(200, json=[{'id': CLOUD, 'url': 'https://' + HOST, 'scopes': self.scopes}])
        if request.url.path.endswith('/myself'):
            return httpx.Response(200, json={'accountId': self.account, 'active': True, 'emailAddress': 'EMAIL_SECRET'})
        key = request.url.path.split('/issue/')[1].split('/')[0]
        if self.fail:
            return httpx.Response(self.fail, headers={'Retry-After': '120'})
        if request.url.path.endswith('/changelog'):
            offset = int(request.url.params['startAt'])
            entries = self.histories[key][offset:offset + self.page_size]
            total = len(self.histories[key])
            return httpx.Response(200, json={'startAt': offset, 'maxResults': self.page_size,
                'isLast': offset + len(entries) == total, 'total': total, 'values': entries,
                'nextPage': 'https://evil.invalid/never-follow?secret=VALUE'})
        return httpx.Response(200, json={'id': key[-1], 'key': key,
            'fields': {'created': CREATED, 'updated': self.updated, 'summary': 'SUMMARY_SECRET'}})

    def client(self, **kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return httpx.Client(transport=httpx.MockTransport(self.request), **kwargs)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(profiles, 'ensure_api_token', lambda: 'owned-hmac-key')
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(profiles, 'resolve_resource_reference', lambda ref: {
        'provider': 'jira', 'resource_kind': 'issue', 'host': HOST, 'resolver_locator': 'OWG-' + str(REFS.index(ref) + 1)})


def create(refs=None):
    return profiles.create(refs or REFS, confirm_reads=True, confirm_retention=True, confirm_status_labels=True)['profile_id']


def run(profile, provider):
    return profiles.run(profile, confirm_reads=True, client_factory=provider.client)


def export(log):
    out = StringIO(newline='')
    workspace.export_log(log, 'sourcecsv', out, 'source_case')
    return list(csv.DictReader(StringIO(out.getvalue())))


def snapshot():
    with profiles._connect() as conn:
        return {name: conn.execute('SELECT * FROM ' + name + ' ORDER BY ALL').fetchall() for name in
                ('logs', 'events', 'event_objects', 'objects', 'event_source_keys', 'log_appends', 'jira_ingestion_manifest')}


def test_multicase_multipage_replay_late_event_and_private_projection():
    provider = Provider()
    provider.histories['OWG-1'].extend([change('3', before='2', after='3', label='<img src=x onerror=alert(1)>'), change('4')])
    profile = create()
    assert not provider.calls
    first = run(profile, provider)
    assert first['added_events'] == 6 and first['checkpoint']['histories'] == 4
    assert first['checkpoint']['provider_pagination_complete'] is True
    assert first['source_completeness_verified'] is False and first['alert_eligible'] is False
    rows = export(first['log_id'])
    assert len(rows) == 6 and {r['case_id'] for r in rows} == {'1', '2'}
    assert all(r['source_namespace'] == profile and r['resource'] == '' for r in rows)
    encoded = json.dumps(rows)
    for marker in ('ACTOR_SECRET', 'NAME_SECRET', 'META_SECRET', 'EMAIL_SECRET', 'ASSIGNEE_SECRET', 'SUMMARY_SECRET', 'BEFORE_LABEL_SECRET', 'owned-token-secret'):
        assert marker not in encoded
    again = run(profile, provider)
    assert again['added_events'] == 0 and again['duplicate_events_skipped'] == 6
    # Backdated insertion before a prior offset is picked up by a full rescan.
    provider.histories['OWG-1'].insert(0, change('9', at='2026-01-01T00:01:00Z', before='5', after='6'))
    late = run(profile, provider)
    assert late['added_events'] == 1 and late['duplicate_events_skipped'] == 6
    assert len(export(first['log_id'])) == 7
    assert all('evil.invalid' not in path for path, _ in provider.calls)
    paths = [p for p, _ in provider.calls]
    assert all(paths[i - 1].endswith('accessible-resources') for i, p in enumerate(paths) if '/rest/api/' in p)
    with profiles._connect() as conn:
        stored = conn.execute('SELECT definition FROM jira_ingestion_profiles').fetchone()[0]
    assert 'owned-token-secret' not in stored and 'owned-account' not in stored


def test_history_items_do_not_collapse_and_array_reordering_is_stable():
    provider = Provider()
    history = change()
    history['items'].extend([copy.deepcopy(history['items'][0]), change(before='2', after='3', label='Done')['items'][0]])
    provider.histories['OWG-1'] = [history]
    profile = create([REFS[0]])
    made = run(profile, provider)
    assert made['added_events'] == 4
    history['items'].reverse()
    assert run(profile, provider)['duplicate_events_skipped'] == 4


@pytest.mark.parametrize('mutation,reason',[
    (lambda p: p.histories['OWG-1'][0]['items'][0].update(toString='Corrected'), 'retained_source_payload_conflict'),
    (lambda p: p.histories['OWG-1'][0].update(created='2026-01-03T00:00:00Z'), 'retained_source_payload_conflict'),
    (lambda p: p.histories['OWG-1'][0]['items'][0].update(to='99'), 'previously_retained_source_events_missing'),
    (lambda p: p.histories['OWG-1'].clear(), 'previously_retained_source_events_missing'),
    (lambda p: setattr(p, 'account', 'other-account'), 'jira_account_binding_changed'),
])
def test_corrections_deletions_and_account_changes_refuse_whole_batch(mutation, reason):
    provider = Provider(); profile = create(); first = run(profile, provider); before = snapshot()
    mutation(provider)
    provider.histories['OWG-2'].append(change('8'))
    with pytest.raises(source.JiraIngestionError, match=reason):
        run(profile, provider)
    assert snapshot() == before
    state = profiles.status()['profiles'][0]
    assert state['status'] == 'error' and state['last_success_at'] == first['last_success_at']
    assert state['checkpoint'] == first['checkpoint']


@pytest.mark.parametrize('status,reason',[(401, 'source_access_denied'), (403, 'source_access_denied'),
    (404, 'selected_issue_unavailable'), (429, 'source_rate_limited'), (500, 'source_request_failed')])
def test_failed_second_case_never_partially_retains(status, reason):
    provider = Provider(); profile = create()
    def mutate(request):
        if '/issue/OWG-2' in request.url.path:
            return httpx.Response(status, headers={'Retry-After': '120'})
    provider.mutate = mutate
    with pytest.raises(source.JiraIngestionError, match=reason):
        run(profile, provider)
    assert workspace.list_logs()['logs'] == []
    state = profiles.status()['profiles'][0]
    assert state['checkpoint'] is None and state['status'] == 'error'
    if status == 429:
        assert state['retry_after'] > state['last_attempt_at'] + 119
        with pytest.raises(source.JiraIngestionError, match='jira_retry_backoff_active'):
            run(profile, provider)


@pytest.mark.parametrize('mutate,reason',[
    (lambda p: p.update(startAt=1), 'invalid_history_pagination'),
    (lambda p: p.update(total=True), 'invalid_history_pagination'),
    (lambda p: p.update(isLast=False), 'contradictory_history_pagination'),
    (lambda p: p.update(total=1001), 'invalid_history_pagination'),
    (lambda p: p['values'][0].update(created='2026-01-01'), 'invalid_source_timestamp'),
    (lambda p: p['values'][0].update(items=[None]), 'invalid_history_item'),
    (lambda p: p['values'][0]['items'][0].update(toString='bad\nlabel'), 'invalid_status_label'),
])
def test_malformed_page_refuses_without_commit(mutate, reason):
    provider = Provider(); profile = create([REFS[0]])
    def alter(request):
        if request.url.path.endswith('/changelog'):
            page = {'startAt': 0, 'maxResults': 1, 'total': 1, 'isLast': True, 'values': [change()]}
            mutate(page)
            return httpx.Response(200, json=page)
    provider.mutate = alter
    with pytest.raises(source.JiraIngestionError, match=reason):
        run(profile, provider)
    assert workspace.list_logs()['logs'] == []


def test_grant_revocation_mid_read_and_configuration_rotation(monkeypatch):
    provider = Provider(); profile = create()
    def revoke(request):
        if request.url.path.endswith('/changelog'):
            provider.scopes = []
    provider.mutate = revoke
    with pytest.raises(source.JiraIngestionError, match='jira_site_or_read_scopes_not_granted'):
        run(profile, provider)
    assert workspace.list_logs()['logs'] == []
    monkeypatch.setenv('OWG_JIRA_OAUTH_ACCESS_TOKEN', 'new-credential')
    count = len(provider.calls)
    with pytest.raises(source.JiraIngestionError, match='jira_configuration_changed_recreate_profile'):
        run(profile, provider)
    assert len(provider.calls) == count


def test_disable_disconnect_and_observed_scope_change_invalidate_inflight_run(monkeypatch):
    for operation in ('disable', 'disconnect', 'scope'):
        provider = Provider(); profile = create([REFS[0]])
        changed = False
        def mutate(request):
            nonlocal changed
            if request.url.path.endswith('/changelog') and not changed:
                changed = True
                if operation == 'disable': profiles.change(profile, enabled=False)
                elif operation == 'disconnect': profiles.change(profile, disconnect=True)
                else: monkeypatch.setattr(profiles, 'resolve_resource_reference', lambda _: None)
        provider.mutate = mutate
        with pytest.raises(source.JiraIngestionError): run(profile, provider)
        assert workspace.list_logs()['logs'] == []
        if operation == 'scope': break
        if operation == 'disable': assert profiles.status()['profiles'][0]['status'] == 'disabled'


def test_checkpoint_failure_rolls_back_events_and_first_log_then_retry(monkeypatch):
    provider = Provider(); profile = create()
    real_put = profiles._put
    def failing(conn, value):
        real_put(conn, value)
        if value['status'] == 'read_retained': raise RuntimeError('OWNED_INJECTED_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(profiles, '_put', failing)
        with pytest.raises(source.JiraIngestionError, match='jira_ingestion_failed_retry_safe'): run(profile, provider)
    assert workspace.list_logs()['logs'] == []
    state = profiles.status()['profiles'][0]
    assert state['status'] == 'error' and state['checkpoint'] is None
    made = run(profile, provider)
    assert made['added_events'] == 4
    before = snapshot()
    provider.histories['OWG-1'].append(change('7'))
    with monkeypatch.context() as patch:
        patch.setattr(profiles, '_put', failing)
        with pytest.raises(source.JiraIngestionError): run(profile, provider)
    assert snapshot() == before
    assert run(profile, provider)['added_events'] == 1


def test_lost_commit_ack_replays_without_new_log_or_duplicates(monkeypatch):
    provider = Provider(); profile = create()
    actual = workspace.import_log
    def lost(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError('OWNED_LOST_ACK')
    with monkeypatch.context() as patch:
        patch.setattr(workspace, 'import_log', lost)
        with pytest.raises(source.JiraIngestionError): run(profile, provider)
    result = run(profile, provider)
    assert result['added_events'] == 0 and result['duplicate_events_skipped'] == 4
    assert len(workspace.list_logs()['logs']) == 1


def test_deleted_dataset_not_silently_recreated_and_disconnect_retains_data():
    provider = Provider(); profile = create(); made = run(profile, provider)
    profiles.change(profile, disconnect=True)
    assert len(export(made['log_id'])) == 4
    other = create(); result = run(other, provider); workspace.delete_log(result['log_id'])
    before = len(provider.calls)
    with pytest.raises(source.JiraIngestionError, match='retained_jira_dataset_deleted'): run(other, provider)
    assert len(provider.calls) == before
    assert len(workspace.list_logs()['logs']) == 1


@pytest.mark.parametrize('field', ['confirm_reads', 'confirm_retention', 'confirm_status_labels'])
def test_consent_is_separate_and_strict(field):
    values = dict(confirm_reads=True, confirm_retention=True, confirm_status_labels=True)
    for bad in (False, 'true', 1, None):
        values[field] = bad
        with pytest.raises(source.JiraIngestionError): profiles.create(REFS, **values)
    assert profiles.status()['profiles'] == []


def test_default_disabled_and_no_network_when_listing(monkeypatch):
    monkeypatch.delenv('OWG_JIRA_RETAINED_INGESTION_ENABLED')
    assert profiles.status()['configured'] is False
    with pytest.raises(source.JiraIngestionError, match='retained_jira_connector_disabled'): create()


def test_run_lease_blocks_parallel_run_and_expired_worker_cannot_commit():
    provider = Provider(); profile = create([REFS[0]])
    entered, release = threading.Event(), threading.Event()
    failures = []
    def held(request):
        if request.url.path.endswith('/changelog'):
            entered.set()
            assert release.wait(20)
    provider.mutate = held
    def worker():
        try: run(profile, provider)
        except Exception as exc: failures.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(20)
        other = Provider()
        with pytest.raises(source.JiraIngestionError, match='jira_run_already_active'): run(profile, other)
        assert not other.calls
        # Represents a process interrupted past its durable lease. A new manual
        # run replaces it; the old worker must never retain or overwrite status.
        with profiles._connect() as conn:
            state = profiles._get(conn, profile)
            state['lease_until'] = 0
            profiles._put(conn, state)
        assert profiles.status()['profiles'][0]['status'] == 'interrupted'
        newest = run(profile, other)
    finally:
        release.set(); thread.join(20)
    assert not thread.is_alive() and failures == ['jira_run_invalidated']
    current = profiles.status()['profiles'][0]
    assert current['checkpoint'] == newest['checkpoint'] and current['status'] == 'read_retained'
    assert len(export(newest['log_id'])) == 2


@pytest.mark.parametrize('kind,reason',[
    ('json', 'invalid_source_json'), ('oversize', 'source_response_too_large'),
    ('identity', 'issue_identity_mismatch'), ('duplicate', 'duplicate_history_identity'),
    ('changed_total', 'source_history_changed_during_read'), ('changed_issue', 'issue_changed_during_read'),
    ('late_timestamp', 'history_outside_issue_time_bounds'),
])
def test_source_boundaries_and_drift_fences(kind, reason):
    provider = Provider(); profile = create([REFS[0]])
    issue_reads = 0
    def mutate(request):
        nonlocal issue_reads
        if request.url.path.endswith('/issue/OWG-1'):
            issue_reads += 1
            if kind == 'identity':
                return httpx.Response(200, json={'key': 'OWG-OTHER'})
            if kind == 'changed_issue' and issue_reads == 2:
                provider.updated = '2026-02-02T00:00:00Z'
        if request.url.path.endswith('/changelog'):
            if kind == 'json': return httpx.Response(200, content=b'{"total":1,"total":2}')
            if kind == 'oversize': return httpx.Response(200, content=b' ' * (source.MAX_BYTES + 1))
            if kind in {'duplicate', 'changed_total'}:
                offset = int(request.url.params['startAt'])
                return httpx.Response(200, json={'startAt': offset, 'maxResults': 1,
                    'total': 2 if not offset or kind == 'duplicate' else 3,
                    'isLast': offset == 1 and kind == 'duplicate', 'values': [change()]})
            if kind == 'late_timestamp': provider.histories['OWG-1'] = [change(at='2026-03-01T00:00:00Z')]
    provider.mutate = mutate
    with pytest.raises(source.JiraIngestionError, match=reason): run(profile, provider)
    assert workspace.list_logs()['logs'] == []


def test_actual_authenticated_http_lifecycle_and_strict_requests(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
import sys
sys.path.insert(0, 'tests')
from test_jira_retained_ingestion import Provider, ENV, REFS, HOST
import os
os.environ.update(ENV)
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, jira_retained_profiles as profiles
from server.jira_retained_source import Extractor
provider = Provider()
profiles.resolve_resource_reference = lambda ref: {'provider':'jira', 'resource_kind':'issue', 'host': HOST, 'resolver_locator':'OWG-'+str(REFS.index(ref)+1)}
class OwnedExtractor(Extractor):
    def __init__(self, *args, **kwargs): super().__init__(*args, client_factory=provider.client, **kwargs)
profiles.Extractor = OwnedExtractor
path = '/v1/event-log-sources/jira'
with TestClient(app) as client:
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    body=dict(resource_refs=REFS, confirm_reads=True, confirm_retention=True, confirm_status_labels=True)
    for headers in ({}, bearer):
        assert client.get(path,headers=headers).status_code in {401,403}
        assert client.post(path,json=body,headers=headers).status_code in {401,403}
    assert not provider.calls
    assert client.post(path,content='x'*5000,headers=session).status_code==415
    jsonheaders={**session,'Content-Type':'application/json'}
    assert client.post(path,content='x'*5000,headers=jsonheaders).status_code==413
    for raw in ('null','{"a":NaN}','{"confirm_reads":true,"confirm_reads":false}','[]'):
        assert client.post(path,content=raw,headers=jsonheaders).status_code==422
    for field in ('confirm_reads','confirm_retention','confirm_status_labels'):
        assert client.post(path,json={**body,field:1},headers=session).status_code==422
    assert client.post(path,json={**body,'token':'never-accepted'},headers=session).status_code==422
    created=client.post(path,json=body,headers=session)
    assert created.status_code==200 and created.headers['cache-control']=='no-store', created.text
    identifier=created.json()['profile_id']; route=path+'/'+identifier
    assert not provider.calls
    for headers in ({}, bearer):
        assert client.post(route+'/run',json={'confirm_reads':True},headers=headers).status_code in {401,403}
        assert client.post(route+'/enabled',json={'enabled':False,'confirmed':True},headers=headers).status_code in {401,403}
        assert client.delete(route,headers=headers).status_code in {401,403}
    result=client.post(route+'/run',json={'confirm_reads':True},headers=session)
    assert result.status_code==200, result.text
    log=result.json()['log_id']; assert result.json()['added_events']==4
    assert client.get('/v1/event-logs/'+log+'/summary',headers=session).json()['events']==4
    assert client.get('/v1/event-logs/'+log+'/summary',headers=bearer).status_code in {401,403}
    assert client.post(route+'/enabled',json={'enabled':False,'confirmed':False},headers=session).json()['status']=='disabled'
    calls=len(provider.calls)
    assert client.post(route+'/run',json={'confirm_reads':True},headers=session).status_code==422
    assert len(provider.calls)==calls
    assert client.post(route+'/enabled',json={'enabled':True,'confirmed':False},headers=session).status_code==422
    assert client.post(route+'/enabled',json={'enabled':True,'confirmed':True},headers=session).status_code==200
    replay=client.post(route+'/run',json={'confirm_reads':True},headers=session)
    assert replay.json()['duplicate_events_skipped']==4
    assert client.delete(route,headers=session).json()['retained_dataset_deleted'] is False
    assert client.get(path,headers=session).json()['profiles']==[]
    assert client.get('/v1/event-logs/'+log+'/summary',headers=session).json()['events']==4
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path/'http-data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path/'http-auth'), 'WORKFLOW_OBSERVER_CONFIG': str(tmp_path/'http-config.json')}
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


@pytest.mark.parametrize('crash_after_commit', [False, True])
def test_actual_process_exit_and_restart_recovery(tmp_path, crash_after_commit):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
import os,sys,json
from pathlib import Path
sys.path.insert(0,'tests')
from test_jira_retained_ingestion import Provider,ENV,REFS,HOST
os.environ.update(ENV)
from server import jira_retained_profiles as profiles,event_log_workspace as workspace
provider=Provider()
profiles.ensure_api_token=lambda:'owned-restart-key'
profiles.resolve_resource_reference=lambda ref:dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1))
marker=Path(sys.argv[2]); stage=sys.argv[1]
if stage=='create':
    identifier=profiles.create(REFS,confirm_reads=True,confirm_retention=True,confirm_status_labels=True)['profile_id']
    marker.write_text(identifier)
else:
    identifier=marker.read_text()
    if stage=='before':
        original=profiles._put
        def crash(conn,value):
            original(conn,value)
            if value['status']=='read_retained':os._exit(73)
        profiles._put=crash
    elif stage=='after':
        original=workspace.import_log
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            os._exit(73)
        workspace.import_log=crash
    elif stage=='recover-before':
        with profiles._connect() as conn:
            state=profiles._get(conn,identifier)
            assert state['checkpoint'] is None and state['log_id'] is None
            assert conn.execute('SELECT count(*) FROM logs').fetchone()[0]==0
            assert conn.execute('SELECT count(*) FROM jira_ingestion_manifest').fetchone()[0]==0
            # Advance the persisted lease past expiration without a real sleep.
            state['lease_until']=0;profiles._put(conn,state)
    result=profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    if stage=='recover-before':assert result['added_events']==4
    if stage=='recover-after':assert result['added_events']==0 and result['duplicate_events_skipped']==4
    assert len(workspace.list_logs()['logs'])==1
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path/'restart-data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path/'restart-auth'), 'WORKFLOW_OBSERVER_CONFIG': str(tmp_path/'restart-config.json')}
    def stage(name, expected=0):
        result = subprocess.run([sys.executable, '-c', code, name, str(tmp_path/'profile.txt')],
            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == expected, result.stdout + '\n' + result.stderr
    stage('create')
    stage('after' if crash_after_commit else 'before', 73)
    stage('recover-after' if crash_after_commit else 'recover-before')


def test_dataset_erasure_clears_connector_manifest_and_checkpoint_atomically(monkeypatch):
    provider = Provider(); profile = create(); made = run(profile, provider)
    before = snapshot(); put = profiles._put
    def failed(conn, state):
        put(conn, state)
        if state['status'] == 'dataset_deleted': raise RuntimeError('OWNED_ERASURE_ROLLBACK')
    with monkeypatch.context() as patch:
        patch.setattr(profiles, '_put', failed)
        with pytest.raises(RuntimeError, match='OWNED_ERASURE_ROLLBACK'):
            workspace.delete_log(made['log_id'])
    assert snapshot() == before
    workspace.delete_log(made['log_id'])
    state = profiles.status()['profiles'][0]
    assert state['status'] == 'dataset_deleted' and not state['enabled']
    assert state['checkpoint'] is None and state['last_success_at'] is None
    with profiles._connect() as conn:
        assert conn.execute('SELECT count(*) FROM jira_ingestion_manifest').fetchone()[0] == 0
    with pytest.raises(source.JiraIngestionError, match='retained_jira_dataset_deleted'):
        profiles.change(profile, enabled=True, confirmed=True)


def test_configuration_change_during_response_invalidates_next_read(monkeypatch):
    provider = Provider(); profile = create([REFS[0]])
    def change_config(request):
        if request.url.path.endswith('/changelog'):
            monkeypatch.setenv('OWG_JIRA_RETAINED_INGESTION_ENABLED', 'false')
    provider.mutate = change_config
    with pytest.raises(source.JiraIngestionError, match='retained_jira_connector_disabled'):
        run(profile, provider)
    assert workspace.list_logs()['logs'] == []
    assert provider.calls[-1][0].endswith('/changelog')


def test_extraction_deadline_is_not_reset_per_request(monkeypatch):
    provider = Provider(); profile = create([REFS[0]])
    original = source.Extractor
    now = -10
    def clock():
        nonlocal now
        now += 10
        return now
    class BoundedExtractor(original):
        def __init__(self, *args, **kwargs): super().__init__(*args, clock=clock, **kwargs)
    monkeypatch.setattr(profiles, 'Extractor', BoundedExtractor)
    with pytest.raises(source.JiraIngestionError, match='source_read_budget_exceeded'):
        run(profile, provider)
    assert workspace.list_logs()['logs'] == []
    assert 1 <= len(provider.calls) < 10
