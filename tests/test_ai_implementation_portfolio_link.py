import copy
import pytest
from shared import ai_implementation_batch as batch
from shared import ai_implementation_planning as planning
from shared import ai_implementation_review as contract
from shared import ai_implementation_portfolio_link as link
from test_ai_implementation_planning import fixture, assumptions


def data():
    args, plan = fixture()
    plan['confirm_human_reviewed_assumptions'] = True
    for c in plan['candidates']: c['assessment'] = assumptions()
    plan['candidates'][1]['assessment']['depends_on_item_indexes'] = [0]
    r = planning.compare(*args, plan)
    return (*args, plan, r['planning_review_sha256'])


def idea(args, idx, n):
    s,p,proposals,rpin,*_ = args
    row = batch.review_batch(s,p,proposals)['items'][idx]
    pin = row['proposal_sha256']
    return {'idea_id':f'idea_{n:016x}', 'revision':2,'state':'assessing',
            'source':{'kind':'manual','reference':f'ai-proposal:{p}:{pin}'},
            'ai_proposal_handoff':{'format':'openworkgraph.ai-opportunity-handoff.v1','packet_sha256':p,
                'proposal_sha256':pin,'structural_review_sha256':row['review_sha256'],'structural_review_only':True,
                'semantic_correctness_verified':False,'source_authenticity_verified':False,'execution_authorized':False,'preview_sha256':'a'*64},
            'planning':{'budget':{'amount':150,'basis':'estimate','currency':'SEK'},'depends_on':[]},'assessment':None}


def test_match_dependencies_and_full_population():
    args=data(); a,b=idea(args,0,1),idea(args,2,2)
    b['planning']['depends_on']=[a['idea_id']]
    r=link.reconcile(*args,[b,a]); assert r['matched_count']==2 and r['item_count']==6
    assert r['items'][2]['dependency_comparison']['exactly_matches_declared_dependencies']
    assert r['items'][0]['budget_comparison']=='within_scenario_range'
    assert r['items'][1]['mapping_status']=='not_a_distinct_reviewed_candidate'
    assert r['reconciliation_sha256']==contract._digest({k:v for k,v in r.items() if k!='reconciliation_sha256'})
    assert r==link.reconcile(*args,[a,b]); assert not r['portfolio_written'] and not r['execution_authorized']


def test_missing_manual_and_dependency():
    args=data(); a,b=idea(args,0,1),idea(args,2,2)
    del a['ai_proposal_handoff']; b['planning']['depends_on']=[a['idea_id']]
    r=link.reconcile(*args,[a,b]); assert r['items'][0]['mapping_status']=='not_recorded'
    d=r['items'][2]['dependency_comparison']; assert d['unresolved_candidate_indexes']==[0] and d['saved_links_outside_scenario']==[a['idea_id']]
    assert not d['exactly_matches_declared_dependencies']


def test_conflicting_and_duplicate_lineage():
    args=data(); a,b,c=idea(args,0,1),idea(args,0,2),idea(args,2,3)
    c['ai_proposal_handoff']['structural_review_sha256']='0'*64
    r=link.reconcile(*args,[a,b,c]); assert r['items'][0]['mapping_status']=='ambiguous_saved_lineage'
    assert r['items'][2]['mapping_status']=='lineage_conflict' and r['matched_count']==0


@pytest.mark.parametrize('amount,currency,status',[(99,'SEK','below_scenario_range'),(201,'SEK','above_scenario_range'),(150,'EUR','currency_mismatch'),(150.25,'SEK','within_scenario_range')])
def test_budget(amount,currency,status):
    args=data(); a=idea(args,0,1);a['planning']['budget'].update(amount=amount,currency=currency)
    assert link.reconcile(*args,[a])['items'][0]['budget_comparison']==status


def test_stale_plan_and_snapshot_change():
    args=data();a=idea(args,0,1);r=link.reconcile(*args,[a]);a['revision']+=1
    assert link.reconcile(*args,[a])['reconciliation_sha256']!=r['reconciliation_sha256']
    with pytest.raises(ValueError):link.reconcile(*args[:-1],'0'*64,[a])
    with pytest.raises(ValueError):link.reconcile(*args,[a,copy.deepcopy(a)])
