"""Read-only journey over existing local references, never an evidence join store."""
from contextlib import closing
import json

from . import automation_pipeline as pipeline
from . import project_report_links as reports
from .opportunity_projects import MAX_PROJECTS_PER_IDEA


def journey(idea_id: str) -> dict:
    # One idea-store snapshot; other local stores are checked afterwards. This is
    # not an atomic snapshot across stores, nor a decision/authorization check.
    with pipeline._LOCK, closing(pipeline._connect()) as conn, conn:
        conn.execute('BEGIN')
        idea = pipeline._public(pipeline._get(conn, idea_id))
        projects = conn.execute('SELECT project_id,idea_revision,created_at,process_reference,spec_sha256,archive_sha256 '
                                'FROM idea_projects WHERE idea_id=? ORDER BY created_at DESC,project_id LIMIT ?',
                                [idea_id, MAX_PROJECTS_PER_IDEA + 1]).fetchall()
        links = conn.execute('SELECT l.project_id,l.deployment_id,l.linked_at FROM project_report_links l '
                             'JOIN idea_projects p ON p.project_id=l.project_id WHERE p.idea_id=? '
                             'ORDER BY l.project_id,l.deployment_id', [idea_id]).fetchall()
        exports = conn.execute('SELECT e.project_id,e.export_id,e.created_at,e.document_sha256 FROM project_source_exports e '
                               'JOIN idea_projects p ON p.project_id=e.project_id WHERE p.idea_id=? '
                               'ORDER BY e.created_at DESC,e.export_id', [idea_id]).fetchall()
    sources, available = reports._sources() if links else ({}, True)
    grouped = {}
    for project, deployment, at in links:
        grouped.setdefault(project, []).append((deployment, at))
    exported = {}
    for project, export_id, at, digest in exports:
        summary = exported.setdefault(project, {'count': 0, 'last_export_at': at,
                                               'last_export_id': export_id, 'last_document_sha256': digest})
        summary['count'] += 1
    items = []
    for project, revision, at, reference, spec, archive in projects[:MAX_PROJECTS_PER_IDEA]:
        linked = []
        for deployment, linked_at in grouped.get(project, []):
            source = sources.get(deployment)
            linked.append({'deployment_id': deployment, 'linked_at': linked_at,
                           'status': reports._status(spec, source, available),
                           'reported_run_count': source['reported_run_count'] if source else None})
        items.append({'project_id': project, 'idea_revision': revision, 'created_at': at,
                      'idea_revision_is_current': revision == idea['revision'],
                      'process_reference': json.loads(reference), 'spec_sha256': spec, 'archive_sha256': archive,
                      'reported_specifications': linked,
                      'source_check_exports': exported.get(project, {'count': 0, 'last_export_at': None,
                                                                    'last_export_id': None, 'last_document_sha256': None})})
    return {'format': 'openworkgraph.opportunity-journey.v1',
            'idea': {key: idea[key] for key in ('idea_id', 'revision', 'state')},
            'process_status': pipeline._process_status(idea.get('process_reference')),
            'projects': items, 'projects_truncated': len(projects) > MAX_PROJECTS_PER_IDEA,
            'max_projects': MAX_PROJECTS_PER_IDEA, 'snapshot_is_atomic_across_stores': False,
            'dataset_cohort_origin_linked': False, 'source_receipt_results_retained': False,
            'source_read_performed': False, 'artifact_execution_verified': False,
            'reported_run_case_identity_verified': False, 'business_outcomes_verified': False,
            'execution_authorized': False}
