"""Per-run specification attribution and run-bound source checks (#370 R5)."""
from datetime import datetime, timezone
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from gateway.db import GatewayDB
from shared.process_catalog import CatalogWrite, ProcessCatalog
from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeStore
from server import automation_monitoring as am, automation_pipeline as pipeline
from server import opportunity_journey as journey, opportunity_projects as projects
from server import project_report_links as links, project_source_checks as checks
from server.open_work_outcomes import source_operation_receipt
from tests.test_process_opportunity_lineage import assess, model, ref

RESOURCE = 'owg:r:' + 'a' * 32


def report(spec_hash, run_ids):
    deployment = {'name': 'Reported agent', 'runtime': 'openai-agents'}
    if spec_hash is not None:
        deployment['workflow_spec_sha256'] = spec_hash
    return {'format': am.FORMAT, 'deployment': deployment, 'runs': [
        {'run_id': rid, 'started_at': f'2024-03-0{i + 1}T10:00:00Z', 'finished_at': f'2024-03-0{i + 1}T10:01:00Z',
         'status': 'succeeded', 'steps': [], 'outcome': {'verified': True, 'source': 'CLIENT_ASSERTION'}}
        for i, rid in enumerate(run_ids)]}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(am, 'DATA_DIR', tmp_path)
    catalog = ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "catalog.db"}')), 'local')
    monkeypatch.setattr(pipeline, '_catalog', lambda: catalog)
    graph = model().model_dump()
    graph['outcome_expectations'] = [{'terminal_activity_id': 'review', 'operation': 'github_pr_merged',
                                      'user_confirmed': True}]
    record = catalog.save(CatalogWrite(workflow_id='review', title='Case review',
                                       process_model=ProcessModel.model_validate(graph), user_confirmed=True))
    idea = assess(pipeline.create_from_process(ref(record)))
    idea = pipeline.transition(idea['idea_id'], {'to': 'approved', 'expected_revision': idea['revision']})
    content, project = projects.export_project(idea['idea_id'], {'expected_revision': idea['revision']})
    with ZipFile(BytesIO(content)) as z:
        spec = json.loads(z.read('workflow.spec.json'))
    digest = am.spec_digest(spec)
    # One deployment: two runs report the exact spec, one legacy run reports no hash.
    dep = am.import_report(report(digest, ['run_a', 'run_b']))['deployment']['deployment_id']
    am.import_report(report(None, ['legacy_run']), dep)
    am.attach_design(dep, spec)

    def read(resource, operation):
        def reader(resource_ref, **kwargs):
            return {'resource_ref': resource_ref, 'source_rows_scoped_to_exact_record': True,
                    'source_log_join_supported': True, 'source_window': {'since': None, 'until': None},
                    'source_event_limit_reached': False, 'record_observed': True,
                    'record_identity_complete_within_retained_window': True,
                    'record_type': {'provider': 'github', 'kind': 'pull_request'},
                    'source_evidence': {'availability': 'source_reported', 'source_state_reported': True,
                                        'source_state': 'merged', 'verified_at': datetime.now(timezone.utc).isoformat(),
                                        'provider': 'github',
                                        'verification_method': 'explicit_opt_in_authenticated_local_github_cli_read'}}
        return source_operation_receipt(resource, operation, record_reader=reader)
    monkeypatch.setattr(checks, '_read_receipt', read)
    return idea, project, dep, digest


def body(idea, **changes):
    return {'expected_revision': idea['revision'], 'terminal_activity_id': 'review',
            'expected_operation': 'github_pr_merged', 'resource_ref': RESOURCE,
            'source_read_and_export_confirmed': True, **changes}


def test_runs_record_their_own_reported_specification(prepared):
    _, _, dep, digest = prepared
    [reference] = am.deployment_references()
    assert reference['reported_run_count'] == 3
    assert reference['runs_reporting_specification_count'] == 2 and reference['unhashed_run_count'] == 1
    assert {r['run_id'] for r in am.attributed_runs([dep], digest)} == {'run_a', 'run_b'}
    assert am.attributed_run(dep, 'legacy_run', digest) is None
    assert am.attributed_run(dep, 'run_a', 'f' * 64) is None


def test_links_and_journey_separate_attributed_and_unhashed_runs(prepared):
    idea, project, dep, _ = prepared
    links.link(idea['idea_id'], {'project_id': project['project_id'], 'deployment_id': dep,
                                 'expected_revision': idea['revision']})
    [link] = links.links(idea['idea_id'])['links']
    assert (link['runs_reporting_specification_count'], link['unhashed_run_count']) == (2, 1)
    assert link['per_run_attribution_verified'] is False
    [item] = journey.journey(idea['idea_id'])['projects']
    [linked] = item['reported_specifications']
    assert (linked['runs_reporting_specification_count'], linked['unhashed_run_count']) == (2, 1)


def test_runs_are_offered_only_from_linked_matching_deployments(prepared):
    idea, project, dep, _ = prepared
    assert checks.options(idea['idea_id'], project['project_id'])['attributable_runs'] == []
    with pytest.raises(pipeline.PipelineError, match='linked deployment'):
        checks.export_check(idea['idea_id'], project['project_id'],
                            body(idea, run_reference={'deployment_id': dep, 'run_id': 'run_a'}))
    links.link(idea['idea_id'], {'project_id': project['project_id'], 'deployment_id': dep,
                                 'expected_revision': idea['revision']})
    offered = checks.options(idea['idea_id'], project['project_id'])['attributable_runs']
    assert [r['run_id'] for r in offered] == ['run_b', 'run_a']
    assert all('outcome' not in r and 'steps' not in r for r in offered)


def test_source_check_binds_one_attributed_run_without_verifying_it(prepared):
    idea, project, dep, digest = prepared
    links.link(idea['idea_id'], {'project_id': project['project_id'], 'deployment_id': dep,
                                 'expected_revision': idea['revision']})
    with pytest.raises(pipeline.PipelineError, match='exact specification'):
        checks.export_check(idea['idea_id'], project['project_id'],
                            body(idea, run_reference={'deployment_id': dep, 'run_id': 'legacy_run'}))
    content, metadata = checks.export_check(idea['idea_id'], project['project_id'],
                                            body(idea, run_reference={'deployment_id': dep, 'run_id': 'run_a'}))
    document = json.loads(content)
    run = document['run_reference']
    assert run['run_id'] == 'run_a' and run['run_reported_spec_sha256'] == digest == document['spec_sha256']
    assert run['attribution'] == 'run_report_declared_exact_project_specification'
    assert document['association_basis'] == 'user_selected_project_run_expectation_and_exact_observed_record'
    assert document['reported_run_case_identity_verified'] is False and document['agent_caused_operation_verified'] is False
    assert 'CLIENT_ASSERTION' not in content.decode()
    plain, _ = checks.export_check(idea['idea_id'], project['project_id'], body(idea))
    assert 'run_reference' not in json.loads(plain)
    assert len(checks.exports(idea['idea_id'], project['project_id'])['exports']) == 2


def test_legacy_run_tables_upgrade_with_unknown_attribution(tmp_path, monkeypatch):
    import duckdb
    monkeypatch.setattr(am, 'DATA_DIR', tmp_path)
    conn = duckdb.connect(str(am.database_path()))
    conn.execute(am._SCHEMA)
    conn.execute("INSERT INTO deployments VALUES ('dep_0000000000000000','Old','other',now(),NULL,NULL,NULL,NULL)")
    conn.execute("INSERT INTO runs VALUES ('dep_0000000000000000','old_run',now(),NULL,'succeeded',NULL,'')")
    conn.close()
    [reference] = am.deployment_references()
    assert reference['unhashed_run_count'] == 1 and reference['runs_reporting_specification_count'] == 0
    am.import_report(report('c' * 64, ['new_run']), 'dep_0000000000000000')
    assert am.attributed_run('dep_0000000000000000', 'new_run', 'c' * 64)['run_status'] == 'succeeded'
