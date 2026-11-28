"""Offline reconciliation of two declared reviewer shadow case suites."""
import argparse
import json
from hashlib import sha256
import os
from pathlib import Path
import re

FORMAT = 'openworkgraph.reviewed-shadow-cases.v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
CASE = re.compile(r'case_[0-9]{1,4}\Z')
LIMIT = 4 * 1024 * 1024
CATEGORIES = ('normal', 'exception', 'missing_facts')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def expectation(e):
    require(isinstance(e, dict) and set(e) == {'status', 'accepted_routes', 'review_confirmed'}
            and e['review_confirmed'] is True and e['status'] in ('proposal', 'needs_clarification')
            and isinstance(e['accepted_routes'], list) and len(e['accepted_routes']) <= 8
            and bool(e['accepted_routes']) == (e['status'] == 'proposal'), 'Invalid reviewed expectation')
    routes = []
    for route in e['accepted_routes']:
        require(isinstance(route, list) and 1 <= len(route) <= 80 and
                all(isinstance(a, str) and 1 <= len(a) <= 64 for a in route), 'Invalid route shape')
        routes.append(tuple(route))
    require(len(routes) == len(set(routes)), 'Duplicate accepted route')
    return {'status': e['status'], 'accepted_routes': [list(r) for r in sorted(routes)], 'review_confirmed': True}


def inventory(suite, pin):
    require(isinstance(pin, str) and HEX.fullmatch(pin) and digest(suite) == pin, 'Suite pin mismatch')
    require(isinstance(suite, dict) and set(suite) == {'format', 'process_reference', 'spec_sha256',
            'reviewed_by_customer', 'case_inputs_authorized', 'cases'} and suite['format'] == FORMAT
            and suite['reviewed_by_customer'] is True and suite['case_inputs_authorized'] is True
            and isinstance(suite['spec_sha256'], str) and HEX.fullmatch(suite['spec_sha256']), 'Invalid reviewed suite')
    ref = suite['process_reference']
    require(isinstance(ref, dict) and set(ref) == {'workflow_id', 'revision', 'content_sha256'}
            and isinstance(ref['workflow_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ref['workflow_id'])
            and type(ref['revision']) is int and ref['revision'] >= 1
            and isinstance(ref['content_sha256'], str) and HEX.fullmatch(ref['content_sha256']), 'Invalid process reference')
    require(isinstance(suite['cases'], list) and 1 <= len(suite['cases']) <= 50, 'Invalid case population')
    rows = {}
    for c in suite['cases']:
        require(isinstance(c, dict) and set(c) == {'id', 'category', 'input', 'expected'}
                and isinstance(c['id'], str) and CASE.fullmatch(c['id']) and c['id'] not in rows
                and c['category'] in CATEGORIES and isinstance(c['input'], dict)
                and len(json.dumps(c['input'], allow_nan=False).encode()) <= 65536, 'Invalid case inventory')
        rows[c['id']] = (c, expectation(c['expected']))
    return rows


def compare(a, a_pin, b, b_pin):
    left, right = inventory(a, a_pin), inventory(b, b_pin)
    require(a['process_reference'] == b['process_reference'] and a['spec_sha256'] == b['spec_sha256'], 'Different process specification')
    require(set(left) == set(right), 'Different case population')
    rows = []
    for cid in sorted(left):
        x, ex = left[cid]; y, ey = right[cid]
        require(x['category'] == y['category'] and digest(x['input']) == digest(y['input']), 'Different case input or category')
        routes_a = {tuple(r) for r in ex['accepted_routes']}
        routes_b = {tuple(r) for r in ey['accepted_routes']}
        state = ('status_disagreement' if ex['status'] != ey['status'] else
                 'route_set_disagreement' if routes_a != routes_b else 'agreement')
        rows.append({'case_id': cid, 'category': x['category'], 'status': state,
                     'reviewer_a_status': ex['status'], 'reviewer_b_status': ey['status'],
                     'a_route_count': len(routes_a), 'b_route_count': len(routes_b),
                     'shared_route_count': len(routes_a & routes_b)})
    disputed = [r['case_id'] for r in rows if r['status'] != 'agreement']
    body = {'format': 'openworkgraph.shadow-suite-disagreement.v1', 'reviewer_a_suite_sha256': a_pin,
            'reviewer_b_suite_sha256': b_pin, 'spec_sha256': a['spec_sha256'], 'case_count': len(rows),
            'agreement_count': len(rows)-len(disputed), 'disputed_case_ids': disputed, 'cases': rows,
            'case_values_returned': False, 'reviewer_independence_verified': False,
            'graph_routes_validated': False, 'ground_truth_verified': False, 'execution_authorized': False}
    return {**body, 'review_sha256': digest(body)}


def reconcile(a, a_pin, b, b_pin, expected_review, choices):
    report = compare(a, a_pin, b, b_pin)
    require(report['review_sha256'] == expected_review, 'Disagreement review pin changed')
    require(isinstance(choices, dict) and set(choices) == {'decisions'} and isinstance(choices['decisions'], dict)
            and set(choices['decisions']) == set(report['disputed_case_ids'])
            and all(v in ('a', 'b') for v in choices['decisions'].values()), 'Explicit choice of reviewer a or b required for every disagreement only')
    left, right = inventory(a, a_pin), inventory(b, b_pin)
    suite = {**a, 'cases': [{**left[cid][0], 'expected': (right if choices['decisions'].get(cid) == 'b' else left)[cid][1]}
                           for cid in sorted(left)]}
    receipt = {'format': 'openworkgraph.shadow-suite-adjudication.v1', 'review_sha256': expected_review,
               'decisions_sha256': digest(choices), 'output_suite_sha256': digest(suite),
               'agreement_count': report['agreement_count'], 'adjudicated_count': len(report['disputed_case_ids']),
               'case_values_returned': False, 'reviewer_independence_verified': False,
               'graph_routes_validated': False, 'ground_truth_verified': False, 'execution_authorized': False}
    return suite, {**receipt, 'receipt_sha256': digest(receipt)}


def load(path):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, 'Duplicate JSON key')
            result[k] = v
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON')
    with Path(path).open('rb') as f:
        data = f.read(LIMIT+1)
    require(0 < len(data) <= LIMIT, 'Input byte budget exceeded')
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def write(path, value):
    raw = json.dumps(value, indent=2, allow_nan=False).encode()
    require(len(raw) <= LIMIT, 'Output byte budget exceeded')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    for mode in ('compare', 'reconcile'):
        q = sub.add_parser(mode)
        for arg in ('reviewer-a', 'a-sha256', 'reviewer-b', 'b-sha256', 'output'):
            q.add_argument('--'+arg, required=True)
        q.add_argument('--confirm-local-review', action='store_true', required=True)
        if mode == 'reconcile':
            q.add_argument('--review-sha256', required=True)
            q.add_argument('--decisions', required=True)
    args = p.parse_args(argv)
    try:
        a, b = load(args.reviewer_a), load(args.reviewer_b)
        if args.mode == 'compare':
            report = compare(a, args.a_sha256, b, args.b_sha256)
            write(args.output, report)
            print(report['review_sha256'])
        else:
            suite, receipt = reconcile(a, args.a_sha256, b, args.b_sha256, args.review_sha256, load(args.decisions))
            root = Path(args.output); root.mkdir(mode=0o700, exist_ok=False)
            write(root/'reviewed-cases.json', suite)
            write(root/'receipt.json', receipt)
            print(receipt['receipt_sha256'])
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        p.exit(2, 'Review refused; check exact inputs and new output location. Discard incomplete outputs.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
