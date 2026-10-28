"""Source-owned spool cleanup, including lifecycle erasure and refusal controls."""
from pathlib import Path
import pytest

from server import jira_ingestion_staging as staging
from server import jira_retained_profiles as profiles, jira_retained_source as source
from server import event_log_workspace as workspace
from test_jira_retained_ingestion import Provider, ENV, REFS, HOST

CONFIRM=dict(confirm_reads=True,confirm_retention=True,confirm_status_labels=True)


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    monkeypatch.setattr(profiles,'ensure_api_token',lambda:'owned-staging-key')
    for key,value in ENV.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(profiles,'resolve_resource_reference',lambda ref:dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1)))


def create():return profiles.create(REFS,**CONFIRM)['profile_id']
def run(identifier,provider):return profiles.run(identifier,confirm_reads=True,client_factory=provider.client)


def simulate_owned_spool(identifier):
    root=staging.prepare(workspace.DATA_DIR,identifier)
    nested=root/'owg-eventlog-owned';nested.mkdir()
    (nested/'upload').write_text('projected business status\n',encoding='utf8')
    manifest=root/'owg-jira-manifest-owned';manifest.mkdir()
    (manifest/'manifest.csv').write_text('opaque source key,digest\n',encoding='utf8')
    return root


def test_normal_retention_places_every_projected_file_in_owned_spool_then_clears(monkeypatch):
    identifier=create();provider=Provider();actual=workspace.import_log;seen=[]
    def inspect(*args,**kwargs):
        root=kwargs['_staging_parent'];assert root.parent.name=='jira_ingestion_staging' and root.name==identifier
        assert root.is_dir();seen.append(root)
        return actual(*args,**kwargs)
    monkeypatch.setattr(workspace,'import_log',inspect)
    result=run(identifier,provider)
    assert result['added_events']==4 and seen and all(not root.exists() for root in seen)
    assert list(workspace.DATA_DIR.glob('owg-eventlog-*'))==[]
    assert list(workspace.DATA_DIR.glob('owg-jira-manifest-*'))==[]


def test_retry_cleans_previous_owned_spool_before_any_provider_read():
    identifier=create();root=simulate_owned_spool(identifier);provider=Provider()
    provider.mutate=lambda _: (None if not root.exists() else (_ for _ in ()).throw(AssertionError('Old source bytes survived into next read')))
    assert run(identifier,provider)['added_events']==4
    assert not root.exists()


def test_disconnecting_never_committed_profile_erases_spool_even_without_configuration(monkeypatch):
    identifier=create();root=simulate_owned_spool(identifier)
    monkeypatch.delenv('OWG_JIRA_SOURCE_READ_ENABLED')
    result=profiles.change(identifier,disconnect=True)
    assert result['retained_log_id'] is None and not root.exists()
    assert profiles.status()['profiles']==[]


def test_dataset_erasure_cleans_owned_spool_but_not_other_profile_or_unowned_data():
    provider=Provider();first=create();made=run(first,provider);first_root=simulate_owned_spool(first)
    second=create();second_root=simulate_owned_spool(second)
    unrelated=workspace.DATA_DIR/'my-import-file.csv';unrelated.write_text('user-owned file')
    workspace.delete_log(made['log_id'])
    assert not first_root.exists() and second_root.exists() and unrelated.read_text()=='user-owned file'
    state=next(p for p in profiles.status()['profiles'] if p['profile_id']==first)
    assert state['status']=='dataset_deleted' and state['checkpoint'] is None
    with profiles._connect() as conn:
        assert conn.execute('SELECT count(*) FROM jira_ingestion_manifest WHERE profile_id=?',[first]).fetchone()[0]==0


def test_unknown_directory_is_preserved_and_dataset_erasure_refuses():
    identifier=create();made=run(identifier,Provider())
    root=workspace.DATA_DIR/'jira_ingestion_staging'/identifier;root.mkdir(parents=True,exist_ok=True)
    unknown=root/'not-owned.txt';unknown.write_text('preserve me')
    with pytest.raises(workspace.EventLogWorkspaceError,match='event log was not deleted'):
        workspace.delete_log(made['log_id'])
    assert unknown.read_text()=='preserve me' and workspace.list_logs()['logs'][0]['events']==4
    assert profiles.status()['profiles'][0]['checkpoint']==made['checkpoint']


def test_empty_directory_before_owner_marker_can_be_recovered():
    identifier=create();root=workspace.DATA_DIR/'jira_ingestion_staging'/identifier;root.mkdir(parents=True)
    staging.clear(workspace.DATA_DIR,identifier)
    assert not root.exists()
    assert run(identifier,Provider())['added_events']==4


def test_cleanup_failure_preserves_registered_profile_and_dataset(monkeypatch):
    identifier=create();made=run(identifier,Provider());root=simulate_owned_spool(identifier)
    def unavailable(_path):raise PermissionError('OWNED_INJECTED_PERMISSION')
    with monkeypatch.context() as patch:
        patch.setattr(staging.shutil,'rmtree',unavailable)
        with pytest.raises(source.JiraIngestionError,match='jira_staging_cleanup_unavailable'):
            profiles.change(identifier,disconnect=True)
    assert root.exists() and profiles.status()['profiles'][0]['log_id']==made['log_id']
    profiles.change(identifier,disconnect=True)
    assert not root.exists() and workspace.list_logs()['logs'][0]['events']==4


@pytest.mark.parametrize('identifier',['../elsewhere','jira_'+'a'*23,'jira_'+'a'*24+'/file',None])
def test_owner_paths_are_not_caller_controlled(identifier):
    with pytest.raises(source.JiraIngestionError,match='invalid_jira_staging_owner'):
        staging.prepare(workspace.DATA_DIR,identifier)


@pytest.mark.parametrize('location',['parent','profile'])
def test_symlinks_and_windows_junctions_are_refused_without_erasing_target(tmp_path,location):
    import os,subprocess
    identifier=create();outside=tmp_path/'outside';outside.mkdir();kept=outside/'keep.txt';kept.write_text('not a source spool')
    parent=workspace.DATA_DIR/'jira_ingestion_staging'
    if location=='parent':link=parent
    else:parent.mkdir();link=parent/identifier
    if os.name=='nt':
        result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stdout+result.stderr
    else:link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(source.JiraIngestionError,match='jira_staging_path_unavailable'):
        staging.prepare(workspace.DATA_DIR,identifier)
    with pytest.raises(source.JiraIngestionError,match='jira_staging_path_unavailable'):
        staging.clear(workspace.DATA_DIR,identifier)
    assert kept.read_text()=='not a source spool' and link.exists()


@pytest.mark.parametrize('prefix',[b'',b'openworkgraph.jira-staging.v1\njira_'])
def test_partial_marker_before_any_source_bytes_can_be_recovered(prefix):
    identifier=create();root=workspace.DATA_DIR/'jira_ingestion_staging'/identifier;root.mkdir(parents=True)
    (root/'.owg-owned-jira-staging').write_bytes(prefix)
    staging.clear(workspace.DATA_DIR,identifier)
    assert not root.exists()
    assert run(identifier,Provider())['added_events']==4


def test_actual_http_erasure_refuses_unknown_owner_then_removes_verified_spool(tmp_path):
    import os,subprocess,sys
    code=r'''
import os,sys
sys.path.insert(0,'tests')
from test_jira_retained_ingestion import Provider,ENV,REFS,HOST
os.environ.update(ENV)
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,event_log_workspace as workspace,jira_retained_profiles as profiles,jira_ingestion_staging as staging
from server.jira_retained_source import Extractor
provider=Provider()
profiles.resolve_resource_reference=lambda ref:dict(provider='jira',resource_kind='issue',host=HOST,resolver_locator='OWG-'+str(REFS.index(ref)+1))
class OwnedExtractor(Extractor):
    def __init__(self,*args,**kwargs):super().__init__(*args,client_factory=provider.client,**kwargs)
profiles.Extractor=OwnedExtractor
path='/v1/event-log-sources/jira'
with TestClient(app) as client:
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    body=dict(resource_refs=REFS,confirm_reads=True,confirm_retention=True,confirm_status_labels=True)
    created=client.post(path,json=body,headers=session);assert created.status_code==200,created.text
    identifier=created.json()['profile_id']
    made=client.post(path+'/'+identifier+'/run',json={'confirm_reads':True},headers=session);assert made.status_code==200,made.text
    log=made.json()['log_id'];root=staging.prepare(workspace.DATA_DIR,identifier)
    upload=root/'upload';upload.write_text('OWNED_PROJECTED_STATUS')
    marker=root/'.owg-owned-jira-staging';valid_marker=marker.read_bytes();marker.write_bytes(b'UNKNOWN_OWNER')
    delete='/v1/event-logs/'+log+'/delete';calls=len(provider.calls)
    assert client.post(delete,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code in {401,403}
    refused=client.post(delete,headers=session)
    assert refused.status_code==422 and 'event log was not deleted' in refused.json()['detail'],refused.text
    assert upload.exists() and 'OWNED_PROJECTED_STATUS' not in refused.text
    assert client.get('/v1/event-logs/'+log+'/summary',headers=session).json()['events']==4
    marker.write_bytes(valid_marker)
    erased=client.post(delete,headers=session);assert erased.status_code==200 and erased.json()['deleted'],erased.text
    assert not root.exists() and len(provider.calls)==calls
    state=client.get(path,headers=session).json()['profiles'][0]
    assert state['status']=='dataset_deleted' and state['checkpoint'] is None
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'http-data'),
        'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'http-auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'http-config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
