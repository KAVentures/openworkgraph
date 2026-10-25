"""Explicit artifact-to-reported-deployment references, never outcome verification."""
from contextlib import closing
from typing import Any

from pydantic import Field, StrictInt

from . import automation_pipeline as pipeline
from . import automation_monitoring as monitoring

MAX_LINKS_PER_PROJECT = 10
CLAIMS = {'reported_by_automation_not_verified_by_owg': True,
          'per_run_attribution_verified': False, 'independent_execution_verified': False,
          'business_outcomes_verified': False, 'execution_authorized': False}


class LinkRequest(pipeline._Strict):
    project_id: str = Field(pattern=r'^project_[0-9a-f]{64}$')
    deployment_id: str = Field(pattern=r'^dep_[0-9a-f]{16}$')
    expected_revision: StrictInt = Field(ge=1, le=2**63-1)


def _sources():
    try:
        return {r['deployment_id']:r for r in monitoring.deployment_references()}, True
    except Exception:
        return {}, False


def _status(spec_sha256, source, available):
    if not available:
        return 'check_failed'
    if source is None:
        return 'unavailable'
    if source['design_sha256'] is None or source['reported_spec_sha256'] is None:
        return 'unbound_specification'
    return ('matching_reported_specification' if source['design_sha256'] == source['reported_spec_sha256'] == spec_sha256
            else 'specification_mismatch')


def link(idea_id: str, payload: Any) -> dict[str, Any]:
    request = pipeline._validate(LinkRequest, payload)
    with pipeline._LOCK, closing(pipeline._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = pipeline._get(conn, idea_id)
        pipeline._check_revision(row, request.expected_revision)
        artifact = conn.execute('SELECT spec_sha256 FROM idea_projects WHERE idea_id=? AND project_id=?',
                                [idea_id, request.project_id]).fetchone()
        if artifact is None:
            raise pipeline.PipelineError('select a project recorded for this idea')
        sources, available = _sources()
        status = _status(artifact[0], sources.get(request.deployment_id), available)
        if status != 'matching_reported_specification':
            raise pipeline.PipelineError('monitoring specification changed, is unavailable or is not bound to this project')
        exists = conn.execute('SELECT linked_at FROM project_report_links WHERE project_id=? AND deployment_id=?',
                              [request.project_id,request.deployment_id]).fetchone()
        if not exists:
            count = conn.execute('SELECT count(*) FROM project_report_links WHERE project_id=?',[request.project_id]).fetchone()[0]
            if count >= MAX_LINKS_PER_PROJECT:
                raise pipeline.PipelineError(f'at most {MAX_LINKS_PER_PROJECT} report links per project; remove a link first')
            at = pipeline._now()
            conn.execute('INSERT INTO project_report_links VALUES (?,?,?)',[request.project_id,request.deployment_id,at])
        else:
            at = exists[0]
        return {'project_id':request.project_id,'deployment_id':request.deployment_id,
                'linked_at':at,'status':status,**CLAIMS}


def unlink(idea_id: str, payload: Any) -> dict[str, Any]:
    request = pipeline._validate(LinkRequest, payload)
    with pipeline._LOCK, closing(pipeline._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        pipeline._check_revision(pipeline._get(conn,idea_id),request.expected_revision)
        if not conn.execute('SELECT 1 FROM idea_projects WHERE idea_id=? AND project_id=?',[idea_id,request.project_id]).fetchone():
            raise pipeline.PipelineError('select a project recorded for this idea')
        cursor = conn.execute('DELETE FROM project_report_links WHERE project_id=? AND deployment_id=?',
                              [request.project_id,request.deployment_id])
        return {'removed':bool(cursor.rowcount),'monitoring_reports_deleted':False,**CLAIMS}


def links(idea_id: str) -> dict[str, Any]:
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        idea = pipeline._public(pipeline._get(conn,idea_id))
        artifacts = conn.execute('SELECT project_id,idea_revision,created_at,spec_sha256 FROM idea_projects '
                                 'WHERE idea_id=? ORDER BY created_at DESC,project_id',[idea_id]).fetchall()
        rows = conn.execute('SELECT p.project_id,p.spec_sha256,l.deployment_id,l.linked_at FROM idea_projects p '
                            'JOIN project_report_links l ON l.project_id=p.project_id WHERE p.idea_id=? '
                            'ORDER BY l.linked_at DESC,p.project_id,l.deployment_id',[idea_id]).fetchall()
    sources, available = _sources()
    return {'links':[{'project_id':p,'deployment_id':d,'linked_at':at,
                      'status':_status(spec,sources.get(d),available),**CLAIMS}
                     for p,spec,d,at in rows],
            'projects':[dict(zip(('project_id','idea_revision','created_at','spec_sha256'),r)) for r in artifacts],
            'deployments':list(sources.values()),'monitoring_metadata_available':available,
            'current_idea_revision':idea['revision'],'max_links_per_project':MAX_LINKS_PER_PROJECT,**CLAIMS}
