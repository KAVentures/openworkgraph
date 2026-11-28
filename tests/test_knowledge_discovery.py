import pytest
from mcp_server.knowledge_discovery import discover, request_args


def rec(n, title, procedure='secret procedure', goal=''):
    return {'workflow_id':f'process_{n}', 'title':title, 'revision':1, 'content_sha256':'a'*64,
            'procedure':procedure,'process_model':{'goal':goal},'observed_evidence':False,
            'organization_policy':False,'execution_authorization':False,'decision_rules':['secret rule']}


def response(*rows, more=False):
    return {'format':'openworkgraph.workflow-knowledge.v1','workflows':list(rows),'returned':len(rows),
            'has_more':more,'knowledge_is_observed_evidence':False,'knowledge_is_execution_authorization':False}


def test_literal_search_no_full_records_or_query_echo():
    params, terms = request_args('',10,'FAKTURA åtgärd',False)
    assert params == {'workflow_id':'','limit':100}
    r=discover(response(rec(1,'Faktura',goal='Åtgärd'),rec(2,'Faktura')),terms,10)
    assert [c['workflow_id'] for c in r['candidates']]==['process_1']
    assert 'secret' not in str(r) and 'FAKTURA' not in str(r)
    assert not r['content_digest_verified'] and not r['execution_authorized']


def test_summary_and_truncation():
    _,terms=request_args('',1,'',True)
    r=discover(response(rec(1,'A'),rec(2,'B'),more=True),terms,1)
    assert r['records_scanned']==2 and r['matches_in_scanned_records']==2 and r['returned']==1
    assert r['source_inventory_truncated'] and r['matches_truncated']
    r=discover(response(rec(1,'A'),more=True),['missing'],1)
    assert r['returned']==0 and r['source_inventory_truncated']


@pytest.mark.parametrize('args',[('id',1,'query',False),('',101,'',True),('',True,'',True),('',1,'a\n',True),('',1,'x '*9,True),('',1,'x'*201,True),('bad/id',1,'',True)])
def test_invalid_args(args):
    with pytest.raises(ValueError):request_args(*args)


@pytest.mark.parametrize('change',['duplicate','claim','bad_digest','count','flags'])
def test_refuses_bad_source(change):
    row=rec(1,'A'); r=response(row)
    if change=='duplicate':r=response(row,row)
    elif change=='claim':row['execution_authorization']=True
    elif change=='bad_digest':row['content_sha256']='bad'
    elif change=='count':r['returned']=0
    elif change=='flags':r['knowledge_is_observed_evidence']=True
    with pytest.raises(ValueError):discover(r,[],5)
