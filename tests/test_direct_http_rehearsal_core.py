import importlib.machinery
import importlib.util
import json
from copy import deepcopy
from itertools import product
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[1] / "shared/process_agent_templates/rehearse_http.py.txt"
loader = importlib.machinery.SourceFileLoader("rehearse_http_core", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
review_cases = module.review_cases

SPEC = {'revision': 1, 'content_sha256': 'a'*64}


def case(n, modes):
    return {'id': f'case_{n}', 'category': 'normal', 'input': {'secret': 'PRIVATE'},
            'expected': {'accepted_routes': [[m] for m in modes]}}


def prepare(spec, plan, data, bindings):
    mode, = plan['activity_ids']
    if mode == 'prepare':
        raise ValueError(data['secret'])
    return {'actions': [{'operation_kind': 'write', 'secret': data['secret'], 'mode': mode}],
            'human_activity_ids': ['h'], 'manual_condition_ids': [], 'source_rules': [],
            'outcomes': [], 'requirements': [data['secret']] if mode == 'require' else []}


def check(prepared, bindings):
    if prepared['actions'][0]['mode'] == 'wire':
        raise ValueError(prepared['actions'][0]['secret'])


def test_exhaustive_route_and_case_accounting():
    modes = ['ok', 'prepare', 'require', 'wire']
    for combination in product(modes, repeat=4):
        cases = [case(1, combination[:2]), case(2, combination[2:]), case(3, [])]
        original = deepcopy(cases)
        r = review_cases(SPEC, cases, {}, prepare=prepare, check_prepared=check)
        assert r['counts']['reviewed_routes'] == 4
        assert r['counts'].get('matching_routes', 0) == combination.count('ok')
        assert r['counts'].get('blocked_routes', 0) == 4 - combination.count('ok')
        assert r['counts']['clarification_cases'] == 1
        assert not r['all_cases_match_offline']
        assert 'PRIVATE' not in json.dumps(r)
        assert cases == original
        for i, mode in enumerate(combination):
            row = r['cases'][i//2]['routes'][i%2]
            assert row['reason'] == {'ok': None, 'prepare': 'offline_preparation_rejected',
                'require': 'offline_preparation_requirements', 'wire': 'direct_http_contract_rejected'}[mode]


def test_all_match_and_no_cases():
    r = review_cases(SPEC, [case(1, ['ok','ok'])], {}, prepare=prepare, check_prepared=check)
    assert r['all_cases_match_offline']
    assert r['cases'][0]['routes'][0]['counts']['human_activities'] == 1
    assert not review_cases(SPEC, [], {}, prepare=prepare, check_prepared=check)['all_cases_match_offline']


def test_requirements_stop_contract_check():
    def never(*args):
        pytest.fail('check must not be called for blocked preparation')
    r = review_cases(SPEC, [case(1, ['prepare','require'])], {}, prepare=prepare, check_prepared=never)
    assert r['counts']['blocked_routes'] == 2
