from itertools import product
from random import Random
import pytest
from shared.connector_mapping_feasibility import assess


def oracle(graph, fixed):
    left = sorted(graph)
    full = [dict(zip(left, xs)) for xs in product(*(graph[k] for k in left))
            if len(set(xs)) == len(xs) and all(xs[left.index(k)] == v for k,v in fixed.items())]
    allowed = {k: sorted({m[k] for m in full}) for k in left}
    return full, allowed


def test_random_exhaustive():
    rng = Random(1972)
    for _ in range(2500):
        n, m = rng.randrange(1,7), rng.randrange(1,8)
        graph = {f'l{i}': [f'r{j}' for j in range(m) if rng.random() < .48] for i in range(n)}
        fixed = {}
        for k, values in graph.items():
            options = [v for v in values if v not in fixed.values()]
            if options and rng.random() < .22:
                fixed[k] = rng.choice(options)
        full, allowed = oracle(graph, fixed)
        result = assess(graph, fixed)
        assert result['feasible'] == bool(full)
        if full:
            assert result['allowed'] == allowed
            assert result['hall_witness'] is None
        else:
            witness = result['hall_witness']
            neighbors = {r for k in witness['left'] for r in graph[k] if r not in fixed.values()}
            assert set(witness['neighbors']) == neighbors
            assert len(witness['left']) > len(neighbors)
            assert witness['deficit'] == len(witness['left'])-len(neighbors)


def test_boundaries():
    assert assess({})['feasible']
    assert assess({'a':['1','2'],'b':['2']})['allowed'] == {'a':['1'],'b':['2']}
    assert assess({'a':['1','2'],'b':['1','2']},{'a':'2'})['allowed'] == {'a':['2'],'b':['1']}
    assert not assess({'a':['1'],'b':['1']})['feasible']
    with pytest.raises(ValueError):
        assess({'a':['1']},{'a':'2'})
    with pytest.raises(ValueError):
        assess({'a':['1'],'b':['1']},{'a':'1','b':'1'})


def test_dense_bound():
    graph = {f'l{i:02}':[f'r{j:03}' for j in range(500)] for i in range(50)}
    result=assess(graph)
    assert result['feasible'] and result['allowed']==graph
