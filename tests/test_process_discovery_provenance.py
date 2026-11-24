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
 inspect='/v1/event-log-discovery/inspect-selection?workflow_id='+saved['workflow_id']+'&revision=1'
 inspect_body={'reference':snapshot['reference'],'expected_process_sha256':saved['content_sha256']}
 assert c.post(inspect,json=inspect_body,headers=bearer).status_code in (401,403)
 inspected=c.post(inspect,json=inspect_body,headers=h)
 assert inspected.status_code==200 and inspected.headers['cache-control']=='no-store',inspected.text
 assert inspected.json()['cases'][0]['case_id']=='PRIVATE_CASE'
 assert inspected.json()['effective_input_status']=='matches'
 detail=c.post(inspect,json={**inspect_body,'case_id':'PRIVATE_CASE','expected_projection_sha256':inspected.json()['current_effective_input']['sha256']},headers=h)
 assert detail.status_code==200 and len(detail.json()['events'])==2,detail.text

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


def _inspection_basis(result):
    saved = {**record(result), "workflow_id": "reviewed_model", "revision": 1,
             "content_sha256": "a" * 64}
    request = {"reference": result["source_snapshot"]["reference"],
               "expected_process_sha256": saved["content_sha256"]}
    return saved, request


def test_exact_effective_input_detects_same_model_changed_case_membership():
    from server import event_log_case_tags as tags

    log = _log([["Receive", "Close"]] * 3)
    tags.add(log, {"tag": "Reviewed", "case_ids": ["c0"]})
    cohort = {"tag": "Reviewed"}
    preview = discovery.discover(log, cohort=cohort, _pin_projection=True)
    original = preview["effective_input"]
    assert original["cases"] == 1 and original["events"] == 2
    body = {"expected_model_sha256": preview["process_model_sha256"],
            "expected_projection_sha256": original["sha256"], "cohort": cohort}
    saved = provenance.record_draft(log, body)
    assert saved["source_snapshot"]["effective_input"] == original
    assert provenance.origins(record(saved))[0]["effective_input_status"] == "matches"

    # Equal path, count, timestamps relative to each case and model: only
    # the selected case membership changes, with no modification of raw rows.
    tags.remove(log, {"tag": "Reviewed", "case_ids": ["c0"]})
    tags.add(log, {"tag": "Reviewed", "case_ids": ["c1"]})
    changed = discovery.discover(log, cohort=cohort, _pin_projection=True)
    assert changed["process_model_sha256"] == preview["process_model_sha256"]
    assert changed["effective_input"]["sha256"] != original["sha256"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="effective discovery input changed"):
        provenance.record_draft(log, body)
    origin = provenance.origins(record(saved))[0]
    assert origin["status"] == "projection_changed"
    assert origin["effective_input_status"] == "changed"
    assert origin["current_effective_input"] == changed["effective_input"]
    assert origin["source_content_sha256"] == saved["source_snapshot"]["source_content_sha256"]


def test_hidden_and_mapped_activities_change_effective_input_without_changing_raw_digest():
    from server import event_log_hidden_activities as hidden
    from server import event_log_activity_mappings as mappings

    log, _, _, made = prepare()
    saved = record(made)
    initial = provenance.origins(saved)[0]
    hidden.set_hidden(log, {"activities": ["Review"]})
    changed = provenance.origins(saved)[0]
    assert changed["status"] == "projection_changed"
    assert changed["current_effective_input"]["events"] == 8
    assert changed["source_content_sha256"] == initial["source_content_sha256"]
    hidden.set_hidden(log, {"activities": []})
    assert provenance.origins(saved)[0]["effective_input_status"] == "matches"
    mapping = mappings.create(log, {"name": "Group review", "groups": [
        {"label": "Check", "activities": ["Review"]}]})
    mappings.apply_mapping(log, mapping["mapping_id"])
    changed = provenance.origins(saved)[0]
    assert changed["status"] == "projection_changed"
    assert changed["current_effective_input"]["events"] == initial["effective_input"]["events"]
    mappings.apply_mapping(log, None)
    assert provenance.origins(saved)[0]["effective_input_status"] == "matches"


def test_exact_selection_pages_reject_outside_case_and_stale_projection():
    from server import event_log_case_tags as tags

    log = _log([["Open", "Review", "Close"]] * 4)
    tags.add(log, {"tag": "selected", "case_ids": ["c0", "c2", "c3"]})
    preview = discovery.discover(log, cohort={"tag": "selected"}, _pin_projection=True)
    result = provenance.record_draft(log, {"expected_model_sha256": preview["process_model_sha256"],
        "expected_projection_sha256": preview["effective_input"]["sha256"], "cohort": {"tag": "selected"}})
    saved, basis = _inspection_basis(result)
    first = provenance.inspect_selection(saved, {**basis, "limit": 1})
    assert first["effective_input_status"] == "matches"
    assert first["historical_rows_retained"] is False
    assert first["raw_source_fingerprint_rechecked"] is False
    assert first["current_effective_input"]["cases"] == 3
    assert first["case_drift"] == {"status": "complete", "added": 0, "modified": 0,
        "unchanged": 3, "removed": 0, "original_cases": 3, "current_cases": 3,
        "removed_case_ids_retained": False}
    assert first["cases"][0]["comparison_status"] == "unchanged"

    assert first["cases"][0]["case_id"] == "c0" and first["next_after"] == "c0"
    pinned = {**basis, "expected_projection_sha256": first["current_effective_input"]["sha256"]}
    rest = provenance.inspect_selection(saved, {**pinned, "after": "c0", "limit": 2})
    assert [row["case_id"] for row in rest["cases"]] == ["c2", "c3"]
    assert rest["next_after"] is None
    detail = provenance.inspect_selection(saved, {**pinned, "case_id": "c2", "limit": 2})
    assert [event["activity"] for event in detail["events"]] == ["Open", "Review"]
    assert detail["next_offset"] == 2
    last = provenance.inspect_selection(saved, {**pinned, "case_id": "c2", "offset": 2})
    assert [event["activity"] for event in last["events"]] == ["Close"]
    assert last["next_offset"] is None
    for bad in ({**pinned, "case_id": "c1"}, {**basis, "case_id": "c0"},
                {**basis, "after": "c0"}, {**pinned, "expected_process_sha256": "b"*64},
                {**pinned, "reference": provenance.PREFIX+"0"*64},
                {**pinned, "cohort": {}}, {**pinned, "case_id": "c2", "after": "c0"}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            provenance.inspect_selection(saved, bad)
    tags.remove(log, {"tag": "selected", "case_ids": ["c2"]})
    with pytest.raises(workspace.EventLogWorkspaceError, match="changed since inspection"):
        provenance.inspect_selection(saved, {**pinned, "after": "c0"})
    changed = provenance.inspect_selection(saved, basis)
    assert changed["effective_input_status"] == "changed"
    assert [row["case_id"] for row in changed["cases"]] == ["c0", "c3"]
    assert changed["case_drift"]["removed"] == 1
    assert changed["case_drift"]["unchanged"] == 2
    assert changed["case_drift"]["added"] == 0
    assert changed["case_drift"]["modified"] == 0
    assert '"c2"' not in json.dumps(changed)

    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError, match="unavailable"):
        provenance.inspect_selection(saved, basis)


def test_legacy_discovery_selection_reports_unknown_historical_membership():
    log, _, _, result = prepare()
    original_ref = result["source_snapshot"]["reference"]
    conn = workspace._connect()
    try:
        payload = json.loads(conn.execute("SELECT payload FROM discovery_snapshots WHERE reference=?", [original_ref]).fetchone()[0])
        payload.pop("effective_input")
        payload.pop("case_digest_index", None)
        legacy_ref = provenance.PREFIX + provenance.digest(payload)
        conn.execute("UPDATE discovery_snapshots SET reference=?,payload=? WHERE reference=?",
                     [legacy_ref, json.dumps(payload), original_ref])
    finally:
        conn.close()
    result["source_snapshot"]["reference"] = legacy_ref
    saved, basis = _inspection_basis(result)
    assert provenance.origins(saved)[0]["effective_input_status"] == "not_recorded"
    inspected = provenance.inspect_selection(saved, basis)
    assert inspected["original_effective_input"] is None
    assert inspected["effective_input_status"] == "not_recorded"
    assert inspected["historical_rows_retained"] is False


def test_complete_case_drift_counts_and_filters_no_raw_removed_keys():
    from server import event_log_case_tags as tags

    log = _log([["Open", "Review", "Close"]] * 4)
    tags.add(log, {"tag": "selected", "case_ids": ["c0", "c1", "c2"]})
    preview = discovery.discover(log, cohort={"tag": "selected"}, _pin_projection=True)
    made = provenance.record_draft(log, {"expected_model_sha256": preview["process_model_sha256"],
                                        "cohort": {"tag": "selected"}})
    saved, basis = _inspection_basis(made)
    tags.remove(log, {"tag": "selected", "case_ids": ["c0"]})
    tags.add(log, {"tag": "selected", "case_ids": ["c3"]})
    conn = workspace._connect()
    try:
        conn.execute("""UPDATE events SET activity='Escalate' WHERE log_id=? AND event_id IN
            (SELECT event_id FROM event_objects WHERE log_id=? AND object_id='c1')
            AND activity='Review'""", [log, log])
    finally:
        conn.close()
    result = provenance.inspect_selection(saved, basis)
    assert result["case_drift"] == {"status": "complete", "added": 1, "removed": 1, "modified": 1,
        "unchanged": 1, "original_cases": 3, "current_cases": 3, "removed_case_ids_retained": False}
    assert {row["case_id"]: row["comparison_status"] for row in result["cases"]} == {
        "c1": "modified", "c2": "unchanged", "c3": "added"}
    assert '"c0"' not in json.dumps(result)
    pin = result["current_effective_input"]["sha256"]
    for status, case in (("added", "c3"), ("modified", "c1"), ("unchanged", "c2")):
        filtered = provenance.inspect_selection(saved, {**basis, "case_status": status, "expected_projection_sha256": pin})
        assert filtered["matching_cases"] == 1
        assert [row["case_id"] for row in filtered["cases"]] == [case]
    # No changed case in a matching original snapshot means an exact empty list.
    tags.remove(log, {"tag": "selected", "case_ids": ["c3"]})
    filtered = provenance.inspect_selection(saved, {**basis, "case_status": "added"})
    assert filtered["matching_cases"] == 0 and filtered["cases"] == []


def test_case_drift_budget_never_stores_or_presents_partial_index(monkeypatch):
    monkeypatch.setattr(provenance, "MAX_INDEXED_CASES", 2)
    log, _, _, made = prepare()
    saved, basis = _inspection_basis(made)
    conn = workspace._connect()
    try:
        payload = json.loads(conn.execute("SELECT payload FROM discovery_snapshots WHERE reference=?",
                                          [basis["reference"]]).fetchone()[0])
    finally:
        conn.close()
    assert "case_digest_index" not in payload
    assert provenance.inspect_selection(saved, basis)["case_drift"] == {"status": "not_recorded"}
    with pytest.raises(workspace.EventLogWorkspaceError, match="Complete case comparison is unavailable"):
        provenance.inspect_selection(saved, {**basis, "case_status": "modified"})
    monkeypatch.setattr(provenance, "MAX_INDEXED_CASES", 10)
    _, _, _, indexed = prepare()
    saved, basis = _inspection_basis(indexed)
    monkeypatch.setattr(provenance, "MAX_INDEXED_EVENTS", 1)
    assert provenance.inspect_selection(saved, basis)["case_drift"]["status"] == "current_work_budget_exceeded"


def test_discovery_inspection_and_handoff_browser_models():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const scope={window:{}};
vm.runInNewContext(fs.readFileSync('dashboard/event_log_workspace.js','utf8'),scope);
vm.runInNewContext(fs.readFileSync('dashboard/automation_pipeline.js','utf8'),scope);
const matches=scope.window.__owgEventLogModel.matchingDiscoveryInspection;
const m=scope.window.__owgPipelineModel;
const sha='a'.repeat(64),other='b'.repeat(64),ref='urn:openworkgraph:local-log-discovery:'+sha;
const basis={workflow_id:'workflow',revision:2,content_sha256:sha,reference:ref,log_id:'log_aaaaaaaaaaaaaaaa'};
const pin={format:'openworkgraph.discovery-projection.v1',sha256:sha,cases:2,events:4};
const changed={...pin,sha256:other};
const common={format:'openworkgraph.discovery-selection-inspection.v1',reference:ref,workflow_id:basis.workflow_id,
  revision:2,process_sha256:sha,log_id:basis.log_id,selection_sha256:sha,
  selection:{case_notion:'case',cohort:{tag:'reviewed'},settings:{noise:0,activities:1}},
  original_effective_input:pin,current_effective_input:changed,effective_input_status:'changed',
  historical_rows_retained:false,raw_source_fingerprint_rechecked:false,
  business_case_identity_verified:false,business_outcomes_verified:false,execution_authorized:false,
  checked_at:'2026-11-24T06:00:00Z',
  case_drift:{status:'complete',added:1,removed:1,modified:1,unchanged:0,original_cases:2,current_cases:2,removed_case_ids_retained:false}};
const caseRow={case_id:'new',events:2,elapsed_seconds:1,timestamp_ties:0,comparison_status:'added'};
const result={...common,mode:'cases',case_status:'added',matching_cases:1,cases:[caseRow],next_after:null};
const page={limit:30,case_status:'added',expected_projection_sha256:other};
assert(matches(result,basis,page));
assert(!matches(result,basis,{...page,case_status:'modified'}));
assert(!matches(result,basis,{...page,expected_projection_sha256:sha}));
assert(!matches({...result,revision:3},basis,page));
assert(!matches({...result,case_drift:{...common.case_drift,removed:0}},basis,page));
assert(!matches({...result,cases:[{...caseRow,comparison_status:'unchanged'}]},basis,page));
assert(!matches({...result,business_outcomes_verified:true},basis,page));
const eventPage={case_id:'new',offset:0,limit:1,expected_projection_sha256:other};
const events={...common,mode:'events',case_id:'new',offset:0,total_events:2,next_offset:1,comparison_status:'added',
  events:[{event_id:'e1',activity:'Open',timestamp:'2026-11-24T00:00:00Z'}]};
assert(matches(events,basis,eventPage));
assert(!matches({...events,next_offset:2},basis,eventPage));
assert(!matches({...events,case_id:'wrong'},basis,eventPage));
const journey={idea:{idea_id:'idea',revision:1,state:'approved'},
  process_status:{status:'current',process_reference:{workflow_id:'workflow',revision:2,content_sha256:sha}},
  projects:[{project_id:'project',idea_revision:1,idea_revision_is_current:true,spec_sha256:sha,archive_sha256:sha,
    reported_specifications:[],source_check_exports:{count:0,last_document_sha256:null}}],
  dataset_origins:[{status:'current',source_content_sha256:sha,selection_sha256:sha,
    effective_input:{...pin,case_ids:['PRIVATE CASE']},current_effective_input:pin,effective_input_status:'matches'}]};
const before=m.handoffReviewPacket(journey,'project','idea','2026-11-24T06:00:00Z');
assert.equal(before.dataset_origins[0].effective_input.status,'matches');
assert(!JSON.stringify(before).includes('PRIVATE CASE'));
assert(!before.readiness.blockers.includes('effective_discovery_input_not_current'));
journey.dataset_origins[0].current_effective_input=changed;
journey.dataset_origins[0].effective_input_status='changed';
journey.dataset_origins[0].status='projection_changed';
const after=m.handoffReviewPacket(journey,'project','idea','2026-11-24T06:00:01Z');
assert(after.readiness.blockers.includes('effective_discovery_input_not_current'));
assert.equal(after.dataset_origins[0].effective_input.current_sha256,other);
assert(m.compareHandoffReviewPackets(before,after).changes.includes('Dataset and cohort source checks'));
assert.equal(m.handoffDependencyReview(journey,'project','idea','2026-11-24T06:00:01Z').stages[0].status,'needs_review');
console.log('Exact discovery inspection and handoff model checks passed');
"""
    result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
