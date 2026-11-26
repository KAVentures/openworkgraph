"""Random DAG oracle for the dependency reassessment worklist."""
from random import Random
from shared.process_dependency_reassessment import reassessment


def test_random_dags_against_independent_reachability_oracle():
    rng = Random(20261124)
    for trial in range(300):
        n = rng.randrange(1, 50)
        names = [f'p{i:03}' for i in range(n)]
        nodes, items, edges = [], {}, []
        direct_expected, parents = {}, {}
        for i, wid in enumerate(names):
            available, current, reviewed = (rng.random() > .08, rng.random() > .12, rng.random() > .2)
            pin = {'workflow_id': wid, 'revision': 2, 'content_sha256': 'a'*64}
            nodes.append({**pin, 'available': available, 'current': current,
                          'active_latest_independently_reviewed_model': reviewed})
            root_stale = rng.random() < .15
            has_declaration = rng.random() > .12
            if has_declaration:
                items[wid] = {'revision': 1, 'dependencies': {'root_process': {**pin, 'revision': 1 if root_stale else 2}}}
            parents[wid] = []
            stale = False
            for p in names[:i] if has_declaration else []:
                if rng.random() < .1:
                    parents[wid].append(p)
                    prior = nodes[names.index(p)]
                    old = rng.random() < .15
                    edges.append({'consumer': wid, 'prerequisite': p, 'link_id': 'to_'+p,
                                  'prerequisite_process': {'workflow_id': p, 'revision': 1 if old else 2, 'content_sha256': 'a'*64}})
                    stale |= old or not prior['available'] or not prior['current']
            direct_expected[wid] = not (available and current and reviewed and has_declaration and not root_stale and not stale)
        result = reassessment({'nodes': nodes, 'edges': edges, 'prerequisite_first_order': names}, items)
        by_id = {r['workflow_id']: r for r in result['processes']}
        for wid in names:
            reachable, todo = set(), list(parents[wid])
            while todo:
                p = todo.pop()
                if p not in reachable:
                    reachable.add(p)
                    todo.extend(parents[p])
            expected_count = sum(direct_expected[p] for p in reachable)
            row = by_id[wid]
            assert bool(row['direct_findings']) == direct_expected[wid]
            assert row['distinct_upstream_processes_with_direct_findings'] == expected_count
            assert row['requires_reassessment'] == (direct_expected[wid] or expected_count > 0)
            assert row['dependency_layer'] == 1 + max((by_id[p]['dependency_layer'] for p in parents[wid]), default=0)
        assert result['review_needed_count'] == sum(r['requires_reassessment'] for r in by_id.values())
