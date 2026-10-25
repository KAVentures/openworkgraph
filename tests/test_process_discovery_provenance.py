"""Exact local snapshot references never certify business rules or edited derivation."""
from io import BytesIO
import json
import os
import pytest

from server import event_log_workspace as workspace, event_log_model_discovery as discovery
from server import process_discovery_provenance as provenance
from tests.test_event_log_model_discovery import _log


@pytest.fixture(autouse=True)
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def prepare(**changes):
    log = _log([['Receive', 'Review', 'Close']] * 4)
    preview = discovery.discover(log, **changes)
    body = {'expected_model_sha256': preview['process_model_sha256'], **changes}
    result = provenance.record_draft(log, body)
    return log, preview, body, result


def record(result):
    return {'process_model': result['process_model'], 'evidence_refs': [result['source_snapshot']['reference']]}


def test_explicit_snapshot_is_deduplicated_and_stores_no_models_or_cases():
    log, preview, body, result = prepare()
    again = provenance.record_draft(log, body)
    assert again['source_snapshot'] == result['source_snapshot']
    assert result['process_model'] == preview['process_model'] and result['process_model']['goal'] == ''
    conn = workspace._connect()
    try:
        rows = conn.execute('SELECT payload FROM discovery_snapshots WHERE log_id=?', [log]).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert 'process_model' not in payload and 'activities' not in payload
    assert '"c0"' not in rows[0][0] and 'Receive' not in rows[0][0]
    assert payload['quality']['cases_used'] == 4
    assert payload['discovery_method']['algorithm'] == 'bounded_inductive.v1'
    found = provenance.origins(record(result))[0]
    assert found['status'] == 'current' and found['reviewed_graph_matches_discovery'] is True
    assert found['derivation_independently_verified'] is False and found['business_rules_verified'] is False
    assert found['execution_authorized'] is False and 'selection' not in found


def test_discovery_get_does_not_record_and_stale_or_client_supplied_models_are_refused():
    log = _log([['Receive', 'Close']])
    preview = discovery.discover(log)
    conn = workspace._connect()
    try:
        assert conn.execute('SELECT count(*) FROM discovery_snapshots').fetchone()[0] == 0
    finally:
        conn.close()
    for body in ({'expected_model_sha256': '0'*64},
                 {'expected_model_sha256': preview['process_model_sha256'], 'process_model': preview['process_model']},
                 {'expected_model_sha256': preview['process_model_sha256'], 'noise': True}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            provenance.record_draft(log, body)
    conn = workspace._connect()
    try:
        assert conn.execute('SELECT count(*) FROM discovery_snapshots').fetchone()[0] == 0
    finally:
        conn.close()


def test_selection_and_object_attributes_are_pinned_and_private_values_are_minimized():
    log, _, _, first = prepare()
    before = provenance.origins(record(first))[0]
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('UPDATE objects SET attributes=? WHERE log_id=?', [json.dumps({'team': 'PRIVATE_TEAM'}), log])
        finally:
            conn.close()
    changed = provenance.origins(record(first))[0]
    assert changed['status'] == 'source_advanced'
    assert changed['source_content_sha256'] == before['source_content_sha256']
    preview = discovery.discover(log, cohort={'attribute': 'team=PRIVATE_TEAM'})
    selected = provenance.record_draft(log, {'expected_model_sha256': preview['process_model_sha256'],
                                            'cohort': {'attribute': 'team=PRIVATE_TEAM'}})
    assert selected['source_snapshot']['reference'] != first['source_snapshot']['reference']
    minimized = provenance.origins(record(selected))[0]
    assert 'PRIVATE_TEAM' not in json.dumps(minimized)
    detailed = provenance.origins(record(selected), include_selection=True)[0]
    assert detailed['selection']['cohort']['attribute'] == 'team=PRIVATE_TEAM'
    edited = record(selected)
    edited['process_model'] = {**edited['process_model'], 'goal': 'User declared new business goal'}
    assert provenance.origins(edited)[0]['reviewed_graph_matches_discovery'] is False
    assert provenance.origins(edited)[0]['derivation_independently_verified'] is False


def test_deleting_log_clears_snapshots_and_leaves_only_an_unresolved_opaque_reference():
    log, _, _, result = prepare()
    saved = record(result)
    workspace.delete_log(log)
    assert provenance.origins(saved) == [{'reference': saved['evidence_refs'][0], 'status': 'unavailable'}]
    conn = workspace._connect()
    try:
        assert conn.execute('SELECT count(*) FROM discovery_snapshots').fetchone()[0] == 0
    finally:
        conn.close()
    replacement, _, _, new_snapshot = prepare()
    assert replacement != log and new_snapshot['source_snapshot']['reference'] != saved['evidence_refs'][0]
    assert provenance.origins(saved)[0]['status'] == 'unavailable'


def test_append_between_preview_and_recording_refuses_a_changed_model():
    log = _log([['Receive', 'Close']])
    preview = discovery.discover(log)
    workspace.append_log(log, BytesIO(b'case,activity,time\nnew,Receive,2025-01-01T00:00:00Z\nnew,Escalate,2025-01-01T01:00:00Z\nnew,Close,2025-01-01T02:00:00Z\n'),
        source_format='csv', mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
    with pytest.raises(workspace.EventLogWorkspaceError, match='discovery changed'):
        provenance.record_draft(log, {'expected_model_sha256':preview['process_model_sha256']})
    conn = workspace._connect()
    try:
        assert conn.execute('SELECT count(*) FROM discovery_snapshots').fetchone()[0] == 0
    finally:
        conn.close()


def test_cap_preserves_existing_snapshots_and_allows_exact_reuse(monkeypatch):
    monkeypatch.setattr(provenance, 'MAX_SNAPSHOTS', 1)
    log, _, body, first = prepare()
    assert provenance.record_draft(log, body)['source_snapshot']['reference'] == first['source_snapshot']['reference']
    preview = discovery.discover(log, noise=0)
    with pytest.raises(workspace.EventLogWorkspaceError, match='at most 1'):
        provenance.record_draft(log, {'expected_model_sha256': preview['process_model_sha256'], 'noise': 0})
    assert provenance.origins(record(first))[0]['status'] == 'current'


def test_missing_or_untrusted_references_do_not_invent_origin_or_initialize_a_store():
    assert provenance.origins({'evidence_refs': ['execution:example']}) == []
    ref = provenance.PREFIX + '0'*64
    assert provenance.origins({'evidence_refs': [ref]}) == [{'reference': ref, 'status': 'unavailable'}]
    assert not workspace.database_path().exists()


def test_fingerprint_work_budget_never_presents_unchecked_sources_as_current(monkeypatch):
    log, _, body, first = prepare()
    monkeypatch.setattr(provenance, 'MAX_SOURCE_ROWS', 1)
    def forbidden(*args):
        raise AssertionError('over budget: do not hash the dataset')
    monkeypatch.setattr(provenance, 'source_digest', forbidden)
    assert provenance.origins(record(first))[0]['status'] == 'not_checked_work_budget'
    with pytest.raises(workspace.EventLogWorkspaceError, match='no partial snapshot'):
        provenance.record_draft(log, body)


def test_changed_snapshot_metadata_never_retargets_the_reference(monkeypatch):
    _, _, _, first = prepare()
    conn = workspace._connect()
    try:
        ref = first['source_snapshot']['reference']
        payload = json.loads(conn.execute('SELECT payload FROM discovery_snapshots WHERE reference=?', [ref]).fetchone()[0])
        payload['source_content_sha256'] = '0'*64
        conn.execute('UPDATE discovery_snapshots SET payload=? WHERE reference=?', [json.dumps(payload), ref])
    finally:
        conn.close()
    def forbidden(*args):
        raise AssertionError('invalid snapshot: do not inspect a source')
    monkeypatch.setattr(provenance, 'source_digest', forbidden)
    assert provenance.origins(record(first)) == [{'reference':ref, 'status':'reference_mismatch'}]


def test_repeated_log_references_share_a_check_and_extra_logs_are_explicitly_unchecked(monkeypatch):
    log, _, _, first = prepare()
    preview = discovery.discover(log, noise=0)
    second = provenance.record_draft(log, {'expected_model_sha256':preview['process_model_sha256'], 'noise':0})
    _, _, _, other = prepare()
    saved = record(first)
    saved['evidence_refs'] += [second['source_snapshot']['reference'], other['source_snapshot']['reference']]
    actual_digest = provenance.source_digest
    calls = []
    def counted(conn, log_id):
        calls.append(log_id)
        return actual_digest(conn, log_id)
    monkeypatch.setattr(provenance, 'source_digest', counted)
    monkeypatch.setattr(provenance, 'MAX_CHECKED_LOGS', 1)
    results = provenance.origins(saved)
    assert calls == [log]
    assert [item['status'] for item in results] == ['current','current','not_checked_work_budget']
    assert results[0]['source_fingerprint_checked_at'] == results[1]['source_fingerprint_checked_at']
    assert results[2]['source_fingerprint_checked_at'] is None


def test_real_routes_keep_source_details_dashboard_only_and_connect_to_the_existing_journey(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as w, automation_pipeline as pipeline
from server.local_process_catalog import catalog
from shared.process_catalog import CatalogWrite
from tests.test_process_opportunity_lineage import ref,assess
from server.opportunity_projects import export_project
log=w.import_log(BytesIO(b'case,activity,time\nPRIVATE_CASE,Open,2025-01-01T00:00:00Z\nPRIVATE_CASE,Close,2025-01-01T01:00:00Z\n'),name='Native cases',source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 base='/v1/event-logs/'+log
 preview=c.get(base+'/model-discovery',headers=h).json()
 body={'expected_model_sha256':preview['process_model_sha256']}
 assert c.post(base+'/model-discovery-draft',json=body,headers=h).status_code==403
 assert c.post(base+'/model-discovery-draft',json=body,headers={**bearer,'x-owg-local-storage-confirmed':'true'}).status_code in (401,403)
 r=c.post(base+'/model-discovery-draft',json=body,headers={**h,'x-owg-local-storage-confirmed':'true'})
 assert r.status_code==200,r.text
 assert r.headers['cache-control']=='no-store'
 made=r.json();snapshot=made['source_snapshot']
 model={**made['process_model'],'goal':'Review supplied cases'}
 saved=catalog.save(CatalogWrite(title='Reviewed log',process_model=model,user_confirmed=True,evidence_refs=[snapshot['reference']]))
 url='/v1/event-log-discovery/process-origin?workflow_id='+saved['workflow_id']+'&revision=1'
 assert c.get(url,headers=bearer).status_code in (401,403)
 origins=c.get(url,headers=h)
 assert origins.status_code==200 and origins.headers['cache-control']=='no-store'
 assert origins.json()['origins'][0]['status']=='current'
 assert origins.json()['origins'][0]['reviewed_graph_matches_discovery'] is False
 idea=assess(pipeline.create_from_process(ref(saved)))
 idea=pipeline.transition(idea['idea_id'],{'to':'approved','expected_revision':idea['revision']})
 project,artifact=export_project(idea['idea_id'],{'expected_revision':idea['revision']})
 assert artifact['process_reference']==ref(saved) and artifact['execution_authorized'] is False
 journey=c.get('/v1/automation-pipeline/'+idea['idea_id']+'/journey',headers=h).json()
 assert journey['dataset_cohort_origin_linked'] is True
 assert journey['dataset_derivation_independently_verified'] is False
 assert journey['dataset_origins'][0]['source_content_sha256']==snapshot['source_content_sha256']
 assert 'PRIVATE_CASE' not in str(journey) and 'selection' not in journey['dataset_origins'][0]
 w.delete_log(log)
 assert c.get(url,headers=h).json()['origins'][0]['status']=='unavailable'
 assert catalog.get(saved['workflow_id'],1)['evidence_refs']==[snapshot['reference']]
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.environ.get('OWG_SCALE_ACCEPTANCE') != '1', reason='opt-in large source fingerprint acceptance')
def test_large_source_snapshot_and_lookup_cover_every_case():
    from datetime import datetime
    import time
    import duckdb
    events = int(os.environ.get('OWG_DISCOVERY_SCALE_EVENTS', '1000000'))
    assert 100_000 <= events <= 10_000_000 and events % 10 == 0
    cases = events // 10
    log = 'log_0123456789abcdef'
    conn = workspace._connect()
    try:
        conn.execute('INSERT INTO logs VALUES (?,?,?,?,?,?,?)', [log,'Owned scale source','csv',datetime(2025,1,1),events,cases,0])
        conn.execute("""INSERT INTO events SELECT ?, 'event_'||i::VARCHAR, 'Step '||(i%10)::VARCHAR,
            TIMESTAMP '2025-01-01' + i * INTERVAL '1 SECOND', 'team_'||(i%4)::VARCHAR, '{"cost":1}'
            FROM range(?) AS source(i)""", [log,events])
        conn.execute("""INSERT INTO objects SELECT ?, 'case_'||i::VARCHAR, 'case', '{"region":"EMEA"}'
            FROM range(?) AS source(i)""", [log,cases])
        conn.execute("""INSERT INTO event_objects SELECT ?, 'event_'||i::VARCHAR,
            'case_'||(i//10)::VARCHAR, 'case' FROM range(?) AS source(i)""", [log,events])
    finally:
        conn.close()
    preview = discovery.discover(log)
    started = time.perf_counter()
    made = provenance.record_draft(log, {'expected_model_sha256':preview['process_model_sha256']})
    recorded_seconds = time.perf_counter() - started
    started = time.perf_counter()
    origin = provenance.origins(record(made))[0]
    checked_seconds = time.perf_counter() - started
    assert made['quality']['cases_total'] == made['quality']['cases_used'] == cases
    assert origin['status'] == 'current' and origin['reviewed_graph_matches_discovery'] is True
    assert origin['source_content_sha256'] == made['source_snapshot']['source_content_sha256']
    assert origin['quality']['cases_used'] == cases
    print(f'\nsource fingerprint acceptance: {events:,} events, {cases:,} cases, '
          f'{2*events+cases:,} total source rows; record {recorded_seconds:.2f}s, '
          f'lookup {checked_seconds:.2f}s; DuckDB {duckdb.__version__}; supplied synthetic data only')
