import copy
import pytest
from evals.task_context_ablation import plan, score, ARMS


def fixture():
    cases=[{'case_id':f'case_{n}','fold':fold,'source_state_sha256':'a'*64,'task_sha256':'b'*64,
            'request_sha256_by_arm':dict(zip(ARMS,[c*64 for c in 'cde']))} for n,fold in enumerate(('development','holdout'))]
    p=plan(cases,{'model_id':'fixture','settings_sha256':'f'*64})
    rows=[]
    for s in p['slots']:
        rows.append({**{k:s[k] for k in ('slot_id','request_sha256','source_state_sha256')},'model_id':'fixture','settings_sha256':'f'*64,
                     'status':'completed','output_sha256':'1'*64,'review':{'reviewer_id':'reviewer','output_sha256':'1'*64,
                     'task_success':s['arm']=='augmented','consequential_wrong_action':False,'wrong_assumptions':0}})
    return p, rows


def test_frozen_balanced_orders_and_holdout():
    p,r=fixture();assert len(p['slots'])==12
    assert [s['arm'] for s in p['slots'][:6]]==['control','observed','augmented','observed','augmented','control']
    result=score(p,p['plan_sha256'],r)
    assert result['all_attempts_recorded'] and result['by_fold']['holdout']['augmented']['reviewed_success']==2
    assert not result['superiority_claimed'] and not result['transport_attested']


def test_missing_timeout_and_unsafe_success_remain_in_denominator():
    p,r=fixture();r[0].update(status='timeout',output_sha256=None,review=None);r[2]['review']['consequential_wrong_action']=True
    result=score(p,p['plan_sha256'],r[:-1]);dev=result['by_fold']['development']
    assert dev['control']['timeout']==1 and dev['control']['planned']==2
    assert dev['augmented']['reviewed_success']==1 and dev['augmented']['consequential_wrong_action']==1
    assert not result['all_attempts_recorded'] and sum(x['missing'] for x in result['by_fold']['holdout'].values())==1


@pytest.mark.parametrize('bad',['request','state','settings','review','dup','pin','plan','failed_review'])
def test_refuses_bad_lineage(bad):
    p,r=fixture();expected=p['plan_sha256']
    if bad=='request':r[0]['request_sha256']='0'*64
    elif bad=='state':r[0]['source_state_sha256']='0'*64
    elif bad=='settings':r[0]['settings_sha256']='0'*64
    elif bad=='review':r[0]['review']['output_sha256']='0'*64
    elif bad=='dup':r.append(copy.deepcopy(r[0]))
    elif bad=='pin':expected='0'*64
    elif bad=='plan':p['slots'][0]['order']=3
    elif bad=='failed_review':r[0]['status']='failed'
    with pytest.raises(ValueError):score(p,expected,r)
