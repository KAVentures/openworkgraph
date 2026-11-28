"""Join existing native shadow and source-state reviewers without grading outcomes."""
from . import native_shadow_roster as native
from . import project_source_cohort as source

FORMAT = 'openworkgraph.joint-shadow-source-review.v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def review(idea_id, project_id, payload, *, options, reconcile):
    require(isinstance(payload, dict) and set(payload) == {
        'plan', 'plan_sha256', 'trace', 'trace_sha256', 'assignments', 'source_cohort'},
        'Supply original frozen roster, trace, assignments and source cohort')
    plan, plan_pin = payload['plan'], payload['plan_sha256']
    planned = native.validate_plan(plan, plan_pin)
    cohort = payload['source_cohort']
    require(isinstance(cohort, dict) and set(cohort) == {
        'expected_idea_revision', 'cases', 'documents'} and isinstance(cohort['cases'], list)
        and 1 <= len(planned) == len(cohort['cases']) <= source.MAX_CASES,
        'Source declarations must cover the exact frozen roster (at most 25 cases)')
    ids = [row.get('case_id') if isinstance(row, dict) else None for row in cohort['cases']]
    require(all(isinstance(cid, str) for cid in ids) and len(set(ids)) == len(ids)
            and set(ids) == set(planned), 'Source case IDs differ from frozen roster')
    # The native reviewer verifies the original trace pin and complete assignment list.
    # The source reviewer reconciles original bytes with retained local export metadata.
    # Neither accepts an externally supplied review result.
    observed = native.review(plan, plan_pin, payload['trace'], payload['trace_sha256'], payload['assignments'])
    retained = source.review(idea_id, project_id, cohort, options=options, reconcile=reconcile)
    nr = {row['case_id']: row for row in observed['cases']}
    sr = {row['case_id']: row for row in retained['cases']}
    require(set(nr) == set(sr) == set(planned), 'Reviewer case inventories differ')
    rows = []
    for cid in planned:
        n, s = nr[cid], sr[cid]
        rows.append({'case_id': cid, 'native_observation_status': n['status'],
                     'historical_source_status': s['status'],
                     'historical_source_document_sha256': s['document_sha256'],
                     'source_reference_needs_review': s['local_reference_needs_review']})
    body = {'format': FORMAT, 'idea_id': idea_id, 'project_id': project_id,
            'idea_revision': retained['idea_revision'], 'spec_sha256': retained['spec_sha256'],
            'plan_sha256': plan_pin, 'package_sha256': plan['package_sha256'],
            'trace_sha256': observed['trace_sha256'], 'assignments_sha256': observed['assignments_sha256'],
            'source_declaration_sha256': retained['declaration_sha256'],
            'native_roster_review_sha256': observed['review_sha256'],
            'source_cohort_review_sha256': retained['review_sha256'],
            'case_count': len(rows), 'cases': rows, 'native_status_counts': observed['counts'],
            'historical_source_status_counts': {status: sum(r['historical_source_status'] == status for r in rows)
                for status in ('historical_operation_reported_verified', 'historical_operation_not_established', 'missing_document')},
            'source_reference_needs_review_count': retained['local_reference_needs_review_count'],
            'unassigned_returned_execution_count': len(observed['unassigned_returned_execution_ids']),
            'response_run_limit_reached': observed['response_run_limit_reached'],
            'exact_declared_case_ids_match': True, 'case_association_verified': False,
            'package_to_project_association_verified': False, 'prospective_enrollment_verified': False,
            'skill_invocation_verified': False, 'current_source_state_verified': False,
            'business_outcomes_verified': False, 'client_permissions_verified': False,
            'source_read_performed': False, 'review_persisted': False,
            'snapshot_is_atomic_across_stores': False, 'execution_authorized': False,
            'interpretation': 'Two separately reviewed evidence dimensions joined by customer-declared case ID. Neither clean returned events nor a historical source operation establishes a business outcome or that the agent caused it.'}
    return {**body, 'review_sha256': native.digest(body)}
