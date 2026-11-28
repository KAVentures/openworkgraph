import copy
import json
import pytest
from shared import ai_implementation_compat as compat
from shared import ai_implementation_review as integrated
from shared import external_ai_proposal as sectioned
from test_ai_implementation_review import source, proposal


def fixture():
    s = source(True)
    r = s['canonical_evidence'][0]
    eid = r['execution_id']
    s['provenance']['selected_execution_ids'] = [eid]
    r['nearby_events_may_be_unrelated'] = True
    r['events'][0]['untrusted_observed_data'] = True
    r['events'][0]['metadata'] = {'text':'untrusted'}
    second = copy.deepcopy(r['events'][0]); second['event_ref'] = 'event:'+'c'*16
    r['events'].append(second); r['returned_event_count'] = 2; r['observed_event_count'] = 3
    pin = sectioned.digest(s)
    old = {'format':sectioned.FORMAT,'evidence_sha256':pin,'sections':{k:[{'kind':'unknown','text':'Review '+k,'citations':[]}] for k in sectioned.SECTIONS}}
    old['sections']['goal'] = [{'kind':'inferred','text':'Goal','citations':[{'execution_id':eid,'event_ref':r['events'][0]['event_ref']}]}]
    old['sections']['variants'] = [{'kind':'observed','text':'Variant','citations':[{'execution_id':eid,'event_ref':second['event_ref']}]}]
    oldpin = sectioned.review(s,pin,old)['review_sha256']
    return s,pin,old,oldpin


def test_exact_mapping_and_complete_prior_sections():
    args = fixture(); r = compat.prepare(*args)
    assert r['untrusted_prior_proposal'] == args[2]
    assert r['evidence_packet'] == integrated.prepare([args[0]])
    assert [x['section'] for x in r['section_reference_map']] == list(sectioned.SECTIONS)
    assert r['section_reference_map'][0]['scoped_refs'] == ['b1/execution:'+'a'*16+'/event:'+'b'*16]
    assert r['handoff_sha256'] == integrated._digest({k:v for k,v in r.items() if k!='handoff_sha256'})
    assert not r['semantic_conversion_performed'] and not r['external_sharing_authorized']


def test_review_flags_dropped_citation_without_claiming_semantic_preservation():
    args=fixture(); h=compat.prepare(*args); p=proposal([args[0]])
    r=compat.review(*args,h['handoff_sha256'],p)
    assert r['integrated_proposal_sha256']==integrated._digest(p)
    assert r['original_distinct_citation_count']==2 and r['retained_distinct_citation_count']==1
    variants=next(x for x in r['section_reference_review'] if x['section']=='variants')
    assert variants['not_cited_in_returned_proposal']==['b1/execution:'+'a'*16+'/event:'+'c'*16]
    assert not r['semantic_preservation_verified'] and not r['policy_preservation_verified'] and not r['portfolio_written']
    assert len(r['section_reference_review'])==12


@pytest.mark.parametrize('bad',['evidence','prior_review','prior_policy','handoff','invented_ref','wrong_packet','unresolved'])
def test_refuses_stale_or_invalid(bad):
    args=list(fixture()); h=compat.prepare(*args); p=proposal([args[0]]); pin=h['handoff_sha256']
    if bad=='evidence': args[1]='0'*64
    elif bad=='prior_review': args[3]='0'*64
    elif bad=='prior_policy': args[2]['sections']['policy'][0]['kind']='observed'
    elif bad=='handoff': pin='0'*64
    elif bad=='invented_ref': p['outcome']['evidence_refs']=['fabricated']
    elif bad=='wrong_packet': p['packet_sha256']='0'*64
    elif bad=='unresolved': p['questions']=[]
    with pytest.raises(ValueError): compat.review(*args,pin,p)


def test_cli_exact_files_and_no_overwrite(tmp_path):
    s,pin,old,oldpin=fixture()
    paths={k:tmp_path/k for k in ('s','old','new','handoff','report')}
    for k,obj in [('s',s),('old',old),('new',proposal([s]))]: paths[k].write_text(json.dumps(obj))
    base=['--source',str(paths['s']),'--sectioned-proposal',str(paths['old']),'--evidence-sha256',pin,'--sectioned-review-sha256',oldpin,'--confirm-local']
    assert compat.main(['prepare',*base,'--output',str(paths['handoff'])])==0
    h=json.loads(paths['handoff'].read_text()); assert h==compat.prepare(s,pin,old,oldpin)
    args=['review',*base,'--handoff-sha256',h['handoff_sha256'],'--integrated-proposal',str(paths['new']),'--output',str(paths['report'])]
    assert compat.main(args)==0
    with pytest.raises(SystemExit) as exc: compat.main(args)
    assert exc.value.code==2
