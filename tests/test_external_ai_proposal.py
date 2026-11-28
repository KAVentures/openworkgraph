from copy import deepcopy
import json
from itertools import product
import pytest
from shared.external_ai_proposal import FORMAT, SECTIONS, prepare, review, digest, main


def evidence():
    return {'format':'openworkgraph.workflow-evidence.v1','canonical_evidence_included':True,
            'provenance':{'source_limit_reached':False,'selected_execution_ids':['run-a','run-b']},
            'canonical_evidence':[{'execution_id':r,'observed_event_count':1,'returned_event_count':1,
                'events_truncated':False,'event_membership_verified':False,'nearby_events_may_be_unrelated':True,
                'events':[{'event_ref':'event:'+v*16,'untrusted_observed_data':True,'metadata':{'text':'PRIVATE'}}]}
                for r,v in [('run-a','a'),('run-b','b')]]}


def proposal(bundle):
    p={'format':FORMAT,'evidence_sha256':digest(bundle),'sections':{k:[{'kind':'unknown','text':'Question PRIVATE','citations':[]}] for k in SECTIONS}}
    p['sections']['goal']=[{'kind':'inferred','text':'Goal PRIVATE','citations':[{'execution_id':'run-a','event_ref':'event:'+'a'*16}]}]
    return p


def test_exact_reference_membership_and_privacy():
    b=evidence();p=proposal(b);out=review(b,digest(b),p)
    assert out['distinct_cited_event_occurrences']==1 and out['unreferenced_execution_count']==1
    assert out['coverage'][0]['cited_event_count']==1 and out['coverage'][1]['cited_event_count']==0
    assert out['schema_and_citation_membership_validated'] and not out['semantic_claims_verified']
    assert not out['execution_authorized'] and not out['live_source_or_revocation_checked']
    assert 'PRIVATE' not in json.dumps(out) and 'PRIVATE' not in json.dumps(prepare(b))
    for run,event in product(('run-a','run-b','wrong'),('a','b','c')):
        p=proposal(b);p['sections']['goal'][0]['citations']=[{'execution_id':run,'event_ref':'event:'+event*16}]
        if (run,event) in [('run-a','a'),('run-b','b')]:
            assert review(b,digest(b),p)['distinct_cited_event_occurrences']==1
        else:
            with pytest.raises(ValueError,match='selected execution'):
                review(b,digest(b),p)


def test_policy_boundary_and_strict_shape():
    b=evidence()
    for section in ('policy','human_approvals'):
        for kind in ('observed','inferred'):
            p=proposal(b);p['sections'][section][0]['kind']=kind
            with pytest.raises(ValueError,match='Policy and approval'):
                review(b,digest(b),p)
    p=proposal(b);p['sections']['goal'][0]['citations']=[]
    with pytest.raises(ValueError,match='needs a citation'):
        review(b,digest(b),p)
    p=proposal(b);p['sections'].pop('exceptions')
    with pytest.raises(ValueError,match='required sections'):
        review(b,digest(b),p)
    p=proposal(b);p['execution_authorized']=True
    with pytest.raises(ValueError,match='exact selected'):
        review(b,digest(b),p)


def test_limited_coverage_and_stale_pin():
    b=evidence();b['canonical_evidence'][0].update(observed_event_count=2,events_truncated=True)
    p=proposal(b);p['sections']['unknowns'][0]['kind']='proposed'
    with pytest.raises(ValueError,match='explicit unknown'):
        review(b,digest(b),p)
    p=proposal(b);assert review(b,digest(b),p)['limited_excerpt_coverage']
    with pytest.raises(ValueError,match='Evidence pin'):
        review(b,'f'*64,p)
    c=deepcopy(b);c['canonical_evidence'][0]['events_truncated']=False
    with pytest.raises(ValueError,match='coverage'):
        prepare(c)
    c=deepcopy(b);c['canonical_evidence'][0]['events']*=2;c['canonical_evidence'][0].update(returned_event_count=2,events_truncated=False)
    with pytest.raises(ValueError,match='Ambiguous'):
        prepare(c)


def test_cli_exclusive_and_duplicate_json(tmp_path):
    b=evidence();p=proposal(b)
    for n,v in [('e',b),('p',p)]: (tmp_path/n).write_text(json.dumps(v))
    args=['--evidence',str(tmp_path/'e'),'--confirm-local-review']
    assert main(['prepare',*args,'--output',str(tmp_path/'request')])==0
    assert json.loads((tmp_path/'request').read_text())['evidence_sha256']==digest(b)
    command=['review',*args,'--proposal',str(tmp_path/'p'),'--evidence-sha256',digest(b),'--output',str(tmp_path/'receipt')]
    assert main(command)==0
    with pytest.raises(SystemExit) as e: main(command)
    assert e.value.code==2
    (tmp_path/'p').write_text('{"format":"x","format":"y"}')
    with pytest.raises(SystemExit) as e: main(command[:-1]+[str(tmp_path/'new')])
    assert e.value.code==2 and not (tmp_path/'new').exists()
