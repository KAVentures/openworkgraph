"""Plan an exact, group-disjoint split of a local reviewed shadow case suite.

This is pre-freeze dataset preparation, not model evaluation or a proof of
independence. Run the existing exported runtime's suite validation afterwards.
"""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re

CATEGORIES = ('normal', 'exception', 'missing_facts')
FORMAT = 'openworkgraph.reviewed-shadow-cases.v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
CASE = re.compile(r'case_[0-9]{1,4}\Z')
GROUP = re.compile(r'group_[a-zA-Z0-9_-]{1,64}\Z')
MAX_BYTES = 4 * 1024 * 1024


def digest(value):
    # Match the existing generated runtime's canonical JSON digest.
    return sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(suite, expected, groups, policy):
    require(isinstance(expected, str) and HEX.fullmatch(expected) and digest(suite) == expected,
            'Original suite digest mismatch')
    require(isinstance(suite, dict) and suite.get('format') == FORMAT and
            suite.get('reviewed_by_customer') is True and suite.get('case_inputs_authorized') is True and
            isinstance(suite.get('process_reference'), dict) and isinstance(suite.get('spec_sha256'), str) and
            HEX.fullmatch(suite['spec_sha256']), 'Reviewed authorized shadow suite required')
    cases = suite.get('cases')
    require(isinstance(cases, list) and 2 <= len(cases) <= 50, 'Select 2 to 50 reviewed cases')
    ids, inputs = set(), set()
    for c in cases:
        require(isinstance(c, dict) and set(c) == {'id', 'category', 'input', 'expected'} and
                isinstance(c['id'], str) and CASE.fullmatch(c['id']) and c['id'] not in ids and
                c['category'] in CATEGORIES and isinstance(c['input'], dict) and
                len(json.dumps(c['input'], allow_nan=False).encode()) <= 65536, 'Invalid case inventory')
        ids.add(c['id'])
        pin = digest(c['input'])
        require(pin not in inputs, 'Duplicate exact case inputs; resolve duplicates before splitting')
        inputs.add(pin)
        e = c['expected']
        require(isinstance(e, dict) and set(e) == {'status', 'accepted_routes', 'review_confirmed'} and
                e['review_confirmed'] is True and e['status'] in ('proposal', 'needs_clarification') and
                isinstance(e['accepted_routes'], list) and len(e['accepted_routes']) <= 8 and
                bool(e['accepted_routes']) == (e['status'] == 'proposal'), 'Invalid reviewed expectation')
        for path in e['accepted_routes']:
            require(isinstance(path, list) and 1 <= len(path) <= 80 and
                    all(isinstance(a, str) and 1 <= len(a) <= 64 for a in path), 'Invalid reviewed route shape')
    require(isinstance(groups, dict) and set(groups) == {'case_groups'} and
            isinstance(groups['case_groups'], dict) and set(groups['case_groups']) == ids and
            all(isinstance(g, str) and GROUP.fullmatch(g) for g in groups['case_groups'].values()),
            'Declare one opaque group for every case, with no extras')
    require(isinstance(policy, dict) and set(policy) == {'target_holdout_cases', 'min_development', 'min_holdout'} and
            type(policy['target_holdout_cases']) is int and 1 <= policy['target_holdout_cases'] < len(cases),
            'Declare a valid holdout size target')
    for key in ('min_development', 'min_holdout'):
        p = policy[key]
        require(isinstance(p, dict) and set(p) == set(CATEGORIES) and
                all(type(v) is int and 0 <= v <= 50 for v in p.values()), 'Declare every category minimum')
    return cases


def choose(vectors, total, target, min_dev, min_test):
    """Exact reachable category count vectors; one deterministic witness each."""
    states = {(0, 0, 0): ()}
    for group, vector in sorted(vectors.items()):
        next_states = dict(states)
        for counts, chosen in states.items():
            new = tuple(a + b for a, b in zip(counts, vector))
            witness = chosen + (group,)
            if new not in next_states or witness < next_states[new]:
                next_states[new] = witness
        states = next_states
    n = sum(total)
    feasible = [(v, g) for v, g in states.items() if 0 < sum(v) < n and
                all(lo <= x <= t-hi for x, t, lo, hi in zip(v, total, min_test, min_dev))]
    def key(pair):
        v, groups = pair
        return (abs(sum(v)-target), sum(abs(x*n-t*sum(v)) for x,t in zip(v,total)), groups)
    return min(feasible, key=key) if feasible else None, len(states), sorted({sum(v) for v, _ in feasible})


def plan(suite, expected, groups, policy):
    cases = validate(suite, expected, groups, policy)
    by_group = defaultdict(Counter)
    for c in cases:
        by_group[groups['case_groups'][c['id']]][c['category']] += 1
    vectors = {g: tuple(counts[k] for k in CATEGORIES) for g, counts in by_group.items()}
    total = tuple(sum(v[i] for v in vectors.values()) for i in range(3))
    answer, visited, sizes = choose(vectors, total, policy['target_holdout_cases'],
        tuple(policy['min_development'][k] for k in CATEGORIES), tuple(policy['min_holdout'][k] for k in CATEGORIES))
    report = {'format': 'openworkgraph.grouped-shadow-split-review.v1', 'original_suite_sha256': expected,
              'group_declaration_sha256': digest(groups), 'policy_sha256': digest(policy),
              'case_count': len(cases), 'group_count': len(vectors), 'reachable_category_vectors': visited,
              'feasible_holdout_sizes': sizes, 'status': 'feasible' if answer else 'infeasible',
              'case_values_returned': False, 'graph_routes_validated': False, 'independence_verified': False,
              'semantic_overlap_checked': False, 'execution_authorized': False,
              'interpretation': 'Exact split over declared groups and reviewed case categories only. Group labels are customer declarations, not verified business identities. Validate both resulting suites in the exported runtime before freezing; no independence or model performance claim.'}
    development = holdout = None
    if answer:
        counts, selected = answer
        selected = set(selected)
        test = [c for c in cases if groups['case_groups'][c['id']] in selected]
        dev = [c for c in cases if groups['case_groups'][c['id']] not in selected]
        development, holdout = {**suite, 'cases': dev}, {**suite, 'cases': test}
        report.update(development_sha256=digest(development), holdout_sha256=digest(holdout),
                      development_case_ids=[c['id'] for c in dev], holdout_case_ids=[c['id'] for c in test],
                      development_counts=dict(zip(CATEGORIES, (a-b for a,b in zip(total,counts)))),
                      holdout_counts=dict(zip(CATEGORIES, counts)))
    return development, holdout, {**report, 'review_sha256': digest(report)}


def load(path):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d, 'Duplicate JSON key')
            d[k] = v
        return d
    def constant(_):
        raise ValueError('Nonfinite JSON')
    with Path(path).open('rb') as f:
        data = f.read(MAX_BYTES+1)
    require(0 < len(data) <= MAX_BYTES, 'Input byte budget exceeded')
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('suite', 'suite-sha256', 'groups', 'policy', 'output-directory'):
        p.add_argument('--'+arg, required=True)
    p.add_argument('--confirm-local-case-split', action='store_true', required=True)
    a = p.parse_args(argv)
    try:
        dev, test, report = plan(load(a.suite), a.suite_sha256, load(a.groups), load(a.policy))
        root = Path(a.output_directory)
        root.mkdir(mode=0o700, exist_ok=False)
        # Private case files first; the report is the completion marker.
        for name, value in [('development.json', dev), ('holdout.json', test), ('review.json', report)]:
            if value is not None:
                fd = os.open(root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(value, f, indent=2, allow_nan=False)
        print(json.dumps({'status': report['status'], 'review_sha256': report['review_sha256']}))
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        p.exit(2, 'Split refused; check inputs and choose a new output directory. Discard any incomplete output.\n')
    return 0 if dev is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
