"""Native selected cases are a separate capability, not observed evidence."""
import pytest
from test_jira_retained_ingestion import Provider, ENV, REFS, HOST, export
from server import jira_retained_profiles as profiles, jira_retained_source as source
from server import event_log_workspace as workspace

CONFIRM = dict(confirm_reads=True, confirm_retention=True, confirm_status_labels=True)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(profiles, 'ensure_api_token', lambda: 'owned-native-case-key')
    for key, value in ENV.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED', raising=False)
    def not_observed(_ref): raise AssertionError('Native case selection must not call an observed resolver')
    monkeypatch.setattr(profiles, 'resolve_resource_reference', not_observed)


def enable(monkeypatch): monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED', 'true')
def create(): return profiles.create_native(['OWG-1', 'OWG-2'], **CONFIRM)['profile_id']
def run(identifier, provider): return profiles.run(identifier, confirm_reads=True, client_factory=provider.client)


def test_native_keys_need_separate_admin_authority_and_no_desktop_evidence(monkeypatch):
    assert profiles.status()['native_case_selection_configured'] is False
    with pytest.raises(source.JiraIngestionError, match='native_jira_case_selection_disabled'): create()
    assert profiles.status()['profiles'] == []
    enable(monkeypatch)
    identifier = create(); provider = Provider()
    assert not provider.calls
    result = run(identifier, provider)
    assert result['selection_basis'] == 'native_issue_keys' and result['resource_refs'] == []
    assert result['selected_issue_keys'] == ['OWG-1', 'OWG-2'] and result['selected_case_count'] == 2
    assert result['added_events'] == 4
    assert not result['execution_authorized'] and not result['source_completeness_verified']
    assert {r['case_id'] for r in export(result['log_id'])} == {'1', '2'}
    replay = run(identifier, provider)
    assert replay['added_events'] == 0 and replay['duplicate_events_skipped'] == 4
    assert len(workspace.list_logs()['logs']) == 1


@pytest.mark.parametrize('bad', [None, [], ['OWG-1']*2, ['owg-1'], ['https://example.org/OWG-1'],
    ['OWG-1?expand=all'], ['OWG-1/..'], ['OWG-1\n'], ['OWG-0'], ['OWG-1']+['OWG-'+str(i) for i in range(2,10)],
    [1], 'OWG-1', {'key':'OWG-1'}])
def test_scope_is_exact_bounded_and_never_guessed(monkeypatch, bad):
    enable(monkeypatch)
    with pytest.raises(source.JiraIngestionError, match='select_one_to_eight_distinct_exact_jira_keys'):
        profiles.create_native(bad, **CONFIRM)
    assert profiles.status()['profiles'] == []


@pytest.mark.parametrize('field', list(CONFIRM))
def test_all_native_profile_confirmations_remain_literal_true(monkeypatch, field):
    enable(monkeypatch)
    for value in (False, 1, 'true', None):
        with pytest.raises(source.JiraIngestionError, match='separate_jira_read_retention_and_label_confirmation_required'):
            profiles.create_native(['OWG-1'], **{**CONFIRM,field:value})
    assert profiles.status()['profiles'] == []


def test_observed_scope_is_not_silently_adopted_or_bypassed(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(profiles, 'resolve_resource_reference', lambda _: None)
    with pytest.raises(source.JiraIngestionError, match='observed_jira_scope_unavailable'):
        profiles.create(REFS, **CONFIRM)
    assert profiles.status()['profiles'] == []


def test_legacy_profile_without_selection_basis_stays_observed(monkeypatch):
    monkeypatch.setattr(profiles, 'resolve_resource_reference', lambda ref: dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1)))
    identifier = profiles.create(REFS, **CONFIRM)['profile_id']
    with profiles._connect() as conn:
        saved=profiles._get(conn,identifier);saved.pop('selection_basis');profiles._put(conn,saved)
    provider=Provider();result=run(identifier,provider)
    assert result['selection_basis']=='observed_refs' and result['resource_refs']==REFS
    monkeypatch.setattr(profiles, 'resolve_resource_reference', lambda _:None)
    enable(monkeypatch)
    count=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='observed_jira_scope_unavailable'):run(identifier,provider)
    assert len(provider.calls)==count


def test_native_flag_revocation_blocks_before_read_and_during_response(monkeypatch):
    enable(monkeypatch);identifier=create();provider=Provider()
    monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','false')
    with pytest.raises(source.JiraIngestionError,match='native_jira_case_selection_disabled'):run(identifier,provider)
    assert not provider.calls
    enable(monkeypatch)
    def revoke(request):
        if request.url.path.endswith('/changelog'):monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','false')
    provider.mutate=revoke
    with pytest.raises(source.JiraIngestionError,match='native_jira_case_selection_disabled'):run(identifier,provider)
    assert workspace.list_logs()['logs']==[] and provider.calls[-1][0].endswith('/changelog')


def test_native_flag_revocation_in_transaction_rolls_back_events_and_checkpoint(monkeypatch):
    enable(monkeypatch);identifier=create();provider=Provider();actual=workspace.import_log
    def revoke(*args,**kwargs):
        original=kwargs['_transaction_hook']
        def guarded(conn,log):
            monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','false');original(conn,log)
        kwargs['_transaction_hook']=guarded
        return actual(*args,**kwargs)
    monkeypatch.setattr(workspace,'import_log',revoke)
    with pytest.raises(source.JiraIngestionError,match='native_jira_case_selection_disabled'):run(identifier,provider)
    assert workspace.list_logs()['logs']==[]
    state=profiles.status()['profiles'][0];assert state['checkpoint'] is None and state['status']=='error'
    with profiles._connect() as conn:assert conn.execute('SELECT count(*) FROM jira_ingestion_manifest').fetchone()[0]==0


def test_native_mode_reuses_correction_refusal_and_erasure(monkeypatch):
    enable(monkeypatch);identifier=create();provider=Provider();made=run(identifier,provider)
    provider.histories['OWG-1'][0]['items'][0]['toString']='Corrected'
    with pytest.raises(source.JiraIngestionError,match='retained_source_payload_conflict'):run(identifier,provider)
    assert len(export(made['log_id']))==4
    workspace.delete_log(made['log_id'])
    assert profiles.status()['profiles'][0]['status']=='dataset_deleted'
    before=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='retained_jira_dataset_deleted'):run(identifier,provider)
    assert len(provider.calls)==before


def test_native_profile_does_not_make_source_receipts_resolvable(monkeypatch):
    from server.jira_source_evidence import jira_issue_receipt
    enable(monkeypatch);identifier=create();provider=Provider();run(identifier,provider)
    calls=len(provider.calls)
    receipt=jira_issue_receipt(REFS[0],environment={**ENV,'OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED':'true'},
        resolver=lambda _:None,reader=lambda *_: (_ for _ in ()).throw(AssertionError('Unobserved source receipt read')))
    assert receipt['availability']=='unknown' and not receipt['source_state_reported']
    assert len(provider.calls)==calls


def test_actual_http_native_selection_and_observed_boundary(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
import os,sys
sys.path.insert(0,'tests')
from test_jira_retained_ingestion import Provider,ENV,REFS
os.environ.update(ENV)
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,jira_retained_profiles as profiles
from server.jira_retained_source import Extractor
provider=Provider()
profiles.resolve_resource_reference=lambda _:None
class OwnedExtractor(Extractor):
    def __init__(self,*args,**kwargs):super().__init__(*args,client_factory=provider.client,**kwargs)
profiles.Extractor=OwnedExtractor
path='/v1/event-log-sources/jira'
confirm=dict(confirm_reads=True,confirm_retention=True,confirm_status_labels=True)
body=dict(issue_keys=['OWG-1','OWG-2'],**confirm)
with TestClient(app) as client:
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post(path,json=body,headers=session).status_code==422
    assert client.get(path,headers=session).json()['native_case_selection_configured'] is False
    os.environ['OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED']='true'
    for headers in ({},bearer):
        assert client.post(path,json=body,headers=headers).status_code in {401,403}
        assert client.get(path,headers=headers).status_code in {401,403}
    assert not provider.calls
    for invalid in ({**body,'resource_refs':REFS},{**body,'token':'NO_SECRET_INPUT'},{**body,'issue_keys':['OWG-1','OWG-1']},{**body,'confirm_retention':1}):
        assert client.post(path,json=invalid,headers=session).status_code==422
    assert client.post(path,json=dict(resource_refs=REFS,**confirm),headers=session).status_code==422
    assert not provider.calls
    created=client.post(path,json=body,headers=session);assert created.status_code==200,created.text
    assert created.json()['resource_refs']==[] and created.json()['selection_basis']=='native_issue_keys'
    identifier=created.json()['profile_id'];route=path+'/'+identifier
    result=client.post(route+'/run',json={'confirm_reads':True},headers=session)
    assert result.status_code==200 and result.json()['added_events']==4,result.text
    log=result.json()['log_id']
    assert client.get('/v1/event-logs/'+log+'/summary',headers=bearer).status_code in {401,403}
    assert client.get('/v1/event-logs/'+log+'/summary',headers=session).json()['events']==4
    replay=client.post(route+'/run',json={'confirm_reads':True},headers=session)
    assert replay.json()['added_events']==0 and replay.json()['duplicate_events_skipped']==4
    os.environ['OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED']='false'
    count=len(provider.calls)
    assert client.post(route+'/run',json={'confirm_reads':True},headers=session).status_code==422
    assert len(provider.calls)==count
    assert client.delete(route,headers=session).json()['retained_dataset_deleted'] is False
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),
        'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'http-config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
