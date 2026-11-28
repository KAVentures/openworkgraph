"""Reconcile a declared cohort against original retained project source checks."""
from collections import Counter
from hashlib import sha256
import json
import re

FORMAT = 'openworkgraph.project-source-cohort-review.v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
CASE = re.compile(r'case_[A-Za-z0-9_-]{1,64}\Z')
RESOURCE = re.compile(r'owg:r:[a-f0-9]{32}\Z')
MAX_CASES = 25
MAX_DOCUMENT = 65536


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def basis(value, revision):
    require(isinstance(value, dict) and value.get('current_idea_revision') == revision
            and isinstance(value.get('spec_sha256'), str) and HEX.fullmatch(value['spec_sha256'])
            and isinstance(value.get('process_reference'), dict), 'Project or idea revision changed')
    expectations = value.get('expectations')
    require(isinstance(expectations, list), 'Project expectations unavailable')
    return (value['spec_sha256'], value['process_reference'],
            sorted({(x['terminal_activity_id'], x['operation']) for x in expectations}))


def review(idea_id, project_id, payload, *, options, reconcile):
    require(isinstance(payload, dict) and set(payload) == {'expected_idea_revision','cases','documents'},
            'Declare a complete case population and original source documents')
    revision = payload['expected_idea_revision']
    cases, documents = payload['cases'], payload['documents']
    require(type(revision) is int and revision >= 1 and isinstance(cases, list)
            and 1 <= len(cases) <= MAX_CASES and isinstance(documents, list)
            and len(documents) <= len(cases), 'Invalid cohort size or idea revision')
    snapshot = basis(options(idea_id, project_id), revision)
    spec_pin, process_ref, expectations = snapshot
    known, seen_cases, seen_targets, selected = {}, set(), set(), set()
    for document in documents:
        require(isinstance(document, str), 'Supply unchanged original UTF-8 documents')
        raw = document.encode('utf-8')
        require(0 < len(raw) <= MAX_DOCUMENT, 'Original document exceeds byte budget')
        pin = sha256(raw).hexdigest()
        require(pin not in known, 'Duplicate original document')
        known[pin] = raw
    for case in cases:
        require(isinstance(case, dict) and set(case) == {'case_id','terminal_activity_id',
                'expected_operation','resource_ref','document_sha256'}, 'Invalid case declaration')
        cid, terminal, op, resource, pin = (case[k] for k in ('case_id','terminal_activity_id',
                'expected_operation','resource_ref','document_sha256'))
        require(isinstance(cid,str) and CASE.fullmatch(cid) and cid not in seen_cases,
                'Declare unique opaque case IDs')
        seen_cases.add(cid)
        require(isinstance(terminal,str) and isinstance(op,str) and (terminal,op) in expectations,
                'Use a confirmed terminal expectation from this exact project')
        require(resource is None or isinstance(resource,str) and RESOURCE.fullmatch(resource),
                'Invalid observed record reference')
        if resource is not None:
            require((resource,op) not in seen_targets, 'Same source record and operation reused across cases')
            seen_targets.add((resource,op))
        if pin is not None:
            require(resource is not None and isinstance(pin,str) and HEX.fullmatch(pin)
                    and pin in known and pin not in selected, 'Missing or reused original document')
            selected.add(pin)
    require(selected == set(known), 'Unused original documents are not permitted')
    rows = []
    for case in cases:
        pin = case['document_sha256']
        row = {'case_id':case['case_id'],'document_sha256':pin}
        if pin is None:
            rows.append({**row,'status':'missing_document','historical_operation_reported_verified':None,
                         'local_reference_needs_review':None})
            continue
        raw = known[pin]
        # The existing local checker must establish retained exact bytes first.
        checked = reconcile(idea_id, project_id, raw)
        require(checked.get('format') == 'openworkgraph.project-source-check-review.v1'
                and checked.get('project_id') == project_id and checked.get('document_sha256') == pin
                and checked.get('exact_retained_export_bytes_match') is True
                and checked.get('current_idea_revision') == revision
                and checked.get('source_read_performed') is False
                and checked.get('execution_authorized') is False, 'Retained document review or idea revision changed')
        original = json.loads(raw)
        receipt = original['receipt']
        require(original['idea_id'] == idea_id and original['project_id'] == project_id
                and original['spec_sha256'] == spec_pin and original['process_reference'] == process_ref
                and original['terminal_activity_id'] == case['terminal_activity_id']
                and receipt['resource_ref'] == case['resource_ref']
                and receipt['expected_operation'] == case['expected_operation'],
                'Original document does not match declared case expectation')
        verified = checked.get('historical_source_operation_reported_verified')
        require(type(verified) is bool and verified is receipt['operation_verified'],
                'Historical operation status inconsistent')
        needs_review = (checked.get('idea_revision_matches_export') is not True
                or checked.get('process_reference_is_current') is not True
                or checked.get('process_expectation_status') != 'retained_expectation_matches'
                or checked.get('run_attribution_status') not in ('no_run_selected_at_export','retained_run_snapshot_matches'))
        status = ('historical_operation_reported_verified' if verified else 'historical_operation_not_established')
        rows.append({**row,'status':status,'historical_operation_reported_verified':verified,
                     'local_reference_needs_review':needs_review,
                     'run_attribution_status':checked['run_attribution_status']})
    require(basis(options(idea_id,project_id), revision) == snapshot, 'Project expectation basis changed during review')
    counts = Counter(r['status'] for r in rows)
    body = {'format':FORMAT,'idea_id':idea_id,'project_id':project_id,'idea_revision':revision,
            'spec_sha256':spec_pin,'declaration_sha256':digest(cases),'case_count':len(rows),
            'historical_operation_reported_verified_count':counts['historical_operation_reported_verified'],
            'historical_operation_not_established_count':counts['historical_operation_not_established'],
            'missing_document_count':counts['missing_document'],
            'local_reference_needs_review_count':sum(r['local_reference_needs_review'] is True for r in rows),
            'cases':rows,'source_read_performed':False,'review_persisted':False,
            'snapshot_is_atomic_across_stores':False,'case_association_verified':False,
            'current_source_state_verified':False,'business_outcomes_verified':False,'execution_authorized':False}
    return {**body,'review_sha256':digest(body)}
