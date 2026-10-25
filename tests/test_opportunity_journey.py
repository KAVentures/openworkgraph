"""A connected local journey exposes gaps instead of manufacturing execution evidence."""
import json

import pytest

from server import automation_pipeline as pipeline, automation_monitoring as monitoring
from server import opportunity_journey as journey, project_source_checks as checks
from server import project_report_links as links
from shared.process_catalog import CatalogWrite
from tests.test_opportunity_projects import setup
from tests.test_project_report_links import prepared, deployment, body, report


def dump():
    with pipeline._connect() as conn:
        return '\n'.join(conn.iterdump())


def test_connected_read_is_minimized_read_only_and_does_not_infer_outcomes(prepared, monkeypatch):
    _, _, idea, artifact, spec = prepared
    dep = deployment(spec)
    # An unrelated report and its label must not leak into this idea's journey.
    unrelated = monitoring.import_report(report(name='PRIVATE_UNRELATED_REPORT'))
    links.link(idea['idea_id'], body(idea, artifact, dep))
    with pipeline._connect() as conn, conn:
        for n in range(2):
            conn.execute('INSERT INTO project_source_exports VALUES (?,?,?,?,?)',
                         ['source_export_' + str(n) * 64, artifact['project_id'], idea['revision'],
                          f'2026-01-0{n+1}T00:00:00Z', str(n) * 64])
    def forbidden(*args):
        raise AssertionError('journey must not read a provider')
    monkeypatch.setattr(checks, '_read_receipt', forbidden)
    before = dump()
    result = journey.journey(idea['idea_id'])
    assert dump() == before
    assert result['process_status']['status'] == 'current'
    project = result['projects'][0]
    assert project['archive_sha256'] == artifact['archive_sha256']
    assert project['spec_sha256'] == artifact['spec_sha256']
    assert project['reported_specifications'][0]['status'] == 'matching_reported_specification'
    assert project['reported_specifications'][0]['reported_run_count'] == 1
    assert project['source_check_exports']['count'] == 2
    assert project['source_check_exports']['last_document_sha256'] == '1' * 64
    for key in ('dataset_cohort_origin_linked', 'source_receipt_results_retained', 'source_read_performed',
                'artifact_execution_verified', 'reported_run_case_identity_verified',
                'business_outcomes_verified', 'execution_authorized', 'snapshot_is_atomic_across_stores'):
        assert result[key] is False
    serialized = json.dumps(result)
    for private in ('PRIVATE_UNRELATED_REPORT', unrelated['deployment']['deployment_id'], 'CLIENT_ASSERTION',
                    'provider_reported_state', 'operation_verified', 'resource_ref'):
        assert private not in serialized


def test_process_and_report_drift_are_rechecked_without_retargeting(prepared, monkeypatch):
    catalog, record, idea, artifact, spec = prepared
    dep = deployment(spec)
    links.link(idea['idea_id'], body(idea, artifact, dep))
    catalog.save(CatalogWrite(workflow_id=record['workflow_id'], title=record['title'],
        expected_revision=record['revision'], process_model=record['process_model'], user_confirmed=True))
    monitoring.delete_deployment(dep)
    result = journey.journey(idea['idea_id'])
    assert result['process_status']['status'] == 'changed'
    assert result['projects'][0]['process_reference'] == idea['process_reference']
    assert result['projects'][0]['reported_specifications'][0]['deployment_id'] == dep
    assert result['projects'][0]['reported_specifications'][0]['status'] == 'unavailable'
    monkeypatch.setattr(links, '_sources', lambda: ({}, False))
    assert journey.journey(idea['idea_id'])['projects'][0]['reported_specifications'][0]['status'] == 'check_failed'
    catalog.store.forget(catalog.owner, record['workflow_id'])
    assert journey.journey(idea['idea_id'])['process_status']['status'] == 'unavailable'


def test_unbound_and_deleted_ideas_never_invent_a_journey(prepared, monkeypatch):
    _, _, idea, _, _ = prepared
    manual = pipeline.create({'title': 'Manual idea'})
    def forbidden():
        raise AssertionError('no links: do not read monitoring')
    monkeypatch.setattr(links, '_sources', forbidden)
    result = journey.journey(manual['idea_id'])
    assert result['projects'] == [] and result['process_status']['status'] == 'unbound_reference'
    pipeline.delete(idea['idea_id'])
    with pytest.raises(pipeline.PipelineError, match='unknown idea'):
        journey.journey(idea['idea_id'])


def test_historical_artifact_is_not_presented_as_current_idea_revision(prepared):
    _, _, idea, artifact, _ = prepared
    pipeline.transition(idea['idea_id'], {'to': 'on_hold', 'expected_revision': idea['revision']})
    result = journey.journey(idea['idea_id'])
    assert result['projects'][0]['project_id'] == artifact['project_id']
    assert result['projects'][0]['idea_revision_is_current'] is False


def test_unrelated_export_history_does_not_expand_the_journey_query_work(prepared, monkeypatch):
    _, _, idea, artifact, _ = prepared
    with pipeline._connect() as conn, conn:
        # Synthetic histories within the caps: 50 projects/idea, 50 exports/project.
        for n in range(400):
            project = 'project_' + f'{n:064x}'
            conn.execute('INSERT INTO idea_projects VALUES (?,?,?,?,?,?,?,?)',
                [project, 'other_idea_' + str(n//50), 1, '2026-01-01', '{}', '0'*64, '0'*64, '0'*64])
            conn.executemany('INSERT INTO project_source_exports VALUES (?,?,?,?,?)',
                [('source_export_' + f'{n*50+k:064x}', project, 1, '2026-01-01', '0'*64) for k in range(50)])
        # Simulate a populated pre-index database; normal connect must upgrade it.
        conn.execute('DROP INDEX IF EXISTS project_source_exports_by_project')
    connect = pipeline._connect
    work = [0]
    def count_work():
        conn = connect()
        def tick():
            work[0] += 100
            return 0
        conn.set_progress_handler(tick, 100)
        return conn
    monkeypatch.setattr(pipeline, '_connect', count_work)
    result = journey.journey(idea['idea_id'])
    assert result['projects'][0]['project_id'] == artifact['project_id']
    assert result['projects'][0]['source_check_exports']['count'] == 0
    # A VM-operation budget, not a machine-speed assertion. An unindexed scan
    # of 20,000 unrelated records exceeds this by orders of magnitude.
    assert work[0] < 2000, work[0]


def test_http_journey_is_dashboard_only_and_not_cached(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, automation_pipeline as p
idea=p.create({'title':'Journey'})
url='/v1/automation-pipeline/'+idea['idea_id']+'/journey'
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 assert c.get(url).status_code in (401,403)
 assert c.get(url,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code in (401,403)
 r=c.get(url,headers=h)
 assert r.status_code==200,r.text
 assert r.headers['cache-control']=='no-store'
 assert r.json()['source_read_performed'] is False
 assert c.get('/v1/automation-pipeline/unknown/journey',headers=h).status_code==404
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    r = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
