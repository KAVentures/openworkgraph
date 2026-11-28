from random import Random
import pytest
from shared.ocel_directed_topology import analyze


def oracle(nodes, pairs):
    reach = {(a, a) for a in nodes} | set(pairs)
    for k in nodes:
        for a in nodes:
            for b in nodes:
                if (a, k) in reach and (k, b) in reach:
                    reach.add((a, b))
    return {a: min(b for b in nodes if (a,b) in reach and (b,a) in reach) for a in nodes}


def test_random_reachability_oracle():
    rng = Random(1603)
    for _ in range(600):
        nodes = [str(i) for i in range(rng.randrange(11))]
        pairs = [(a,b) for a in nodes for b in nodes if rng.random() < .23]
        assertions = [(a,b,'x') for a,b in pairs] + [(a,b,'y') for a,b in pairs if rng.random() < .3]
        result = analyze([(a,'t'+str(int(a)%2)) for a in nodes], assertions)
        membership = oracle(nodes,pairs)
        assert {r['object_id']:r['component_id'] for r in result['directed_members']} == membership
        inter = {(membership[a],membership[b]) for a,b in pairs if membership[a] != membership[b]}
        assert result['counts']['condensation_edges'] == len(inter)
        for row in result['directed_components']:
            c = row['component_id']; group = {a for a in nodes if membership[a]==c}
            assert row['objects'] == len(group)
            assert row['cyclic'] == (len(group)>1 or any((a,a) in pairs for a in group))
            assert row['predecessor_components'] == sum(b==c for a,b in inter)
            assert row['successor_components'] == sum(a==c for a,b in inter)
            assert row['internal_literal_assertions'] == sum(a in group and b in group for a,b,q in assertions)
            assert row['outgoing_literal_assertions'] == sum(a in group and b not in group for a,b,q in assertions)
        assert result == analyze([(a,'t'+str(int(a)%2)) for a in reversed(nodes)], list(reversed(assertions)))


def test_directed_cycle_dag_and_isolate():
    objects = [(a,'t') for a in 'abcdef']
    assertions = [('a','b','x'),('b','a','x'),('b','c','x'),('b','c','y'),('c','d','x'),('d','d','x'),('d','e','x')]
    result=analyze(objects,assertions)
    assert result['counts'] == {'objects':6,'literal_assertions':7,'directed_pairs_including_self':6,
        'strong_components':5,'cyclic_components':2,'objects_in_cyclic_components':3,'self_loop_objects':1,
        'source_components':2,'sink_components':2,'condensation_edges':3,'largest_strong_component_objects':2}
    c=next(r for r in result['directed_components'] if r['component_id']=='a')
    assert c['internal_literal_assertions']==2 and c['outgoing_literal_assertions']==2
    isolate=next(r for r in result['directed_components'] if r['component_id']=='f')
    assert isolate['source_component'] and isolate['sink_component'] and not isolate['cyclic']


def test_long_chain_and_cycle():
    objects=[(str(i),'t') for i in range(20000)]
    edges=[(str(i),str(i+1),'') for i in range(19999)]
    r=analyze(objects,edges)
    assert r['counts']['strong_components']==20000 and r['counts']['cyclic_components']==0
    r=analyze(objects,edges+[('19999','0','')])
    assert r['counts']['strong_components']==1 and r['counts']['objects_in_cyclic_components']==20000


@pytest.mark.parametrize('objects,assertions', [([('a','t'),('a','s')],[]),([('a','t')],[('a','b','')]),([('a','t')],[('a','a',''),('a','a','')])])
def test_invalid_population(objects,assertions):
    with pytest.raises(ValueError):
        analyze(objects,assertions)
