import json
from collections import Counter
from copy import deepcopy

import pytest

from shared import native_shadow_roster as roster
from shared import native_shadow_trace_review as trace_review


def package():
    m = {'format': roster.PACKAGE, 'mode': 'shadow_only',
         'packet_sha256': 'a' * 64, 'proposal_sha256': 'b' * 64, 'draft_sha256': 'c' * 64,
         'execution_authorized': False, 'deployment_verified': False,
         'client_permissions_verified': False, 'semantic_correctness_verified': False}
    return {**m, 'package_sha256': roster.digest(m)}


def cases(n=3):
    return [{'case_id': f'case_{i}', 'expected_framework': 'claude_code',
             'reviewed_tool_labels': ['Read']} for i in range(n)]


def plan(n=3):
    m = package()
    return roster.freeze(m, m['package_sha256'], cases(n), confirmed=True)


def run(i, tool='Read', status='success', *, finished=True, children=(), parent=None):
    rid = lambda n: f'execution:{n:016x}'
    ops = [('run_started', 'success', None), ('tool_call', status, tool)]
    if finished:
        ops.append(('run_finished', 'success', None))
    events = [{'event_ref': f'event:{i*100+j:016x}', 'operation': op, 'status': st,
               'tool': {'name': label} if label is not None else None}
              for j, (op, st, label) in enumerate(ops)]
    return {'execution_id': rid(i), 'parent_execution_id': rid(parent) if parent else None,
            'child_execution_ids': [rid(c) for c in children], 'events': events,
            'event_count_total': len(events), 'event_count_returned': len(events),
            'events_truncated': False, 'operation_counts': dict(Counter(e['operation'] for e in events)),
            'run_start_observed': True, 'run_finish_observed': finished,
            'complete_boundary_observed': finished, 'agent': {'framework': 'claude_code'}}


def snapshot(*runs):
    return {'source': 'canonical_agent_evidence', 'read_only': True, 'writes_performed': False,
            'evidence_is_canonical': True, 'authoritative': False, 'executions': list(runs),
            'returned': len(runs), 'agent_execution_count_considered': len(runs)}


def evaluate(p, s, assigned):
    return roster.review(p, p['plan_sha256'], s, trace_review.digest(s), assigned)


def test_frozen_denominator_and_exact_trace_reviewer():
    p = plan(5)
    assignments = roster.worklist(p, p['plan_sha256'])
    for i, rid in enumerate((1, 2, 3, 4)):
        assignments[i]['root_execution_id'] = f'execution:{rid:016x}'
    s = snapshot(run(1), run(2, 'Write'), run(3, finished=False), run(9))
    r = evaluate(p, s, assignments)
    assert r['counts'] == {'not_assigned': 1, 'root_not_returned': 1,
        'incomplete_observation': 1, 'observed_deviation_or_failure': 1,
        'no_deviation_in_returned_events': 1}
    assert sum(r['counts'].values()) == r['case_count'] == 5
    assert r['unassigned_returned_execution_ids'] == [run(9)['execution_id']]
    assert r['cases'][1]['trace_observation']['deviations'][0]['code'] == 'observed_tool_label_outside_reviewed_list'
    assert r['cases'][3]['trace_observation']['coverage_gaps'][0]['code'] == 'declared_run_not_returned'
    assert not r['business_outcomes_verified'] and not r['execution_authorized']
    assert evaluate(p, s, assignments[::-1]) == r


def test_all_missing_is_not_success():
    p = plan()
    a = roster.worklist(p, p['plan_sha256'])
    a[0]['root_execution_id'] = 'execution:0000000000000001'
    r = evaluate(p, snapshot(), a)
    assert r['counts']['not_assigned'] == 2 and r['counts']['root_not_returned'] == 1
    assert r['underlying_trace_review_sha256'] is None
    assert r['counts']['no_deviation_in_returned_events'] == 0


@pytest.mark.parametrize('bad', ['pin', 'mode', 'authority', 'duplicates', 'bad_label', 'confirmation'])
def test_freeze_refuses(bad):
    m, c = package(), cases()
    if bad == 'pin': m['packet_sha256'] = '0' * 64
    elif bad == 'mode': m['mode'] = 'production'
    elif bad == 'authority': m['execution_authorized'] = True
    elif bad == 'duplicates': c.append(c[0])
    elif bad == 'bad_label': c[0]['reviewed_tool_labels'] = ['Read', 'Read']
    if bad in ('mode', 'authority'):
        m['package_sha256'] = roster.digest({k: v for k, v in m.items() if k != 'package_sha256'})
    with pytest.raises(ValueError):
        roster.freeze(m, m['package_sha256'], c, confirmed=bad != 'confirmation')


@pytest.mark.parametrize('bad', ['missing', 'extra', 'unknown', 'duplicate', 'root_reuse', 'child_reuse', 'tamper', 'trace_pin', 'empty_forged'])
def test_review_refuses(bad):
    p = plan()
    a = roster.worklist(p, p['plan_sha256'])
    s = snapshot(run(1, children=[2]), run(2, parent=1))
    tpin = trace_review.digest(s)
    if bad == 'missing': a.pop()
    elif bad == 'extra': a.append({'case_id': 'case_5', 'root_execution_id': None})
    elif bad == 'unknown': a[0]['case_id'] = 'case_9'
    elif bad == 'duplicate': a[1] = a[0]
    elif bad == 'root_reuse': a[0]['root_execution_id'] = a[1]['root_execution_id'] = run(1)['execution_id']
    elif bad == 'child_reuse':
        a[0]['root_execution_id'] = run(1)['execution_id']
        a[1]['root_execution_id'] = run(2)['execution_id']
    elif bad == 'tamper': p['cases'][0]['reviewed_tool_labels'] = ['Write']
    elif bad == 'trace_pin': tpin = '0' * 64
    elif bad == 'empty_forged':
        s = snapshot(); s['authoritative'] = True; tpin = trace_review.digest(s)
    with pytest.raises(ValueError):
        roster.review(p, p['plan_sha256'], s, tpin, a)


def test_cli_outputs_are_exclusive_and_canonical(tmp_path):
    m, c = package(), cases()
    manifest, cases_file, out = [tmp_path / name for name in ('manifest.json','cases.json','plan.json')]
    manifest.write_text(json.dumps(m)); cases_file.write_text(json.dumps(c))
    argv = ['freeze','--manifest',str(manifest),'--package-sha256',m['package_sha256'],
            '--cases',str(cases_file),'--output',str(out),'--confirm-local']
    assert roster.main(argv) == 0
    assert json.loads(out.read_text()) == plan()
    with pytest.raises(SystemExit) as exc: roster.main(argv)
    assert exc.value.code == 2
