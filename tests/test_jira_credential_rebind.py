"""Owned provider checks; credential continuity is separate from history reads."""
import copy
import json
import threading
import time
import httpx
import pytest

from server import jira_retained_profiles as profiles, jira_credential_rebind as rebind
from server import jira_retained_source as source, event_log_workspace as workspace
from test_jira_retained_ingestion import Provider, ENV, HOST, CLOUD, REFS, change

CONFIRM=dict(confirm_reads=True,confirm_retention=True,confirm_status_labels=True)
OLD=ENV['OWG_JIRA_OAUTH_ACCESS_TOKEN']
NEW='owned-renewed-token'


class RenewableProvider(Provider):
    def __init__(self):
        super().__init__();self.expected=OLD;self.tokens=[]
    def request(self,request):
        assert request.headers['authorization']=='Bearer '+self.expected
        self.tokens.append(request.headers['authorization'])
        # Preserve the independently specified existing issue/grant fixture.
        request.headers['authorization']='Bearer '+OLD
        return super().request(request)


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    monkeypatch.setattr(profiles,'ensure_api_token',lambda:'owned-rebind-key')
    for key,value in ENV.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(profiles,'resolve_resource_reference',lambda ref:dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1)))


def raw(identifier):
    with profiles._connect() as conn:return profiles._get(conn,identifier)


def setup(provider,native=False):
    if native:
        identifier=profiles.create_native(['OWG-1','OWG-2'],**CONFIRM)['profile_id']
    else:identifier=profiles.create(REFS,**CONFIRM)['profile_id']
    first=profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    return identifier,first


def rotate(monkeypatch,provider):
    monkeypatch.setenv('OWG_JIRA_OAUTH_ACCESS_TOKEN',NEW);provider.expected=NEW


def check(identifier,provider,revision=None,confirmed=True):
    return rebind.rebind(identifier,revision=raw(identifier)['revision'] if revision is None else revision,
                         confirm_account_check=confirmed,client_factory=provider.client)


def data_snapshot():
    with profiles._connect() as conn:
        return {table:conn.execute('SELECT * FROM '+table+' ORDER BY ALL').fetchall() for table in
                ('logs','events','objects','event_objects','event_source_keys','event_source_namespaces',
                 'log_appends','log_quality','jira_ingestion_manifest')}


@pytest.mark.parametrize('native',[False,True])
def test_same_account_renewal_preserves_namespace_history_checkpoint_and_replay(monkeypatch,native):
    monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','true')
    provider=RenewableProvider();identifier,first=setup(provider,native);before=data_snapshot();old=raw(identifier)
    rotate(monkeypatch,provider);start=len(provider.calls)
    result=check(identifier,provider)
    assert result['revision']==old['revision']+1 and result['credential_generation']==2
    assert result['status']=='read_retained' and result['credential_check_status']=='verified'
    assert result['log_id']==first['log_id'] and result['checkpoint']==first['checkpoint']
    assert result['last_success_at']==first['last_success_at'] and result['last_attempt_at']==first['last_attempt_at']
    assert data_snapshot()==before
    assert [p for p,_ in provider.calls[start:]]==['/oauth/token/accessible-resources',
        '/ex/jira/'+CLOUD+'/rest/api/3/myself']*2
    assert not result['source_completeness_verified'] and not result['execution_authorized'] and not result['alert_eligible']
    encoded=json.dumps(result)+json.dumps(raw(identifier))
    for forbidden in (OLD,NEW,'owned-account','EMAIL_SECRET','NAME_SECRET'):assert forbidden not in encoded
    after=raw(identifier);assert after['configuration_binding']!=old['configuration_binding']
    assert after['account_binding']==old['account_binding'] and after['issue_keys']==old['issue_keys']
    replay=profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    assert replay['added_events']==0 and replay['duplicate_events_skipped']==4
    assert len(workspace.list_logs()['logs'])==1


def test_unchanged_credential_check_is_not_a_new_generation_or_source_read():
    provider=RenewableProvider();identifier,first=setup(provider)
    a=check(identifier,provider);b=check(identifier,provider)
    assert a['credential_generation']==b['credential_generation']==1
    assert b['checkpoint']==first['checkpoint'] and b['last_success_at']==first['last_success_at']
    assert b['revision']==a['revision']+1


def test_disabled_profile_stays_disabled_after_rebind(monkeypatch):
    provider=RenewableProvider();identifier,first=setup(provider)
    profiles.change(identifier,enabled=False);rotate(monkeypatch,provider)
    result=check(identifier,provider)
    assert result['enabled'] is False and result['status']=='disabled' and result['credential_check_status']=='verified'
    calls=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='jira_profile_disabled'):
        profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    assert len(provider.calls)==calls and result['checkpoint']==first['checkpoint']


def test_existing_history_conflict_is_not_cleared_by_successful_credential_check(monkeypatch):
    provider=RenewableProvider();identifier,first=setup(provider)
    provider.histories['OWG-1'][0]['items'][0]['toString']='Corrected'
    with pytest.raises(source.JiraIngestionError,match='retained_source_payload_conflict'):
        profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    rotate(monkeypatch,provider);result=check(identifier,provider)
    assert result['credential_check_status']=='verified' and result['status']=='error'
    assert result['error']=='retained_source_payload_conflict' and result['checkpoint']==first['checkpoint']


@pytest.mark.parametrize('kind,reason',[('account','jira_credential_account_mismatch'),
    ('grant','jira_site_or_read_scopes_not_granted'),('inactive','jira_account_unavailable')])
def test_unapproved_identity_or_grant_never_changes_binding_or_dataset(monkeypatch,kind,reason):
    provider=RenewableProvider();identifier,first=setup(provider);before=data_snapshot();old=raw(identifier)
    rotate(monkeypatch,provider)
    if kind=='account':provider.account='different-account'
    if kind=='grant':provider.scopes=[]
    if kind=='inactive':
        provider.mutate=lambda r:httpx.Response(200,json={'accountId':'owned-account','active':False}) if r.url.path.endswith('/myself') else None
    with pytest.raises(source.JiraIngestionError,match=reason):check(identifier,provider)
    after=raw(identifier)
    assert after['configuration_binding']==old['configuration_binding'] and after['account_binding']==old['account_binding']
    assert after['status']==old['status'] and after['error']==old['error']
    assert after['credential_check_status']=='failed' and after['credential_check_error']==reason
    assert after['checkpoint']==first['checkpoint'] and data_snapshot()==before


@pytest.mark.parametrize('bad',[False,1,'true',None])
def test_confirmation_is_literal_true_before_mutation_or_read(bad):
    provider=RenewableProvider();identifier,_=setup(provider);old=raw(identifier);calls=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='explicit_jira_account_check_confirmation_required'):
        check(identifier,provider,confirmed=bad)
    assert raw(identifier)==old and len(provider.calls)==calls


@pytest.mark.parametrize('revision',[True,0,-1,'1',1.0,None])
def test_revision_is_strict_and_current_before_provider_read(revision):
    provider=RenewableProvider();identifier,_=setup(provider);old=raw(identifier);calls=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='current_jira_profile_revision_required'):
        rebind.rebind(identifier,revision=revision,confirm_account_check=True,client_factory=provider.client)
    assert raw(identifier)==old and len(provider.calls)==calls


def test_stale_revision_and_tenant_change_refuse_before_read(monkeypatch):
    provider=RenewableProvider();identifier,_=setup(provider);old=raw(identifier);calls=len(provider.calls)
    profiles.change(identifier,enabled=False)
    with pytest.raises(source.JiraIngestionError,match='jira_profile_revision_conflict'):check(identifier,provider,revision=old['revision'])
    monkeypatch.setenv('OWG_JIRA_HOST','another.atlassian.net')
    with pytest.raises(source.JiraIngestionError,match='jira_credential_tenant_change_refused'):check(identifier,provider)
    assert len(provider.calls)==calls


def test_unconfirmed_or_deleted_dataset_cannot_be_adopted():
    provider=RenewableProvider();identifier=profiles.create(REFS,**CONFIRM)['profile_id']
    with pytest.raises(source.JiraIngestionError,match='jira_prior_account_and_dataset_required'):check(identifier,provider)
    assert not provider.calls
    made=profiles.run(identifier,confirm_reads=True,client_factory=provider.client);workspace.delete_log(made['log_id'])
    calls=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='jira_prior_account_and_dataset_required'):check(identifier,provider)
    assert len(provider.calls)==calls


def test_local_hmac_key_rotation_does_not_adopt_old_identity(monkeypatch):
    provider=RenewableProvider();identifier,_=setup(provider);old=raw(identifier)
    monkeypatch.setattr(profiles,'ensure_api_token',lambda:'different-local-key')
    with pytest.raises(source.JiraIngestionError,match='jira_credential_account_mismatch'):check(identifier,provider)
    assert raw(identifier)['configuration_binding']==old['configuration_binding']


@pytest.mark.parametrize('operation',['disable','enable','disconnect','delete','scope','native_flag','token','hmac_key'])
def test_changes_during_check_invalidate_commit_without_stale_overwrite(monkeypatch,operation):
    provider=RenewableProvider();monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','true')
    identifier,made=setup(provider,native=operation=='native_flag');rotate(monkeypatch,provider)
    changed=False
    def mutate(request):
        nonlocal changed
        if request.url.path.endswith('/myself') and not changed:
            changed=True
            if operation=='disable':profiles.change(identifier,enabled=False)
            elif operation=='enable':profiles.change(identifier,enabled=True,confirmed=True)
            elif operation=='disconnect':profiles.change(identifier,disconnect=True)
            elif operation=='delete':workspace.delete_log(made['log_id'])
            elif operation=='scope':monkeypatch.setattr(profiles,'resolve_resource_reference',lambda _:None)
            elif operation=='native_flag':monkeypatch.setenv('OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED','false')
            elif operation=='token':monkeypatch.setenv('OWG_JIRA_OAUTH_ACCESS_TOKEN','changed-again')
            else:monkeypatch.setattr(profiles,'ensure_api_token',lambda:'changed-key-during-read')
    provider.mutate=mutate
    with pytest.raises(source.JiraIngestionError):check(identifier,provider)
    if operation=='disconnect':assert profiles.status()['profiles']==[]
    else:
        result=raw(identifier)
        assert result.get('credential_check_status')!='verified'
        if operation=='disable':assert result['status']=='disabled' and not result['enabled']
        if operation=='delete':assert result['status']=='dataset_deleted' and result['checkpoint'] is None


def test_atomic_binding_failure_restores_previous_configuration_and_history(monkeypatch):
    provider=RenewableProvider();identifier,_=setup(provider);before=data_snapshot();old=raw(identifier);rotate(monkeypatch,provider)
    put=profiles._put
    def fail(conn,value):
        put(conn,value)
        if value.get('credential_check_status')=='verified':raise RuntimeError('OWNED_SQL_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(profiles,'_put',fail)
        with pytest.raises(source.JiraIngestionError,match='jira_credential_check_failed_retry_safe'):check(identifier,provider)
    result=raw(identifier)
    assert result['configuration_binding']==old['configuration_binding'] and result['checkpoint']==old['checkpoint']
    assert result['credential_check_status']=='failed' and data_snapshot()==before
    assert check(identifier,provider)['credential_check_status']=='verified'


def test_rebind_fences_an_inflight_history_read(monkeypatch):
    provider=RenewableProvider();identifier,first=setup(provider);entered=threading.Event();release=threading.Event();errors=[]
    def hold(request):
        if request.url.path.endswith('/changelog'):
            entered.set();assert release.wait(30)
    provider.mutate=hold
    def worker():
        try:profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
        except Exception as error:errors.append(str(error))
    thread=threading.Thread(target=worker);thread.start()
    try:
        assert entered.wait(20)
        separate=RenewableProvider();result=check(identifier,separate)
        assert result['status']=='interrupted' and result['checkpoint']==first['checkpoint']
    finally:release.set();thread.join(20)
    assert not thread.is_alive() and errors==['jira_run_invalidated']
    assert raw(identifier)['credential_check_status']=='verified'


def test_concurrent_checks_and_expired_leases_cannot_commit_stale_result():
    provider=RenewableProvider();identifier,_=setup(provider);entered=threading.Event();release=threading.Event();errors=[]
    def hold(request):
        if request.url.path.endswith('/myself'):
            entered.set();assert release.wait(30)
    provider.mutate=hold
    def worker():
        try:check(identifier,provider)
        except Exception as error:errors.append(str(error))
    thread=threading.Thread(target=worker);thread.start()
    try:
        assert entered.wait(20)
        other=RenewableProvider()
        with pytest.raises(source.JiraIngestionError,match='jira_credential_check_already_active'):check(identifier,other)
        assert not other.calls
        with profiles._connect() as conn:
            old=profiles._get(conn,identifier);old['lease_until']=0;profiles._put(conn,old)
        public=profiles.status()['profiles'][0]
        assert public['status']=='interrupted' and public['credential_check_status']=='interrupted'
        newest=check(identifier,other)
    finally:release.set();thread.join(20)
    assert not thread.is_alive() and errors==['jira_credential_check_invalidated']
    assert raw(identifier)['revision']==newest['revision'] and raw(identifier)['credential_check_status']=='verified'


def test_lost_commit_ack_is_inspectable_and_retry_does_not_advance_generation(monkeypatch):
    provider=RenewableProvider();identifier,first=setup(provider);rotate(monkeypatch,provider)
    actual=rebind._commit
    def lost(*args,**kwargs):
        actual(*args,**kwargs)
        raise RuntimeError('OWNED_LOST_ACK')
    with monkeypatch.context() as patch:
        patch.setattr(rebind,'_commit',lost)
        with pytest.raises(source.JiraIngestionError,match='jira_credential_check_failed_retry_safe'):check(identifier,provider)
    saved=raw(identifier)
    assert saved['credential_check_status']=='verified' and saved['credential_generation']==2
    retry=check(identifier,provider)
    assert retry['credential_generation']==2 and retry['checkpoint']==first['checkpoint']


def test_rate_limit_does_not_clear_history_error_or_allow_immediate_retry(monkeypatch):
    provider=RenewableProvider();identifier,first=setup(provider)
    provider.histories['OWG-1'][0]['items'][0]['toString']='Corrected'
    with pytest.raises(source.JiraIngestionError):profiles.run(identifier,confirm_reads=True,client_factory=provider.client)
    rotate(monkeypatch,provider)
    provider.mutate=lambda _:httpx.Response(429,headers={'Retry-After':'120'})
    with pytest.raises(source.JiraIngestionError,match='source_rate_limited'):check(identifier,provider)
    state=raw(identifier)
    assert state['error']=='retained_source_payload_conflict' and state['status']=='error'
    assert state['credential_check_error']=='source_rate_limited' and state['retry_after']>time.time()+118
    calls=len(provider.calls)
    with pytest.raises(source.JiraIngestionError,match='jira_retry_backoff_active'):check(identifier,provider)
    assert len(provider.calls)==calls and state['checkpoint']==first['checkpoint']


def test_http_current_revision_confirmation_and_separate_account_only_authority(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
import os,sys
sys.path.insert(0,'tests')
from test_jira_credential_rebind import RenewableProvider,ENV,REFS,HOST,OLD,NEW
os.environ.update(ENV)
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,jira_retained_profiles as profiles
from server.jira_retained_source import Extractor
provider=RenewableProvider()
profiles.resolve_resource_reference=lambda ref:dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1))
class OwnedExtractor(Extractor):
    def __init__(self,*args,**kwargs):super().__init__(*args,client_factory=provider.client,**kwargs)
profiles.Extractor=OwnedExtractor
path='/v1/event-log-sources/jira'
with TestClient(app) as client:
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    created=client.post(path,json=dict(resource_refs=REFS,confirm_reads=True,confirm_retention=True,confirm_status_labels=True),headers=session).json()
    route=path+'/'+created['profile_id']
    first=client.post(route+'/run',json={'confirm_reads':True},headers=session).json()
    os.environ['OWG_JIRA_OAUTH_ACCESS_TOKEN']=NEW;provider.expected=NEW
    body={'revision':first['revision'],'confirm_account_check':True};calls=len(provider.calls)
    for headers in ({},bearer):assert client.post(route+'/credential',json=body,headers=headers).status_code in {401,403}
    for bad in ({**body,'token':'NO_INPUT_SECRET'},{**body,'confirm_account_check':1},{**body,'revision':True},{'confirm_account_check':True}):
        assert client.post(route+'/credential',json=bad,headers=session).status_code==422
    assert client.post(route+'/credential',content='{"revision":1,"revision":1,"confirm_account_check":true}',headers={**session,'Content-Type':'application/json'}).status_code==422
    assert len(provider.calls)==calls
    verified=client.post(route+'/credential',json=body,headers=session)
    assert verified.status_code==200 and verified.headers['cache-control']=='no-store',verified.text
    result=verified.json();assert result['credential_generation']==2 and result['credential_check_status']=='verified'
    assert result['checkpoint']==first['checkpoint'] and result['last_success_at']==first['last_success_at']
    assert all('/issue/' not in p for p,_ in provider.calls[calls:])
    assert OLD not in verified.text and NEW not in verified.text and 'owned-account' not in verified.text
    count=len(provider.calls)
    assert client.post(route+'/credential',json=body,headers=session).status_code==422
    assert len(provider.calls)==count
    replay=client.post(route+'/run',json={'confirm_reads':True},headers=session).json()
    assert replay['added_events']==0 and replay['duplicate_events_skipped']==4
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'http-config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
