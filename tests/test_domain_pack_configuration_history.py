"""Bounded reviewed setup history, with explicit legacy missing information."""
from io import BytesIO
import copy
import json

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, order_to_cash


@pytest.fixture
def log_id(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    return workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),name='Synthetic configurations',source_format='ocel')['log_id']


def install(log_id, days=30):
    return packs.install(log_id,'order_to_cash',{'version':'1.0.0','mapping':copy.deepcopy(MAPPING),'parameters':{'payment_terms_days':days}})


def test_reconfigure_retains_exact_old_mapping_parameters_and_digest(log_id):
    first=install(log_id)
    second=install(log_id,45)
    history=packs.configuration_history(log_id,'order_to_cash')
    assert len(history['entries'])==2
    before,current=history['entries']
    assert before['current'] is False and current['current'] is True
    for key in ('mapping','parameters','version','installed_at','definitions_sha256'):
        assert before[key]==first[key]
        assert current[key]==second[key]
    assert before['configuration_available'] and current['configuration_available']
    assert history['complete_installation_history'] is False
    assert history['historical_events_retained'] is False
    assert history['execution_authorized'] is False
    history['entries'][0]['mapping']['activities'].clear()
    assert packs.configuration_history(log_id,'order_to_cash')['entries'][0]['mapping']==MAPPING


def test_legacy_history_stays_unavailable_and_future_snapshot_is_retained(log_id):
    first=install(log_id)
    second=install(log_id,45)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            record=packs._record(conn,log_id,'order_to_cash')
            record.pop('configuration_history')
            conn.execute('UPDATE domain_pack_installs SET definition=? WHERE log_id=?',[json.dumps(record),log_id])
        finally:conn.close()
    install(log_id,60)
    old,available,current=packs.configuration_history(log_id,'order_to_cash')['entries']
    assert old['definitions_sha256']==first['definitions_sha256']
    assert old['configuration_available'] is False and 'mapping' not in old
    assert available['parameters']==second['parameters'] and available['configuration_available']
    assert current['parameters']['payment_terms_days']==60


def test_history_is_bounded_and_removed_with_installation(log_id,monkeypatch):
    monkeypatch.setattr(packs,'MAX_HISTORY',2)
    for days in (30,35,40,45):install(log_id,days)
    history=packs.configuration_history(log_id,'order_to_cash')
    assert [e['parameters']['payment_terms_days'] for e in history['entries']]==[35,40,45]
    assert history['max_previous']==2
    packs.uninstall(log_id,'order_to_cash')
    with pytest.raises(workspace.EventLogWorkspaceError):packs.configuration_history(log_id,'order_to_cash')


def test_mismatched_snapshot_is_not_presented_as_known(log_id):
    install(log_id)
    install(log_id,45)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            record=packs._record(conn,log_id,'order_to_cash')
            record['configuration_history'][0]['definitions_sha256']='0'*64
            conn.execute('UPDATE domain_pack_installs SET definition=? WHERE log_id=?',[json.dumps(record),log_id])
        finally:conn.close()
    entry=packs.configuration_history(log_id,'order_to_cash')['entries'][0]
    assert entry['configuration_available'] is False and 'mapping' not in entry


def test_history_endpoint_owner_only_no_store(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
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
    made=client.post('/v1/event-logs/import?format=ocel&name=owned',content=json.dumps(order_to_cash()).encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/domain-packs/order_to_cash'
    assert client.post(url,json={'version':'1.0.0','mapping':MAPPING},headers=owner).status_code==200
    r=client.get(url+'/configurations',headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store',r.text
    assert r.json()['entries'][0]['mapping']==MAPPING
    assert client.get(url+'/configurations',headers=ai).status_code in (401,403)
    assert client.get(url+'/configurations').status_code in (401,403)
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'), 'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'), 'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
