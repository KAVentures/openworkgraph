"""Explicit project exports linked to local opportunity revisions; never an executor.

The existing SDK specification and every generator file remain unchanged. An
additional manifest names the idea/revision and exact spec/base archive. Only
hashes and references are retained locally, not another model or archive store.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
from typing import Any
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from pydantic import Field, StrictInt

from shared.process_agent_project import build_runtime_project, build_runtime_spec
from . import automation_pipeline as pipeline

MAX_PROJECTS_PER_IDEA = 50


class ExportRequest(pipeline._Strict):
    expected_revision: StrictInt = Field(ge=1, le=2**63-1)


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _validate_source(idea: dict[str, Any]) -> dict[str, Any]:
    reference = idea.get('process_reference')
    if reference is None:
        raise pipeline.PipelineError('select an idea linked to an exact saved process')
    if idea['state'] not in {'approved', 'implemented'}:
        raise pipeline.PipelineError('approve the assessed idea before exporting its project')
    if not pipeline._process_status(reference)['reference_is_current']:
        raise pipeline.PipelineError('linked process changed or is unavailable; open its current revision and create a new idea')
    return reference


def export_project(idea_id: str, payload: Any) -> tuple[bytes, dict[str, Any]]:
    request = pipeline._validate(ExportRequest, payload)
    with pipeline._LOCK, closing(pipeline._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = pipeline._get(conn, idea_id)
        pipeline._check_revision(row, request.expected_revision)
        idea = pipeline._public(row)
        reference = _validate_source(idea)
        try:
            packet = pipeline._catalog().export(reference['workflow_id'], reference['revision'])
            if packet['content_sha256'] != reference['content_sha256']:
                raise ValueError('mismatch')
            base = build_runtime_project(packet)
        except Exception:
            raise pipeline.PipelineError('linked process project could not be generated; no export was recorded') from None
        # An edit/deletion while building must not be presented as a current export.
        _validate_source(idea)
        with ZipFile(BytesIO(base)) as archive:
            if 'opportunity.lineage.json' in archive.namelist():
                raise pipeline.PipelineError('generated project already has an opportunity manifest; no export was recorded')
            spec = json.loads(archive.read('workflow.spec.json'))
            if spec != build_runtime_spec(packet):
                raise pipeline.PipelineError('generated project does not match the linked design; no export was recorded')
            base_sha = sha256(base).hexdigest()
            manifest = {
                'format': 'openworkgraph.opportunity-project-link.v1',
                'idea_id': idea['idea_id'], 'idea_revision': idea['revision'],
                'process_reference': reference, 'spec_sha256': _digest(spec),
                'base_archive_sha256': base_sha,
                'execution_authorized': False, 'business_outcomes_verified': False,
                'independent_design_review_verified': False,
            }
            project_id = 'project_' + _digest(manifest)
            manifest['project_id'] = project_id
            output = BytesIO()
            with ZipFile(output, 'w', compression=ZIP_DEFLATED) as linked:
                for entry in archive.infolist():
                    linked.writestr(entry, archive.read(entry.filename))
                info = ZipInfo('opportunity.lineage.json', date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                linked.writestr(info, json.dumps(manifest, sort_keys=True, indent=2)+'\n')
        content = output.getvalue()
        existing = conn.execute('SELECT archive_sha256, created_at FROM idea_projects WHERE project_id=?', [project_id]).fetchone()
        archive_sha = sha256(content).hexdigest()
        if existing and existing[0] != archive_sha:
            raise pipeline.PipelineError('project artifact changed; no export was recorded')
        if not existing:
            count = conn.execute('SELECT count(*) FROM idea_projects WHERE idea_id=?', [idea_id]).fetchone()[0]
            if count >= MAX_PROJECTS_PER_IDEA:
                raise pipeline.PipelineError(f'an idea holds at most {MAX_PROJECTS_PER_IDEA} distinct project records')
            created_at = pipeline._now()
            conn.execute('INSERT INTO idea_projects VALUES (?,?,?,?,?,?,?,?)',
                         [project_id, idea_id, idea['revision'], created_at, json.dumps(reference),
                          manifest['spec_sha256'], archive_sha, base_sha])
        else:
            created_at = existing[1]
        return content, {**manifest, 'archive_sha256': archive_sha, 'created_at': created_at}


def projects(idea_id: str) -> dict[str, Any]:
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        idea = pipeline._public(pipeline._get(conn, idea_id))
        rows = conn.execute('SELECT project_id,idea_revision,created_at,process_reference,spec_sha256,archive_sha256,base_archive_sha256 '
                            'FROM idea_projects WHERE idea_id=? ORDER BY created_at DESC,project_id', [idea_id]).fetchall()
    status = pipeline._process_status(idea.get('process_reference'))
    return {'projects': [{'project_id': p, 'idea_revision': r, 'created_at': t,
                          'process_reference': json.loads(ref), 'spec_sha256': spec,
                          'archive_sha256': archive, 'base_archive_sha256': base,
                          'idea_revision_is_current': r == idea['revision']}
                         for p,r,t,ref,spec,archive,base in rows],
            'current_idea_revision': idea['revision'], 'process_status': status,
            'max_projects': MAX_PROJECTS_PER_IDEA, 'execution_authorized': False,
            'deployment_verified': False, 'business_outcomes_verified': False}
