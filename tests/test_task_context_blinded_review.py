import copy
import pytest
from evals import task_context_ablation as ab, task_context_collector as c, task_context_blinded_review as blind
from test_task_context_collector import setup, endpoint, reply, run


def prepare_fixture(setup,endpoint):
    p,requests,root=setup
    cases=copy.deepcopy(p['cases']);cases[0]['task_sha256']=c.sha(b'Check invoice')
    p=ab.plan(cases,p['settings']);setup=(p,requests,root)
    endpoint[2].extend([(200,reply()),(503,b'{}'),(200,reply())])
    for n in range(3):run(setup,endpoint,n)
    m={'case1':{'task':'Check invoice','rubric':'Review correct result and abstention','ground_truth_reviewer_id':'truth','approved':True}}
    packet,key=blind.prepare(p,p['plan_sha256'],root,m,confirmed=True)
    reviews=[{**r,'reviewer_id':'independent','task_success':True,'consequential_wrong_action':False,'wrong_assumptions':0} for r in blind.template(packet)]
    return p,root,m,packet,key,reviews,setup


def test_exact_blind_packet_and_denominator(setup,endpoint):
    p,root,m,packet,key,reviews,_=prepare_fixture(setup,endpoint)
    assert len(packet['entries'])==2 and all(set(e)=={'review_id','task','rubric','output'} for e in packet['entries'])
    assert all(e['output']=='Exact response å\n' for e in packet['entries'])
    assert all(len(e['review_id'])==32 for e in packet['entries'])
    assert not packet['blinding_verified']
    assert not any(r['slot_id'] in str(packet) for r in p['slots'])
    result=blind.reconcile(p,p['plan_sha256'],root,m,packet,key,ab.digest(key),reviews)
    report=result['collected']['report'];assert report['recorded_attempts']==3 and report['planned_attempts']==6
    assert report['by_fold']['holdout']['observed']['failed']==1 and not result['reviewer_independence_verified']


def test_reject_modified_packet_key_review_material_or_incomplete_review(setup,endpoint):
    p,root,m,packet,key,reviews,_=prepare_fixture(setup,endpoint);pin=ab.digest(key)
    for what in ('packet','key','material','missing','duplicate','truth','null'):
        a,b,d,e=map(copy.deepcopy,(m,packet,key,reviews))
        if what=='packet':b['entries'][0]['output']='changed'
        if what=='key':d['mapping'].clear()
        if what=='material':a['case1']['rubric']='changed'
        if what=='missing':e.pop()
        if what=='duplicate':e[1]=e[0]
        if what=='truth':e[0]['reviewer_id']='truth'
        if what=='null':e[0]['task_success']=None
        with pytest.raises(ValueError):blind.reconcile(p,p['plan_sha256'],root,a,b,d,pin,e)


def test_new_attempt_invalidates_frozen_review(setup,endpoint):
    p,root,m,packet,key,reviews,updated=prepare_fixture(setup,endpoint)
    endpoint[2].append((200,reply()));run(updated,endpoint,3)
    with pytest.raises(ValueError,match='inventory changed'):blind.reconcile(p,p['plan_sha256'],root,m,packet,key,ab.digest(key),reviews)


def test_prepare_requires_approval_and_exact_task(setup,endpoint):
    p,root,m,packet,key,reviews,_=prepare_fixture(setup,endpoint)
    with pytest.raises(ValueError):blind.prepare(p,p['plan_sha256'],root,m)
    m['case1']['task']='Different task'
    with pytest.raises(ValueError,match='frozen task'):blind.prepare(p,p['plan_sha256'],root,m,confirmed=True)
