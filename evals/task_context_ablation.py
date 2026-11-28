"""Offline paired agent-evaluation manifest and conservative reviewed scoring."""
import hashlib
import json
import re
from collections import Counter

ARMS = ('control', 'observed', 'augmented')
PIN = re.compile(r'[a-f0-9]{64}\Z')
ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def pin(value):
    return isinstance(value, str) and bool(PIN.fullmatch(value))


def plan(cases, settings, replicates=2):
    """Freeze independently selected cases and exact per-arm request fingerprints."""
    require(isinstance(cases, list) and 1 <= len(cases) <= 200, 'Invalid case count')
    require(type(replicates) is int and 2 <= replicates <= 5, 'Use two to five replicates')
    require(isinstance(settings, dict) and set(settings) == {'model_id', 'settings_sha256'}
            and isinstance(settings['model_id'], str) and 0 < len(settings['model_id']) <= 200
            and pin(settings['settings_sha256']), 'Exact shared model settings required')
    rows, seen = [], set()
    for case in cases:
        require(isinstance(case, dict) and set(case) == {'case_id', 'fold', 'source_state_sha256', 'task_sha256', 'request_sha256_by_arm'}, 'Exact case specification required')
        cid = case['case_id']
        require(isinstance(cid, str) and ID.fullmatch(cid) and cid not in seen and case['fold'] in ('development', 'holdout')
                and pin(case['source_state_sha256']) and pin(case['task_sha256']), 'Invalid or duplicate case')
        seen.add(cid)
        requests = case['request_sha256_by_arm']
        require(isinstance(requests, dict) and set(requests) == set(ARMS) and all(pin(v) for v in requests.values())
                and len(set(requests.values())) == 3, 'Distinct frozen arm requests required')
        rows.append(json.loads(json.dumps(case)))
    slots = []
    for i, case in enumerate(rows):
        for rep in range(1, replicates + 1):
            shift = (i + rep - 1) % 3
            for order, arm in enumerate(ARMS[shift:] + ARMS[:shift], 1):
                slot = {'case_id':case['case_id'], 'replicate':rep, 'arm':arm, 'order':order,
                        'request_sha256':case['request_sha256_by_arm'][arm], 'source_state_sha256':case['source_state_sha256']}
                slots.append({**slot, 'slot_id':digest(slot)})
    body = {'format':'openworkgraph.task-context-ablation-plan.v1','cases':rows,'settings':dict(settings),
            'replicates':replicates,'slots':slots,'model_invoked':False,'execution_authorized':False}
    return {**body,'plan_sha256':digest(body)}


def score(frozen, expected_pin, runs):
    require(isinstance(frozen, dict) and frozen == plan(frozen['cases'], frozen['settings'], frozen['replicates'])
            and frozen['plan_sha256'] == expected_pin, 'Plan changed or wrong independent pin')
    slots = {s['slot_id']:s for s in frozen['slots']}
    cases = {c['case_id']:c for c in frozen['cases']}
    require(isinstance(runs, list) and len(runs) <= len(slots), 'Invalid attempt inventory')
    by_slot = {}
    fields = {'slot_id','model_id','settings_sha256','request_sha256','source_state_sha256','status','output_sha256','review'}
    for r in runs:
        require(isinstance(r, dict) and set(r) == fields and isinstance(r['slot_id'], str)
                and r['slot_id'] in slots and r['slot_id'] not in by_slot, 'Unexpected or duplicate attempt')
        s = slots[r['slot_id']]
        require(r['model_id'] == frozen['settings']['model_id'] and r['settings_sha256'] == frozen['settings']['settings_sha256']
                and all(r[k] == s[k] for k in ('request_sha256','source_state_sha256')), 'Attempt lineage mismatch')
        require(r['status'] in ('completed','failed','timeout'), 'Invalid attempt status')
        review = r['review']
        if r['status'] == 'completed':
            require(pin(r['output_sha256']), 'Completed attempt requires output digest')
            require(isinstance(review, dict) and set(review) == {'reviewer_id','output_sha256','task_success','consequential_wrong_action','wrong_assumptions'}
                    and isinstance(review['reviewer_id'], str) and ID.fullmatch(review['reviewer_id'])
                    and review['output_sha256'] == r['output_sha256'] and type(review['task_success']) is bool
                    and type(review['consequential_wrong_action']) is bool and type(review['wrong_assumptions']) is int
                    and review['wrong_assumptions'] >= 0, 'Completed attempt requires output-bound independent review')
        else:
            require(r['output_sha256'] is None and review is None, 'Failed attempts must remain ungraded')
        by_slot[r['slot_id']] = r
    report = {}
    for fold in ('development','holdout'):
        members = [s for s in slots.values() if cases[s['case_id']]['fold'] == fold]
        arm_counts = {}
        for arm in ARMS:
            counts = Counter(planned=0, missing=0, failed=0, timeout=0, completed=0, reviewed_success=0,
                             consequential_wrong_action=0, wrong_assumptions=0)
            for s in members:
                if s['arm'] != arm:
                    continue
                counts['planned'] += 1
                r = by_slot.get(s['slot_id'])
                if r is None:
                    counts['missing'] += 1
                else:
                    counts[r['status']] += 1
                    if r['review'] is not None:
                        q = r['review']; counts['reviewed_success'] += int(q['task_success'] and not q['consequential_wrong_action'])
                        counts['consequential_wrong_action'] += int(q['consequential_wrong_action'])
                        counts['wrong_assumptions'] += q['wrong_assumptions']
            arm_counts[arm] = dict(counts)
        # Denominators are planned attempts, not only completed responses.
        report[fold] = arm_counts
    return {'format':'openworkgraph.task-context-ablation-report.v1','plan_sha256':expected_pin,
            'planned_attempts':len(slots),'recorded_attempts':len(by_slot),'all_attempts_recorded':len(by_slot)==len(slots),
            'by_fold':report,'model_invoked_by_scorer':False,'reviewer_independence_verified':False,
            'transport_attested':False,'superiority_claimed':False,'execution_authorized':False,
            'interpretation':'Counts of submitted output-bound reviews, not verified model runs. Retain all failures and missing attempts; holdout separate. No causal or superiority inference.'}
