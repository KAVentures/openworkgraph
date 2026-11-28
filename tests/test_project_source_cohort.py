import json
from hashlib import sha256
from itertools import product
import pytest
from shared.project_source_cohort import review, digest


def fixtures(modes):
    expected = {'current_idea_revision':3,'spec_sha256':'a'*64,'process_reference':{'revision':1},
                'expectations':[{'terminal_activity_id':'review','operation':'github_pr_merged'}]}
    cases,docs,checked = [],[],{}
    for i,mode in enumerate(modes):
        ref='owg:r:'+f'{i:032x}'
        case={'case_id':f'case_{i}','terminal_activity_id':'review','expected_operation':'github_pr_merged',
              'resource_ref':ref,'document_sha256':None}
        if mode!='missing':
            verified=mode!='unknown'
            doc={'idea_id':'idea','project_id':'project','spec_sha256':'a'*64,'process_reference':{'revision':1},
                 'terminal_activity_id':'review','receipt':{'resource_ref':ref,'expected_operation':'github_pr_merged',
                 'operation_verified':verified,'private':'PRIVATE'}}
            raw=json.dumps(doc,indent=2)+'\n';pin=sha256(raw.encode()).hexdigest()
            case['document_sha256']=pin;docs.append(raw)
            checked[pin]={'format':'openworkgraph.project-source-check-review.v1','project_id':'project','document_sha256':pin,
                          'exact_retained_export_bytes_match':True,'current_idea_revision':3,'source_read_performed':False,
                          'execution_authorized':False,'historical_source_operation_reported_verified':verified,
                          'idea_revision_matches_export':mode!='stale','process_reference_is_current':True,
                          'process_expectation_status':'retained_expectation_matches','run_attribution_status':'no_run_selected_at_export'}
        cases.append(case)
    return {'expected_idea_revision':3,'cases':cases,'documents':docs}, expected, checked


def call(p,b,c):
    return review('idea','project',p,options=lambda *a:b,reconcile=lambda i,j,raw:c[sha256(raw).hexdigest()])


def test_every_case_preserved_and_unknown_not_failure_or_success():
    for modes in product(('missing','unknown','verified','stale'),repeat=3):
        p,b,c=fixtures(modes);r=call(p,b,c)
        assert r['case_count']==3
        assert r['historical_operation_reported_verified_count']==sum(m in ('verified','stale') for m in modes)
        assert r['historical_operation_not_established_count']==modes.count('unknown')
        assert r['missing_document_count']==modes.count('missing')
        assert r['local_reference_needs_review_count']==modes.count('stale')
        assert 'PRIVATE' not in json.dumps(r) and 'owg:r:' not in json.dumps(r)
        assert not r['case_association_verified'] and not r['business_outcomes_verified']
        assert r['review_sha256']==digest({k:v for k,v in r.items() if k!='review_sha256'})


def test_refuses_duplicate_targets_unused_documents_and_changed_pin():
    p,b,c=fixtures(('verified','verified'))
    p['cases'][1]['resource_ref']=p['cases'][0]['resource_ref']
    with pytest.raises(ValueError,match='reused across cases'):call(p,b,c)
    p,b,c=fixtures(('verified','verified'));p['cases'][1]['document_sha256']=None
    with pytest.raises(ValueError,match='Unused'):call(p,b,c)
    p,b,c=fixtures(('verified',));p['documents'][0]+=' '
    with pytest.raises(ValueError,match='Missing or reused'):call(p,b,c)
    p,b,c=fixtures(('verified',));p['cases'][0]['resource_ref']='owg:r:'+'f'*32
    with pytest.raises(ValueError,match='does not match'):call(p,b,c)


def test_reviewer_must_validate_exact_retained_bytes_and_current_basis():
    p,b,c=fixtures(('verified',));pin=p['cases'][0]['document_sha256']
    c[pin]['exact_retained_export_bytes_match']=False
    with pytest.raises(ValueError,match='Retained document'):call(p,b,c)
    p,b,c=fixtures(('verified',));values=iter([b,{**b,'current_idea_revision':4}])
    with pytest.raises(ValueError,match='revision changed'):
        review('idea','project',p,options=lambda *a:next(values),reconcile=lambda *a:c[pin])
    p,b,c=fixtures(('missing',));p['cases'][0]['expected_operation']='unreviewed'
    with pytest.raises(ValueError,match='confirmed terminal'):call(p,b,c)
