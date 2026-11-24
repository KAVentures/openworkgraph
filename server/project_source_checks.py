"""Explicit source-state check downloads with digest-only local provenance.

Source access remains in the existing receipt adapter. Neither receipt bodies,
observed record references, operations nor verification values are persisted.
A source's operational state never proves agent causality or run/case identity.
"""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt

from shared.process_model import ProcessModel
from . import automation_pipeline as pipeline
from . import automation_monitoring as monitoring
from . import project_report_links as reports
from .open_work_outcomes import source_operation_receipt

MAX_EXPORTS_PER_PROJECT = 50
Operation = Literal['github_pr_merged','stripe_invoice_paid','stripe_refund_processor_succeeded','jira_issue_resolved']
FALSE_CLAIMS = {'agent_caused_operation_verified':False,'business_objective_verified':False,
                'reported_run_case_identity_verified':False,'artifact_execution_verified':False,
                'execution_authorized':False}
_RECEIPT_FIELDS = ('format','resource_ref','expected_operation','availability','reason','provider',
                   'provider_record_kind','provider_reported_state','verified_at','operation_verified',
                   'verification_scope','business_objective_verified','customer_outcome_verified',
                   'agent_caused_operation_verified','agent_execution_authorized',
                   'no_customer_identity_or_native_record_id_included','read_only')


class ConsentRequired(pipeline.PipelineError):
    pass


class RunReference(pipeline._Strict):
    deployment_id: str = Field(pattern=r'^dep_[0-9a-f]{16}$')
    run_id: str = Field(pattern=r'^[\x20-\x7e]{1,128}$')


class CheckRequest(pipeline._Strict):
    expected_revision: StrictInt = Field(ge=1, le=2**63-1)
    run_reference: RunReference | None = None
    terminal_activity_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    expected_operation: Operation
    resource_ref: str = Field(pattern=r'^owg:r:[0-9a-f]{32}$')
    source_read_and_export_confirmed: StrictBool = False


def _context(conn, idea_id, project_id, expected_revision=None):
    row = pipeline._get(conn,idea_id)
    if expected_revision is not None:
        pipeline._check_revision(row,expected_revision)
    project = conn.execute('SELECT process_reference,spec_sha256 FROM idea_projects WHERE idea_id=? AND project_id=?',
                           [idea_id,project_id]).fetchone()
    if project is None:
        raise pipeline.PipelineError('select a project recorded for this idea')
    return pipeline._public(row),json.loads(project[0]),project[1]


def _model(reference):
    try:
        record=pipeline._catalog().get(reference['workflow_id'],reference['revision'])
        if record['content_sha256']!=reference['content_sha256']:
            raise ValueError('mismatch')
        return ProcessModel.model_validate(record['process_model'])
    except Exception:
        raise pipeline.PipelineError('linked process changed or is unavailable; no source check can be linked') from None


def _expectation(model, request):
    if not any(e.terminal_activity_id==request.terminal_activity_id and e.operation==request.expected_operation
               and e.user_confirmed for e in model.outcome_expectations):
        raise pipeline.PipelineError('select a confirmed source-state expectation from this exact saved process')


def _linked_deployments(conn, project_id, spec):
    """Deployments explicitly linked to this project whose reports still match its spec."""
    linked = [row[0] for row in conn.execute('SELECT deployment_id FROM project_report_links WHERE project_id=? '
                                             'ORDER BY deployment_id', [project_id]).fetchall()]
    if not linked:
        return []
    sources, available = reports._sources()
    return [d for d in linked if reports._status(spec, sources.get(d), available) == 'matching_reported_specification']


def _run(conn, project_id, spec, reference):
    """The selected run, only from a matching linked deployment and with its own exact spec hash."""
    if reference is None:
        return None
    if reference.deployment_id not in _linked_deployments(conn, project_id, spec):
        raise pipeline.PipelineError('select a run from a linked deployment whose reports match this project')
    try:
        run = monitoring.attributed_run(reference.deployment_id, reference.run_id, spec)
    except Exception:
        run = None
    if run is None:
        raise pipeline.PipelineError('that run did not report this project\'s exact specification, or is unavailable')
    return dict(run, attribution='run_report_declared_exact_project_specification')


def options(idea_id: str, project_id: str) -> dict[str, Any]:
    with pipeline._LOCK,closing(pipeline._connect()) as conn:
        idea,reference,spec=_context(conn,idea_id,project_id)
        deployments=_linked_deployments(conn,project_id,spec)
    model=_model(reference)
    try:
        runs=monitoring.attributed_runs(deployments,spec)
    except Exception:
        runs=[]
    labels={a.id:a.label for a in model.activities}
    return {'process_reference':reference,'spec_sha256':spec,'current_idea_revision':idea['revision'],
            'expectations':[{'terminal_activity_id':e.terminal_activity_id,'operation':e.operation,
                             'activity_label':labels[e.terminal_activity_id]}
                            for e in model.outcome_expectations if e.user_confirmed],
            'attributable_runs':runs,'attributable_runs_limit':50,
            'source_read_performed':False,**FALSE_CLAIMS}


def _read_receipt(resource_ref, operation):
    return source_operation_receipt(resource_ref,operation)


def export_check(idea_id: str, project_id: str, payload: Any) -> tuple[bytes,dict[str,Any]]:
    request=pipeline._validate(CheckRequest,payload)
    if not request.source_read_and_export_confirmed:
        raise ConsentRequired('confirm this exact source read and receipt download')
    with pipeline._LOCK,closing(pipeline._connect()) as conn:
        idea,reference,spec=_context(conn,idea_id,project_id,request.expected_revision)
        if conn.execute('SELECT count(*) FROM project_source_exports WHERE project_id=?',[project_id]).fetchone()[0]>=MAX_EXPORTS_PER_PROJECT:
            raise pipeline.PipelineError(f'at most {MAX_EXPORTS_PER_PROJECT} source-check exports per project')
        run=_run(conn,project_id,spec,request.run_reference)
    _expectation(_model(reference),request)
    # Never hold the idea database/write lock across a provider read. The adapter
    # independently enforces retained observation, opt-ins, identity and freshness.
    try:
        raw=_read_receipt(request.resource_ref,request.expected_operation)
        if (not isinstance(raw,dict) or raw.get('format')!='openworkgraph.source-operation-receipt.v1'
                or raw.get('resource_ref')!=request.resource_ref or raw.get('expected_operation')!=request.expected_operation
                or type(raw.get('operation_verified')) is not bool or raw.get('read_only') is not True):
            raise ValueError('invalid receipt')
        receipt={key:raw[key] for key in _RECEIPT_FIELDS if key in raw}
        receipt.update(business_objective_verified=False,customer_outcome_verified=False,
                       agent_caused_operation_verified=False,agent_execution_authorized=False)
        now=datetime.now(timezone.utc).isoformat()
        document={'format':'openworkgraph.project-source-check-export.v1','project_id':project_id,
                  'idea_id':idea_id,'idea_revision_at_check':idea['revision'],'process_reference':reference,
                  'spec_sha256':spec,'terminal_activity_id':request.terminal_activity_id,'checked_at':now,
                  'association_basis':('user_selected_project_run_expectation_and_exact_observed_record' if run
                                       else 'user_selected_project_expectation_and_exact_observed_record'),
                  **({'run_reference':run} if run else {}),
                  'receipt':receipt,'receipt_body_persisted':False,**FALSE_CLAIMS}
        content=(json.dumps(document,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    except Exception:
        raise pipeline.PipelineError('source receipt could not be checked; no document was recorded') from None
    # Revalidate after the external read; stale/deleted ideas or definitions must
    # not produce a newly linked export. This is not a cross-store transaction.
    _expectation(_model(reference),request)
    digest=sha256(content).hexdigest();export_id='source_export_'+digest
    with pipeline._LOCK,closing(pipeline._connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        _,current_ref,current_spec=_context(conn,idea_id,project_id,request.expected_revision)
        if current_ref!=reference or current_spec!=spec:
            raise pipeline.PipelineError('project reference changed during the source check')
        if run is not None and _run(conn,project_id,spec,request.run_reference)!=run:
            raise pipeline.PipelineError('the selected run changed during the source check')
        existing=conn.execute('SELECT 1 FROM project_source_exports WHERE export_id=?',[export_id]).fetchone()
        if not existing:
            if conn.execute('SELECT count(*) FROM project_source_exports WHERE project_id=?',[project_id]).fetchone()[0]>=MAX_EXPORTS_PER_PROJECT:
                raise pipeline.PipelineError(f'at most {MAX_EXPORTS_PER_PROJECT} source-check exports per project')
            conn.execute('INSERT INTO project_source_exports VALUES (?,?,?,?,?)',
                         [export_id,project_id,idea['revision'],now,digest])
    return content,{'export_id':export_id,'project_id':project_id,'document_sha256':digest,
                    'created_at':now,'receipt_body_persisted':False,**FALSE_CLAIMS}


def exports(idea_id: str,project_id: str) -> dict[str,Any]:
    with pipeline._LOCK,closing(pipeline._connect()) as conn:
        _context(conn,idea_id,project_id)
        rows=conn.execute('SELECT export_id,idea_revision,created_at,document_sha256 FROM project_source_exports '
                          'WHERE project_id=? ORDER BY created_at DESC,export_id',[project_id]).fetchall()
    return {'exports':[dict(zip(('export_id','idea_revision','created_at','document_sha256'),row)) for row in rows],
            'max_exports':MAX_EXPORTS_PER_PROJECT,'receipt_body_persisted':False,'source_read_performed':False,**FALSE_CLAIMS}


MAX_REVIEW_BYTES = 64 * 1024


def review_export(idea_id: str, project_id: str, content: bytes) -> dict[str, Any]:
    """Reconcile an explicitly supplied original download against retained metadata.

    Compare exact bytes before interpreting receipt data. No source calls or
    persistence of supplied documents; report historical state separately
    from the current local run association.
    """
    if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_REVIEW_BYTES:
        raise pipeline.PipelineError('select an original source-check document of at most 64 KiB')
    digest = sha256(content).hexdigest()
    export_id = 'source_export_' + digest
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        idea, reference, spec = _context(conn, idea_id, project_id)
        metadata = conn.execute(
            'SELECT idea_revision,created_at,document_sha256 FROM project_source_exports '
            'WHERE project_id=? AND export_id=?', [project_id, export_id]).fetchone()
    if metadata is None or metadata[2] != digest:
        raise pipeline.PipelineError('document does not match a retained export for this project')
    try:
        document = json.loads(content)
        if (not isinstance(document, dict)
                or document.get('format') != 'openworkgraph.project-source-check-export.v1'
                or document.get('idea_id') != idea_id or document.get('project_id') != project_id
                or document.get('idea_revision_at_check') != metadata[0]
                or document.get('checked_at') != metadata[1]
                or document.get('process_reference') != reference or document.get('spec_sha256') != spec
                or document.get('receipt_body_persisted') is not False
                or any(document.get(k) is not False for k in FALSE_CLAIMS)):
            raise ValueError('invalid document')
        receipt = document['receipt']
        if (not isinstance(receipt, dict)
                or receipt.get('format') != 'openworkgraph.source-operation-receipt.v1'
                or type(receipt.get('operation_verified')) is not bool
                or receipt.get('read_only') is not True
                or any(receipt.get(k) is not False for k in (
                    'business_objective_verified', 'customer_outcome_verified',
                    'agent_caused_operation_verified', 'agent_execution_authorized'))):
            raise ValueError('invalid receipt')
        run = document.get('run_reference')
        run_reference = (RunReference.model_validate({
            'deployment_id': run['deployment_id'], 'run_id': run['run_id']}) if run is not None else None)
        expected = CheckRequest.model_validate({
            'expected_revision': metadata[0], 'terminal_activity_id': document['terminal_activity_id'],
            'expected_operation': receipt['expected_operation'], 'resource_ref': receipt['resource_ref']})
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise pipeline.PipelineError('retained document has invalid source-check structure') from None
    try:
        _expectation(_model(reference), expected)
        expectation_status = 'retained_expectation_matches'
    except pipeline.PipelineError:
        expectation_status = 'retained_expectation_unavailable_or_changed'
    # Only compare current locally retained monitoring metadata. A matching
    # declared run still does not independently identify the observed record.
    with pipeline._LOCK, closing(pipeline._connect()) as conn:
        current_idea, current_reference, current_spec = _context(conn, idea_id, project_id)
        current_metadata = conn.execute(
            'SELECT idea_revision,created_at,document_sha256 FROM project_source_exports '
            'WHERE project_id=? AND export_id=?', [project_id, export_id]).fetchone()
        if current_metadata != metadata or current_reference != reference or current_spec != spec:
            raise pipeline.PipelineError('retained export reference changed during review')
        if run_reference is None:
            run_status = 'no_run_selected_at_export'
        else:
            try:
                current_run = _run(conn, project_id, spec, run_reference)
            except pipeline.PipelineError:
                run_status = 'run_attribution_unavailable_or_changed'
            else:
                run_status = ('retained_run_snapshot_matches' if current_run == run
                              else 'retained_run_snapshot_changed')
    process_status = pipeline._process_status(reference)
    return {
        'format': 'openworkgraph.project-source-check-review.v1',
        'project_id': project_id, 'export_id': export_id, 'document_sha256': digest,
        'exported_at': metadata[1], 'idea_revision_at_export': metadata[0],
        'current_idea_revision': current_idea['revision'],
        'idea_revision_matches_export': current_idea['revision'] == metadata[0],
        'exact_retained_export_bytes_match': True,
        'process_expectation_status': expectation_status, 'run_attribution_status': run_status,
        'current_process_reference_status': process_status['status'],
        'process_reference_is_current': process_status['reference_is_current'],
        'latest_process_revision': process_status['latest_revision'],
        'historical_source_operation_reported_verified': receipt['operation_verified'],
        'historical_source_availability': receipt.get('availability'),
        'historical_source_checked_at': receipt.get('verified_at'),
        'source_read_performed': False, 'current_source_state_verified': False,
        'receipt_body_persisted': False, 'receipt_body_returned': False,
        'review_persisted': False, **FALSE_CLAIMS,
    }
