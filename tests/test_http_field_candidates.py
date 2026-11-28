import importlib.machinery
import importlib.util
from pathlib import Path
from random import Random
import json
import pytest

path = Path(__file__).resolve().parents[1] / 'shared/process_agent_templates/suggest_http_fields.py.txt'
loader = importlib.machinery.SourceFileLoader('suggest_http_fields', str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


def case(n, data, routes):
    return {'id': f'case_{n}', 'input': data, 'expected': {'accepted_routes': routes}}


OPS = {'read': {'fields': {'id': 'integer', 'tag': 'string'}}}


def compatible(op, arg, value):
    return type(value) is (int if arg == 'id' else str)


def review(cases):
    return m.candidates(cases, {'a': 'read'}, OPS, role=lambda op, arg: 'path', compatible=compatible)[0]


def test_random_distinct_case_support_oracle():
    rng = Random(17)
    for _ in range(600):
        cases = []
        for i in range(rng.randrange(1, 10)):
            data = {k: rng.choice([1, 2, 'PRIVATE', True]) for k in ('x', 'y', 'z') if rng.randrange(3)}
            routes = rng.choice([[['a'], ['a', 'a']], [['b']], []])
            cases.append(case(i, data, routes))
        relevant = [c['input'] for c in cases if any('a' in route for route in c['expected']['accepted_routes'])]
        row = review(cases)
        assert row['relevant_cases'] == len(relevant)
        for arg in row['arguments']:
            expected = {k: sum(k in d and compatible(OPS['read'], arg['argument'], d[k]) for d in relevant) for k in ('x', 'y', 'z')}
            assert arg['full_support_paths'] == sorted('/'+k for k, count in expected.items() if relevant and count == len(relevant))
            assert arg['best_case_support'] == max(expected.values())
        assert 'PRIVATE' not in json.dumps(row)
        assert row == review(list(reversed(cases)))


def test_nested_projection_and_no_route():
    fields, omitted = m.project({'nested': {'code': 1}, 'array': [2], 'unsafe/key': 3, 'none': None})
    assert fields == {'/nested/code': 1} and omitted == 3
    row = review([case(1, {'id': 1}, [['b']])])
    assert all(a['status'] == 'no_reviewed_route' for a in row['arguments'])
    assert not row['all_arguments_have_unique_candidates']


def test_unique_and_ambiguous_never_authorized():
    row = review([case(1, {'x': 1, 'y': 'PRIVATE', 'z': 'PRIVATE2'}, [['a']])])
    assert row['unreviewed_unique_argument_map'] == {'id': '/x'}
    assert row['arguments'][1]['status'] == 'ambiguous'
    assert not row['binding_authorized']
    assert not row['all_arguments_have_unique_candidates']


def test_budget_refusal(monkeypatch):
    with pytest.raises(ValueError, match='scalar field budget'):
        m.project({f'k{i}': i for i in range(201)})
    monkeypatch.setattr(m, 'MAX_CHECKS', 1)
    with pytest.raises(ValueError, match='work budget'):
        review([case(1, {'id': 1, 'tag': 'a'}, [['a']])])
