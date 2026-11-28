"""Optional known-record observation companion; no live provider refresh."""
import hashlib
import json
import re

REF = re.compile(r'owg:r:[a-f0-9]{32}\Z')
MAX_BYTES = 64 * 1024
OPERATIONS = {('github', 'pull_request'): 'github_pr_merged',
              ('jira', 'issue'): 'jira_issue_resolved',
              ('stripe', 'invoice'): 'stripe_invoice_paid',
              ('stripe', 'refund'): 'stripe_refund_processor_succeeded'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate(ref):
    if ref is None or ref == '':
        return None
    require(isinstance(ref, str) and REF.fullmatch(ref), 'Use an exact OWG-observed record reference')
    return ref


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def read(ref, get):
    ref = validate(ref)
    if ref is None:
        return None
    data = get('/v1/case-context', {'resource_ref': ref, 'include_source_status': False})
    require(isinstance(data, dict) and data.get('format') == 'openworkgraph.observed-case-context.v1'
            and data.get('record_ref') == ref and type(data.get('record_observed')) is bool
            and type(data.get('timeline_truncated')) is bool
            and type(data.get('record_identity_complete_within_retained_window')) is bool
            and data.get('does_not_query_open_work_inventory') is True
            and data.get('canonical_evidence_unchanged') is True, 'Unexpected case observation response')
    status = data.get('source_reported_status')
    require(isinstance(status, dict) and status.get('availability') == 'unknown'
            and status.get('state') == 'unknown' and 'verified_at' in status and status['verified_at'] is None
            and 'verified_operational_outcome' in status and status['verified_operational_outcome'] is None
            and status.get('end_customer_outcome_verified') is False, 'Unexpected provider verification claim')
    auth = data.get('authorization')
    require(isinstance(auth, dict) and auth.get('read_only') is True and all(auth.get(k) is False for k in (
        'may_contact_customer', 'may_send_invoice', 'may_issue_refund', 'agent_execution_authorized', 'observed_behavior_is_authority')),
        'Unexpected case authority claim')
    timeline = data.get('timeline')
    require(isinstance(timeline, list) and len(timeline) <= 100 and all(isinstance(e, dict) for e in timeline), 'Invalid case timeline')
    kind = data.get('record_type')
    if data['record_observed']:
        require(isinstance(kind, dict) and isinstance(kind.get('provider'), str) and isinstance(kind.get('kind'), str), 'Invalid observed record type')
    else:
        require(kind is None and not timeline, 'Unexpected observations for an unknown record')
    operation = None
    if data['record_observed'] and data['record_identity_complete_within_retained_window']:
        operation = OPERATIONS.get((kind['provider'], kind['kind']))
    raw = encoded(data)
    require(len(raw) <= MAX_BYTES, 'Case companion exceeds 64 KiB')
    result = {'format': 'openworkgraph.task-case-companion.v1', 'record_ref': ref,
              'ai_visible_api_sha256': hashlib.sha256(raw).hexdigest(), 'case_observations': json.loads(raw),
              'verification_navigation': None if operation is None else {'tool': 'verify_case_operation', 'expected_operation': operation},
              'selection_basis': 'explicit_caller_reference', 'task_record_relationship_verified': False,
              'provider_refresh_requested': False, 'source_operation_verified': False,
              'business_outcome_verified': False, 'atomic_snapshot_verified': False, 'execution_authorized': False,
              'interpretation': 'Separate retained case observations, not proof this task concerns the record or that work succeeded. No provider refresh requested. A verification candidate is navigation only; use the existing optional verification tool separately if enabled and appropriate with source-read permission. Unknown is not absence.'}
    require(len(encoded(result)) <= MAX_BYTES, 'Case companion exceeds 64 KiB')
    return result
