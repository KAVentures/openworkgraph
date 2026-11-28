import copy
import pytest
from shared import ai_implementation_forecast_review as review
from shared import ai_implementation_portfolio_link as link
from shared import ai_implementation_review as contract
from test_ai_implementation_portfolio_link import data, idea


def fixture():
    args = data(); target = idea(args, 0, 1); target['state'] = 'implemented'
    pin = link.reconcile(*args, [target])['reconciliation_sha256']
    quality = {'format': review.QUALITY, 'idea_id': target['idea_id'], 'idea_revision': target['revision'],
               'snapshot_sha256': 'a'*64, 'scenario_sha256': 'b'*64, 'currency': 'SEK',
               'selected_measurement_ids': ['meas_0000000000000001', 'meas_0000000000000002'],
               'excluded_measurement_ids': ['meas_0000000000000003'],
               'after': {'cases': 100, 'eligible': 80},
               'financial_scenario': {'after_monthly_case_volume': 100, 'effort_change_with_extra_review_hours_at_after_volume': 25,
                                      'nonlabor_cost_change_at_after_volume': -40, 'modeled_net_monthly_value_change': 2460},
               'assumptions': {'snapshot_sha256':'a'*64, 'currency':'SEK', 'labor_cost_per_hour':100, 'one_time_cost':150},
               'review_gates': [{'gate':'quality_failure_rate_change','status':'above_threshold'}],
               'all_declared_gates_met': False, 'whole_population_quality': {'threshold_status':'unresolved'},
               **{k:False for k in review.CLAIMS}}
    comparable = {**{k:True for k in review.BASES}, 'basis_note':'Reviewed same scope; hypothetical test.'}
    return args,[target],pin,quality,comparable


def compare(f):
    args,ideas,pin,quality,comparable=f
    return review.compare(args,ideas,pin,0,quality,'b'*64,comparable)


def test_exact_lineage_and_variance_with_quality_failure():
    r=compare(fixture()); metrics={m['metric']:m for m in r['comparisons']}
    assert metrics['monthly_case_volume']['status']=='within_forecast_range'
    assert metrics['eligible_share']['reported_value']=='4/5'
    assert metrics['monthly_net_human_hours']['status']=='above_forecast_range'
    assert metrics['monthly_incremental_nonlabor_cost']['reported_value']=='40'
    assert metrics['monthly_incremental_nonlabor_cost']['status']=='within_forecast_range'
    assert metrics['one_time_implementation_cost']['status']=='within_forecast_range'
    assert not r['all_declared_gates_met'] and r['whole_population_quality_threshold_status']=='unresolved'
    assert len(r['excluded_measurement_ids'])==1 and not r['forecast_recorded_before_implementation_verified']
    assert not r['savings_claimed'] and not r['portfolio_written']
    assert r['forecast_review_sha256']==contract._digest({k:v for k,v in r.items() if k!='forecast_review_sha256'})


def test_currency_and_comparability_are_not_zero():
    f=fixture();f[3]['currency']=f[3]['assumptions']['currency']='EUR';f[4]['net_human_effort']=False
    r=compare(f);m={x['metric']:x for x in r['comparisons']}
    assert m['monthly_net_human_hours']['status']=='comparability_not_confirmed'
    assert m['monthly_incremental_nonlabor_cost']['status']=='currency_mismatch'
    assert m['monthly_conditional_net_value']['status']=='currency_mismatch'
    assert m['monthly_case_volume']['status']=='within_forecast_range'


@pytest.mark.parametrize('bad',['revision','idea','quality_pin','claim','state','snapshot','denominator','excluded','comparability','portfolio'])
def test_refuses_stale_or_invalid(bad):
    f=list(fixture());args,ideas,pin,q,c=f
    if bad=='revision': q['idea_revision']+=1
    elif bad=='idea':q['idea_id']='idea_0000000000000002'
    elif bad=='quality_pin':q['scenario_sha256']='c'*64
    elif bad=='claim':q['savings_claimed']=True
    elif bad=='state':
        ideas[0]['state']='approved';f[2]=link.reconcile(*args,ideas)['reconciliation_sha256']
    elif bad=='snapshot':q['snapshot_sha256']='c'*64
    elif bad=='denominator':q['after']['eligible']=101
    elif bad=='excluded':q['excluded_measurement_ids']=q['selected_measurement_ids'][:1]
    elif bad=='comparability':c['workload']=1
    elif bad=='portfolio':ideas[0]['revision']+=1
    with pytest.raises(ValueError):compare(f)
