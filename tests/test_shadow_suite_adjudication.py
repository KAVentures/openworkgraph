from copy import deepcopy
from itertools import product
import json
import pytest
from shared.shadow_suite_adjudication import compare, reconcile, digest, main


def suite(modes):
    return {'format':'openworkgraph.reviewed-shadow-cases.v1','reviewed_by_customer':True,'case_inputs_authorized':True,
            'process_reference':{'workflow_id':'example','revision':1,'content_sha256':'a'*64},'spec_sha256':'b'*64,
            'cases':[{'id':f'case_{i}','category':'normal','input':{'value':f'PRIVATE_{i}'},
                      'expected':{'status':'needs_clarification' if mode==0 else 'proposal','review_confirmed':True,
                                  'accepted_routes':[] if mode==0 else [['start',f'route_{mode}','end']]}}
                     for i,mode in enumerate(modes)]}


def test_exhaustive_disagreement_and_adjudication():
    for modes in product(range(3),repeat=6):
        a,b=suite(modes[:3]),suite(modes[3:]);ap,bp=digest(a),digest(b)
        r=compare(a,ap,b,bp)
        expected=[f'case_{i}' for i in range(3) if modes[i]!=modes[i+3]]
        assert r['disputed_case_ids']==expected
        assert r['agreement_count']==3-len(expected)
        for choice in ('a','b'):
            output,receipt=reconcile(a,ap,b,bp,r['review_sha256'],{'decisions':dict.fromkeys(expected,choice)})
            assert output==(a if choice=='a' else b)
            assert receipt['output_suite_sha256']==digest(output)
            assert 'PRIVATE' not in json.dumps([receipt,r])
            assert not receipt['execution_authorized'] and not receipt['ground_truth_verified']


def test_order_and_multiple_accepted_routes():
    a=suite([1,2]);a['cases'][0]['expected']['accepted_routes'].append(['start','another','end'])
    b=deepcopy(a);b['cases'].reverse();b['cases'][1]['expected']['accepted_routes'].reverse()
    r=compare(a,digest(a),b,digest(b));assert r['agreement_count']==2
    b['cases'][1]['expected']['accepted_routes'].append(['start','another','end'])
    with pytest.raises(ValueError,match='Duplicate accepted'):
        compare(a,digest(a),b,digest(b))


def test_invalid_population_pin_and_incomplete_adjudication():
    a,b=suite([1,2]),suite([2,0]);ap,bp=digest(a),digest(b)
    r=compare(a,ap,b,bp)
    for decisions in ({},{'case_0':'a'},{'case_0':'a','case_1':'union'},{'case_0':'a','case_1':'b','case_2':'a'}):
        with pytest.raises(ValueError,match='Explicit choice'):
            reconcile(a,ap,b,bp,r['review_sha256'],{'decisions':decisions})
    with pytest.raises(ValueError,match='pin changed'):
        reconcile(a,ap,b,bp,'f'*64,{'decisions':{'case_0':'a','case_1':'b'}})
    for key,value in [('input',{'different':True}),('category','exception')]:
        c=deepcopy(b);c['cases'][0][key]=value
        with pytest.raises(ValueError,match='Different case input'):
            compare(a,ap,c,digest(c))
    with pytest.raises(ValueError,match='pin mismatch'):
        compare(a,'f'*64,b,bp)
    c=deepcopy(b);c['cases'].pop()
    with pytest.raises(ValueError,match='population'):
        compare(a,ap,c,digest(c))


def test_cli_exclusive_outputs(tmp_path):
    a,b=suite([1]),suite([0]);ap,bp=digest(a),digest(b)
    for name,value in [('a',a),('b',b),('decisions',{'decisions':{'case_0':'b'}})]:
        (tmp_path/name).write_text(json.dumps(value))
    common=['--reviewer-a',str(tmp_path/'a'),'--a-sha256',ap,'--reviewer-b',str(tmp_path/'b'),'--b-sha256',bp,'--confirm-local-review']
    assert main(['compare',*common,'--output',str(tmp_path/'report')])==0
    report=json.loads((tmp_path/'report').read_text())
    args=['reconcile',*common,'--review-sha256',report['review_sha256'],'--decisions',str(tmp_path/'decisions'),'--output',str(tmp_path/'out')]
    assert main(args)==0
    assert json.loads((tmp_path/'out/reviewed-cases.json').read_text())==b
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code==2
