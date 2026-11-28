import copy
import hashlib
import json
import pytest
from shared import ai_implementation_knowledge_context as ctx


def record(key='process_one', revision=1):
    value = {'format':'openworkgraph.workflow-knowledge.v1','workflow_id':key,'title':'Review invoices',
             'data_layer':'user_reviewed_knowledge','review_basis':'client_declared_user_confirmation',
             'process_model':{'goal':'Review invoices å','business_rules':[{'text':'Check approval','user_confirmed':False}]},
             'unresolved_questions':['Who approves?'],'evidence_refs':['old:event'],
             'observed_evidence':False,'organization_policy':False,'execution_authorization':False}
    return {**value,'revision':revision,'content_sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()}


def test_exact_context_and_separate_knowledge():
    r=record(); ref=ctx.reference(r); c=ctx.build('a'*64,[ref],lambda _:r)
    assert c['reviewed_knowledge']==[r] and c['references']==[ref]
    assert c['context_sha256']==ctx.digest({k:v for k,v in c.items() if k!='context_sha256'})
    assert not c['observed_evidence'] and not c['execution_authorized'] and not c['external_sharing_authorized']
    r['process_model']['goal']='changed'; assert c['reviewed_knowledge'][0]['process_model']['goal']=='Review invoices å'


@pytest.mark.parametrize('bad',['stale','digest','policy','duplicate','empty','budget','wrong_packet','race'])
def test_refuses_bad_or_stale_context(bad,monkeypatch):
    r=record(); refs=[ctx.reference(r)]; pin='a'*64; calls=[]
    def get(_):
        calls.append(1)
        return record(revision=2) if bad=='race' and len(calls)>1 else r
    if bad=='stale': r['revision']=2
    if bad=='digest': r['title']='tampered'
    if bad=='policy': r['organization_policy']=True
    if bad=='duplicate': refs*=2
    if bad=='empty': refs=[]
    if bad=='budget': monkeypatch.setattr(ctx,'MAX_BYTES',10)
    if bad=='wrong_packet': pin='bad'
    with pytest.raises(ValueError): ctx.build(pin,refs,get)


def test_order_and_excludes_unselected():
    a,b=record('a'),record('b'); rows={'a':a,'b':b}
    c=ctx.build('b'*64,[ctx.reference(b),ctx.reference(a)],rows.__getitem__)
    assert [r['workflow_id'] for r in c['reviewed_knowledge']]==['b','a']
