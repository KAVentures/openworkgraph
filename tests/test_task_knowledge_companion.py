import hashlib
import pytest
from mcp_server import task_knowledge_companion as companion
from test_knowledge_discovery import rec, response


def record(n=1):
    return {**rec(n, 'Invoice'), 'format':'openworkgraph.workflow-knowledge.v1',
            'data_layer':'user_reviewed_knowledge','review_basis':'client_declared_user_confirmation'}


def ref(r):
    return {k:r[k] for k in ('workflow_id','revision','content_sha256')}


def test_exact_order_and_independent_source_fingerprint():
    a,b=record(1),record(2); data={r['workflow_id']:response(r) for r in (a,b)}; calls=[]
    def get(path,params):
        calls.append((path,params)); return data[params['workflow_id']]
    result=companion.read([ref(b),ref(a)],get)
    assert calls==[('/v1/workflow-knowledge',{'workflow_id':r['workflow_id'],'limit':1}) for r in (b,a)]
    assert [r['reviewed_record'] for r in result['records']]==[b,a]
    assert result['records'][0]['ai_visible_api_sha256']==hashlib.sha256(companion.encoded(data[b['workflow_id']])).hexdigest()
    assert not result['content_digest_verified'] and not result['relevance_verified'] and not result['execution_authorized']
    b['title']='changed';assert result['records'][0]['reviewed_record']['title']=='Invoice'


def test_no_selection_means_no_read():
    assert companion.read(None,lambda *_:pytest.fail('unexpected read')) is None
    assert companion.read([],lambda *_:pytest.fail('unexpected read')) is None


@pytest.mark.parametrize('bad',[None,{}, {'workflow_id':'x'}, {'workflow_id':'x','revision':True,'content_sha256':'a'*64}])
def test_bad_reference(bad):
    with pytest.raises(ValueError): companion.read([bad],lambda *_:pytest.fail('unexpected read'))


def test_duplicate_and_max_refs_refused_before_read():
    a=ref(record())
    for refs in [[a,a],[ref(record(i)) for i in range(4)]]:
        with pytest.raises(ValueError): companion.read(refs,lambda *_:pytest.fail('unexpected read'))


@pytest.mark.parametrize('bad',['missing','stale','wrong_id','wrong_pin','policy','not_reviewed','more','size'])
def test_bad_returned_record_refused(bad,monkeypatch):
    r=record(); pin=ref(r); data=response(r)
    if bad=='missing':data=response()
    elif bad=='stale':r['revision']=2
    elif bad=='wrong_id':r['workflow_id']='different'
    elif bad=='wrong_pin':r['content_sha256']='b'*64
    elif bad=='policy':r['organization_policy']=True
    elif bad=='not_reviewed':r['review_basis']='unknown'
    elif bad=='more':data['has_more']=True
    elif bad=='size':monkeypatch.setattr(companion,'MAX_BYTES',10)
    with pytest.raises(ValueError): companion.read([pin],lambda *_:data)
