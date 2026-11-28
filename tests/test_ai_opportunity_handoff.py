from copy import deepcopy
import json
import pytest
from shared.ai_opportunity_handoff import prepare, confirm, digest


def payload():
    return {'sources': [{'private': 'EVIDENCE_PRIVATE'}], 'proposal': {'packet_sha256': 'a'*64,
            'text': 'MODEL_PRIVATE'}, 'title': 'Human reviewed idea', 'description': 'A candidate, not an approval'}


def review(sources, proposal):
    assert sources[0]['private'] == 'EVIDENCE_PRIVATE'
    return {'format': 'openworkgraph.ai-implementation-review.v1', 'status': 'structural_review_only',
            'packet_sha256': proposal['packet_sha256'], 'proposal_sha256': digest(proposal),
            'human_review_required': True, 'semantic_correctness_verified': False,
            'source_authenticity_verified': False, 'deployment_verified': False,
            'execution_authorized': False, 'spending_authorized': False, 'coverage_gaps': [],
            'unresolved_question_count': 2}


def test_prepare_and_explicit_confirmation():
    p = payload(); v = prepare(p, reviewer=review)
    assert v['state_on_create'] == 'idea' and v['assessment_on_create'] is None
    assert v['idea']['source']['kind'] == 'manual'
    assert v['idea']['source']['reference'] == 'ai-proposal:'+'a'*64+':'+digest(p['proposal'])
    assert 'PRIVATE' not in json.dumps(v)
    assert v['preview_sha256'] == digest({k:x for k,x in v.items() if k != 'preview_sha256'})
    idea, lineage = confirm({'draft':p,'expected_preview_sha256':v['preview_sha256'],'confirm_create_idea':True}, reviewer=review)
    assert idea == v['idea'] and lineage['preview_sha256'] == v['preview_sha256']
    assert not lineage['execution_authorized']


def test_stale_review_and_human_text():
    p = payload(); pin = prepare(p, reviewer=review)['preview_sha256']
    for key, value in [('title','Changed'),('description','Changed'),('proposal',{'packet_sha256':'a'*64,'text':'Changed'})]:
        q = deepcopy(p); q[key] = value
        with pytest.raises(ValueError,match='Preview changed'):
            confirm({'draft':q,'expected_preview_sha256':pin,'confirm_create_idea':True}, reviewer=review)
    for title in ('','  ','x'*201,'x\x00'):
        q = deepcopy(p); q['title'] = title
        with pytest.raises(ValueError,match='human-entered'):
            prepare(q, reviewer=review)


def test_refuses_permission_and_external_review():
    p = payload(); v = prepare(p, reviewer=review)
    for flag in (False, 1, None):
        with pytest.raises(ValueError,match='confirmation'):
            confirm({'draft':p,'expected_preview_sha256':v['preview_sha256'],'confirm_create_idea':flag}, reviewer=review)
    for field in ('execution_authorized','semantic_correctness_verified','spending_authorized'):
        def unsafe(s, prop): return {**review(s,prop),field:True}
        with pytest.raises(ValueError,match='Unexpected'):
            prepare(p, reviewer=unsafe)
    with pytest.raises(ValueError,match='Supply sources'):
        prepare({**p,'review':v['review']}, reviewer=review)
    def wrong(s, prop): return {**review(s,prop),'proposal_sha256':'f'*64}
    with pytest.raises(ValueError,match='pin mismatch'):
        prepare(p, reviewer=wrong)
