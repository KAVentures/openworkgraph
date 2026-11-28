import copy
import pytest
from shared import ai_implementation_portfolio_handoff as handoff
from shared import ai_implementation_portfolio_link as link
from shared import ai_implementation_planning as planning
from shared import ai_implementation_review as contract
from test_ai_implementation_portfolio_link import data, idea


def fixture():
    args=data(); a,b=idea(args,0,1),idea(args,2,2)
    b['planning'].update(owner='Owner',team='Team',target_quarter='2027-Q1',budget=None,depends_on=['idea_0000000000000003'])
    return args,[a,b,{'idea_id':'idea_0000000000000003','revision':1,'state':'idea'}]


def preview(args, ideas, estimate='150.25', index=2):
    pin=link.reconcile(*args,ideas)['reconciliation_sha256']
    return handoff.preview(args,ideas,pin,index,estimate)


def test_exact_preview_preserves_fields_and_external_links():
    args, ideas=fixture(); original=copy.deepcopy(ideas); r=preview(args,ideas)
    assert r['proposed_planning']=={'owner':'Owner','team':'Team','target_quarter':'2027-Q1',
        'budget':{'amount':150.25,'currency':'SEK','basis':'estimate'},
        'depends_on':['idea_0000000000000001','idea_0000000000000003'],'expected_revision':2}
    assert r['added_dependency_idea_ids']==['idea_0000000000000001']
    assert r['preview_sha256']==contract._digest({k:v for k,v in r.items() if k!='preview_sha256'})
    assert ideas==original and not r['portfolio_written'] and not r['execution_authorized']


def test_dependency_only_preserves_budget():
    args,ideas=fixture(); ideas[1]['planning']['budget']={'amount':400,'currency':'EUR','basis':'approved_budget'}
    r=preview(args,ideas,None)
    assert r['proposed_planning']['budget']==ideas[1]['planning']['budget']
    with pytest.raises(ValueError):preview(args,ideas)


@pytest.mark.parametrize('bad',['approved','implemented','rejected','missing','conflict','dependency_rejected','range','duplicate','no_op','wrong_type','stale'])
def test_refuses_unsafe_or_stale_preview(bad):
    args,ideas=fixture(); pin=link.reconcile(*args,ideas)['reconciliation_sha256']; estimate='150'; index=2
    if bad in {'approved','implemented','rejected'}:ideas[1]['state']=bad
    elif bad=='missing':ideas.pop(0)
    elif bad=='conflict':ideas[1]['ai_proposal_handoff']['structural_review_sha256']='0'*64
    elif bad=='dependency_rejected':ideas[0]['state']='rejected'
    elif bad=='range':estimate='201'
    elif bad=='duplicate':index=3
    elif bad=='no_op':ideas[1]['planning']['depends_on'].append(ideas[0]['idea_id']);estimate=None
    elif bad=='wrong_type':estimate=150
    elif bad=='stale':ideas[1]['revision']+=1
    if bad!='stale':pin=link.reconcile(*args,ideas)['reconciliation_sha256']
    with pytest.raises((ValueError,TypeError)):handoff.preview(args,ideas,pin,index,estimate)


def test_nonplausible_refusal():
    args,ideas=fixture(); plan=args[-2]; plan['candidates'][1]['assessment']['feasibility']='blocked'
    args=(*args[:-1],planning.compare(*args[:-1])['planning_review_sha256'])
    with pytest.raises(ValueError):preview(args,ideas)
