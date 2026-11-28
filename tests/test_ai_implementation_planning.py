import copy
import json
from fractions import Fraction

import pytest

from shared import ai_implementation_batch as batch
from shared import ai_implementation_review as contract
from shared import ai_implementation_planning as planning
from test_ai_implementation_batch import setup


def fixture():
    s, p, proposals = setup()
    r = batch.review_batch(s, p, proposals)
    args = s, p, proposals, r['batch_review_sha256']
    plan = planning.worklist(*args, 'SEK')
    return args, plan


def assumptions():
    return {'ranges': {'cases_per_month':['60','120'], 'eligible_share':['0.5','1'],
            'net_human_minutes_saved_per_eligible_case':['-6','12'],
            'human_capacity_value_per_hour':['100','200'],
            'incremental_operating_cost_per_month':['10','100'],
            'one_time_implementation_cost':['100','200']}, 'feasibility':'plausible','risk':'unknown',
            'depends_on_item_indexes':[], 'basis_note':'Reviewed hypothetical workload and cost bounds; no observed baseline.'}


def test_exact_intervals_negative_time_and_overlap():
    args, plan = fixture()
    plan['confirm_human_reviewed_assumptions'] = True
    plan['candidates'][0]['assessment'] = assumptions()
    plan['candidates'][1]['assessment'] = assumptions()
    plan['candidates'][1]['assessment']['depends_on_item_indexes'] = [0]
    r = planning.compare(*args, plan)
    a = r['candidates'][0]['assessment']
    assert a['modeled_net_human_hours_per_month'] == {'low':'-12','high':'24'}
    assert a['modeled_capacity_value_per_month'] == {'low':'-2400','high':'4800'}
    assert a['modeled_capacity_value_less_operating_cost_per_month'] == {'low':'-2500','high':'4790'}
    assert a['modeled_first_year_capacity_value_less_incremental_costs'] == {'low':'-30200','high':'57380'}
    assert r['item_count'] == 6 and r['assessed_count'] == 2
    assert r['shared_scoped_citations'] == [{'left_item_index':0,'right_item_index':2,'shared_scoped_citation_count':1}]
    assert r['portfolio_total'] is None and r['execution_authorized'] is False
    assert r['realized_savings_verified'] is False and r['opportunity_ranking_performed'] is False
    assert r['planning_review_sha256'] == contract._digest({k:v for k,v in r.items() if k != 'planning_review_sha256'})


def test_unknown_not_zero_and_dependency_visible():
    args, plan = fixture()
    plan['confirm_human_reviewed_assumptions'] = True
    plan['candidates'][1]['assessment'] = assumptions()
    a = plan['candidates'][1]['assessment']
    a['ranges']['human_capacity_value_per_hour'] = None
    a['depends_on_item_indexes'] = [0]
    r = planning.compare(*args, plan)
    assert r['assessed_count'] == 1 and r['candidates'][0]['assessment'] is None
    row = r['candidates'][2]
    assert row['assessment']['modeled_net_human_hours_per_month'] is not None
    assert row['assessment']['modeled_capacity_value_per_month'] is None
    assert row['assessment']['modeled_first_year_capacity_value_less_incremental_costs'] is None
    assert row['dependencies_without_plausible_assessment'] == [0]


def test_exact_fraction():
    args, plan = fixture()
    plan['confirm_human_reviewed_assumptions'] = True
    a = assumptions()
    a['ranges']['cases_per_month'] = ['1','1']
    a['ranges']['eligible_share'] = ['1','1']
    a['ranges']['net_human_minutes_saved_per_eligible_case'] = ['1','1']
    plan['candidates'][0]['assessment'] = a
    r = planning.compare(*args, plan)
    assert Fraction(r['candidates'][0]['assessment']['modeled_net_human_hours_per_month']['low']) == Fraction(1,60)


@pytest.mark.parametrize('bad', ['confirmation','pin','missing','duplicate','bad_range','float','dependency','cycle','currency','basis','unknown_field'])
def test_refuses_bad_inputs(bad):
    args, plan = fixture()
    plan['confirm_human_reviewed_assumptions'] = True
    plan['candidates'][0]['assessment'] = assumptions()
    a = plan['candidates'][0]['assessment']
    if bad == 'confirmation': plan['confirm_human_reviewed_assumptions'] = False
    elif bad == 'pin': plan['batch_review_sha256'] = '0'*64
    elif bad == 'missing': plan['candidates'].pop()
    elif bad == 'duplicate': plan['candidates'][1] = copy.deepcopy(plan['candidates'][0])
    elif bad == 'bad_range': a['ranges']['eligible_share'] = ['0.9','0.1']
    elif bad == 'float': a['ranges']['cases_per_month'] = [1.0,2.0]
    elif bad == 'dependency': a['depends_on_item_indexes'] = [1]  # rejected candidate
    elif bad == 'cycle':
        a['depends_on_item_indexes'] = [2]
        plan['candidates'][1]['assessment'] = assumptions()
        plan['candidates'][1]['assessment']['depends_on_item_indexes'] = [0]
    elif bad == 'currency': plan['currency'] = 'sek'
    elif bad == 'basis': a['basis_note'] = ''
    elif bad == 'unknown_field': a['ranges']['cash_savings'] = ['1','2']
    with pytest.raises(ValueError): planning.compare(*args, plan)


def test_cli_exclusive_and_canonical(tmp_path):
    args, plan = fixture()
    s,p,proposals,pin = args
    source, items, work, output = [tmp_path/f'{name}.json' for name in ('source','items','work','output')]
    source.write_text(json.dumps(s[0])); items.write_text(json.dumps(proposals))
    base = ['--source',str(source),'--packet-sha256',p,'--proposals',str(items),'--batch-review-sha256',pin,'--confirm-local']
    assert planning.main(['worklist',*base,'--currency','SEK','--output',str(work)]) == 0
    assert json.loads(work.read_text()) == plan
    plan['confirm_human_reviewed_assumptions'] = True
    work.write_text(json.dumps(plan))
    compare = ['compare',*base,'--plan',str(work),'--output',str(output)]
    assert planning.main(compare) == 0
    assert output.read_bytes() == contract._json(planning.compare(*args,plan))
    with pytest.raises(SystemExit) as exc: planning.main(compare)
    assert exc.value.code == 2
