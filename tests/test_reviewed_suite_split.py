from itertools import product
import json
from random import Random
import pytest
from shared.reviewed_suite_split import CATEGORIES, choose, digest, plan, main


def test_exact_randomized_group_split_oracle():
    rng = Random(87)
    for _ in range(500):
        vectors = {f'g{i}': tuple(rng.randrange(3) for _ in range(3)) for i in range(rng.randrange(2,9))}
        vectors = {g:v for g,v in vectors.items() if sum(v)}
        total = tuple(sum(v[i] for v in vectors.values()) for i in range(3))
        n = sum(total)
        if n < 2:
            continue
        target = rng.randrange(1,n)
        low = tuple(rng.randrange(3) for _ in range(3))
        high = tuple(rng.randrange(3) for _ in range(3))
        actual, visited, sizes = choose(vectors,total,target,low,high)
        all_vectors, feasible = set(), []
        for mask in product((False,True),repeat=len(vectors)):
            groups = tuple(g for g,b in zip(sorted(vectors),mask) if b)
            v = tuple(sum(vectors[g][i] for g in groups) for i in range(3))
            all_vectors.add(v)
            if 0 < sum(v) < n and all(low[i]<=total[i]-v[i] and high[i]<=v[i] for i in range(3)):
                feasible.append((v,groups))
        expected = min(feasible,key=lambda p:(abs(sum(p[0])-target),sum(abs(n*x-sum(p[0])*t) for x,t in zip(p[0],total)),p[1])) if feasible else None
        assert actual == expected
        assert visited == len(all_vectors)
        assert sizes == sorted({sum(v) for v,_ in feasible})


def inputs():
    cases = [{'id':f'case_{i}','category':CATEGORIES[i%3],'input':{'private':f'SECRET_{i}'},
              'expected':{'status':'proposal','accepted_routes':[['start','end']],'review_confirmed':True}} for i in range(6)]
    suite = {'format':'openworkgraph.reviewed-shadow-cases.v1','reviewed_by_customer':True,'case_inputs_authorized':True,
             'process_reference':{'workflow_id':'example','revision':1,'content_sha256':'a'*64},'spec_sha256':'b'*64,'cases':cases}
    groups = {'case_groups':{c['id']:f'group_{i//3}' for i,c in enumerate(cases)}}
    policy = {'target_holdout_cases':3,'min_development':dict.fromkeys(CATEGORIES,1),'min_holdout':dict.fromkeys(CATEGORIES,1)}
    return suite,groups,policy


def test_exact_groups_and_compatible_outputs():
    suite,groups,policy=inputs()
    dev,test,r=plan(suite,digest(suite),groups,policy)
    assert len(dev['cases'])==len(test['cases'])==3
    assert {groups['case_groups'][c['id']] for c in dev['cases']}.isdisjoint({groups['case_groups'][c['id']] for c in test['cases']})
    assert sorted(dev['cases']+test['cases'],key=lambda c:c['id'])==suite['cases']
    assert r['development_sha256']==digest(dev) and r['holdout_sha256']==digest(test)
    assert 'SECRET' not in json.dumps(r) and 'group_0' not in json.dumps(r)
    assert r['review_sha256']==digest({k:v for k,v in r.items() if k!='review_sha256'})
    assert not r['independence_verified'] and not r['execution_authorized']


def test_infeasible_and_invalid_inputs():
    suite,groups,policy=inputs()
    policy['min_holdout']['normal']=2
    dev,test,r=plan(suite,digest(suite),groups,policy)
    assert dev is test is None and r['status']=='infeasible' and r['feasible_holdout_sizes']==[]
    with pytest.raises(ValueError,match='digest'):
        plan(suite,'f'*64,groups,policy)
    groups['case_groups'].pop('case_0')
    with pytest.raises(ValueError,match='opaque group'):
        plan(suite,digest(suite),groups,policy)
    suite,groups,policy=inputs();suite['cases'][1]['input']=suite['cases'][0]['input']
    with pytest.raises(ValueError,match='Duplicate exact'):
        plan(suite,digest(suite),groups,policy)


def test_cli_exclusive_outputs(tmp_path):
    suite,groups,policy=inputs()
    for name,value in [('suite',suite),('groups',groups),('policy',policy)]:
        (tmp_path/name).write_text(json.dumps(value))
    args=['--suite',str(tmp_path/'suite'),'--suite-sha256',digest(suite),'--groups',str(tmp_path/'groups'),
          '--policy',str(tmp_path/'policy'),'--output-directory',str(tmp_path/'out'),'--confirm-local-case-split']
    assert main(args)==0
    assert sorted(p.name for p in (tmp_path/'out').iterdir())==['development.json','holdout.json','review.json']
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code==2
