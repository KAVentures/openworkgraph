"""Optimistic pack saves bind to the full current installation, not just definitions."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, order_to_cash


@pytest.fixture
def log_id(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    return workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()), name='Synthetic reviewed save', source_format='ocel')['log_id']


def install(log_id, **extra):
    return packs.install(log_id, 'order_to_cash', {'version': '1.0.0', 'mapping': MAPPING, **extra})


def state(log_id):
    return packs.installed(log_id), query.list_metrics(log_id)


def test_first_install_and_reconfiguration_require_current_token_when_supplied(log_id):
    first = install(log_id, expected_installation=None)
    token = first['installation_token']
    assert token == packs.installed(log_id)['installs'][0]['installation_token']
    second = install(log_id, expected_installation=token, parameters={'payment_terms_days':45})
    assert second['installation_token'] != token
    before = state(log_id)
    for stale in (None, token, '0'*64):
        with pytest.raises(packs.DomainPackConflict, match='reload setup'):
            install(log_id, expected_installation=stale)
        assert state(log_id) == before


def test_reinstall_same_definitions_still_changes_token(log_id):
    first = install(log_id)
    second = install(log_id, expected_installation=first['installation_token'])
    assert second['definitions_sha256'] == first['definitions_sha256']
    assert second['installation_token'] != first['installation_token']
    with pytest.raises(packs.DomainPackConflict):
        install(log_id, expected_installation=first['installation_token'])


def test_deleted_and_recreated_install_rejects_old_basis(log_id):
    first = install(log_id)
    packs.uninstall(log_id, 'order_to_cash')
    before = state(log_id)
    with pytest.raises(packs.DomainPackConflict):
        install(log_id, expected_installation=first['installation_token'])
    assert state(log_id) == before
    recreated = install(log_id, expected_installation=None)
    assert recreated['installation_token'] != first['installation_token']


def test_legacy_caller_can_still_omit_precondition(log_id):
    first = install(log_id)
    second = install(log_id)
    assert second['change'] == 'reconfigured'
    assert second['installation_token'] != first['installation_token']


@pytest.mark.parametrize('bad', ['', 'A'*64, 'f'*63, True, {'definitions_sha256':'f'*64}])
def test_malformed_precondition_has_no_effect(log_id, bad):
    before = state(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError):
        install(log_id, expected_installation=bad)
    assert state(log_id) == before


def test_concurrent_first_saves_cannot_both_claim_absent_installation(log_id):
    def save(_):
        try:
            install(log_id, expected_installation=None)
            return 'saved'
        except packs.DomainPackConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == ['conflict', 'saved']
    record = packs.installed(log_id)['installs'][0]
    assert {m['metric_id'] for m in query.list_metrics(log_id)['metrics']} == set(record['metric_ids'])


def test_actual_owner_http_reports_stale_save_as_409(tmp_path):
    code = r'''
import json,sys
sys.path.insert(0,'tests')
from order_to_cash_fixture import MAPPING,order_to_cash
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    ai={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=ocel&name=owned',content=json.dumps(order_to_cash()).encode(),
        headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/domain-packs/order_to_cash'
    body={'version':'1.0.0','mapping':MAPPING,'expected_installation':None}
    assert client.post(url,json=body,headers=ai).status_code in (401,403)
    first=client.post(url,json=body,headers=owner)
    assert first.status_code==200,first.text
    stale=client.post(url,json=body,headers=owner)
    assert stale.status_code==409 and 'reload setup' in stale.json()['detail'],stale.text
    listing=client.get(url.rsplit('/',1)[0],headers=owner).json()['installs'][0]
    body['expected_installation']=listing['installation_token']
    second=client.post(url,json=body,headers=owner)
    assert second.status_code==200 and second.headers['cache-control']=='no-store',second.text
    assert client.post(url,json=body,headers=owner).status_code==409
    old=body['expected_installation']
    assert client.delete(url+'?expected_installation='+old,headers=owner).status_code==409
    assert client.delete(url+'?expected_installation=malformed',headers=owner).status_code==422
    fresh=second.json()['installation_token']
    assert client.delete(url+'?expected_installation='+fresh,headers=ai).status_code in (401,403)
    assert client.delete(url+'?expected_installation='+fresh,headers=owner).status_code==200
    assert client.delete(url+'?expected_installation='+fresh,headers=owner).status_code==409
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'), 'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+'\n'+result.stderr


def test_stale_uninstall_cannot_remove_reconfigured_or_recreated_install(log_id):
    first = install(log_id)
    second = install(log_id)
    before = state(log_id)
    with pytest.raises(packs.DomainPackConflict):
        packs.uninstall(log_id, 'order_to_cash', expected_installation=first['installation_token'])
    assert state(log_id) == before
    packs.uninstall(log_id, 'order_to_cash', expected_installation=second['installation_token'])
    with pytest.raises(packs.DomainPackConflict):
        packs.uninstall(log_id, 'order_to_cash', expected_installation=second['installation_token'])
    third = install(log_id)
    with pytest.raises(packs.DomainPackConflict):
        packs.uninstall(log_id, 'order_to_cash', expected_installation=second['installation_token'])
    assert packs.installed(log_id)['installs'][0]['installation_token'] == third['installation_token']
