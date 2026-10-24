"""Case age and new-case evidence remain explicit when history is bounded."""
from datetime import datetime
from itertools import combinations
import json

import pytest

from server import event_log_signals as signals, event_log_workspace as workspace
from test_event_log_signals import _log, _rule


@pytest.fixture(autouse=True)
def data(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def open_rule(log):
    signals.add_rule(log,_rule('Open age',kind='cases',condition='open_longer_than',seconds=48*3600,end_activities=['Close']))


def test_open_case_age_uses_first_retained_event_even_when_work_continues():
    log=_log({'old_recent':[('Receive',0),('Check',99)],'closed':[('Receive',0),('Close',99)],
              'recent':[('Receive',90),('Check',99)],'clock':[('Receive',100)]})
    open_rule(log);result=signals.evaluate(log)['signals'][0]
    assert result['matching_cases']==1
    case=result['evidence_cases'][0]
    assert case['case_id']=='old_recent'
    assert case['age_since_first_event_seconds']==100*3600
    assert case['idle_since_last_event_seconds']==3600
    assert case['duration_seconds']==99*3600
    assert case['first_event_at']=='2024-01-01T00:00:00Z'


def test_last_activity_at_timestamp_ties_follows_retained_event_order():
    log=_log({'closed_last':[('Receive',0),('Check',99),('Close',99)],
              'check_last':[('Receive',0),('Close',99),('Check',99)],'clock':[('Receive',100)]})
    open_rule(log);result=signals.evaluate(log)['signals'][0]
    assert [case['case_id'] for case in result['evidence_cases']]==['check_last']


def test_open_age_threshold_uses_elapsed_time_including_fractional_seconds():
    log=_log({'just_over':[('Receive',52-0.5/3600),('Check',99)],
              'exact':[('Receive',52),('Check',99)],
              'just_under':[('Receive',52+0.5/3600),('Check',99)],'clock':[('Receive',100)]})
    open_rule(log);result=signals.evaluate(log)['signals'][0]
    assert [case['case_id'] for case in result['evidence_cases']]==['just_over']
    assert result['evidence_cases'][0]['age_since_first_event_seconds']==48*3600+0.5


def test_sample_rank_changes_do_not_report_previously_unretained_cases_as_new(monkeypatch):
    monkeypatch.setattr(signals,'MAX_REMEMBERED_CASES',2)
    log=_log({'A':[('Start',0),('Match',3)],'B':[('Start',0),('Match',2)],'C':[('Start',0),('Match',1)]})
    signals.add_rule(log,_rule('Matches',kind='cases',condition='contains_activity',activity='Match'))
    first=signals.evaluate(log)['signals'][0]
    assert first['new_cases_comparison']['reason']=='first_check'
    conn=workspace._connect()
    conn.execute("UPDATE events SET ts=? WHERE log_id=? AND activity='Match' AND event_id IN "
                 "(SELECT event_id FROM event_objects WHERE log_id=? AND object_id='C')",
                 [datetime(2024,1,1,4),log,log]);conn.close()
    second=signals.evaluate(log)['signals'][0]
    assert second['matching_cases']==3 and second['new_cases_since_last_evaluation'] is None
    compared=second['new_cases_comparison']
    assert not compared['comparison_complete'] and compared['reason']=='retained_case_limit'
    assert compared['lower_bound']==0 and compared['upper_bound']==2
    assert compared['current_retained_cases']==2 and compared['previous_matching_cases']==3


def test_bounds_cover_true_set_difference_for_every_small_population_and_retained_sample():
    universe={'A','B','C','D','E'}
    populations=[set(items) for size in range(6) for items in combinations(sorted(universe),size)]
    samples=[(whole,set(sample)) for whole in populations for sample in combinations(sorted(whole),min(len(whole),2))]
    for before,prior in samples:
        for current,retained in samples:
            value=signals._new_case_comparison(len(current),list(retained),{'matching_cases':len(before),'case_ids':list(prior)})
            actual=len(current-before)
            assert value['lower_bound']<=actual<=value['upper_bound']
            if value['comparison_complete']:assert value['count']==actual
            else:assert value['count'] is None


def test_legacy_case_history_keeps_unknown_prior_population_until_next_check():
    log=_log({'A':[('Start',0)],'B':[('Start',1)]})
    signals.add_rule(log,_rule('Starts',kind='cases',condition='contains_activity',activity='Start'))
    signals.evaluate(log)
    conn=workspace._connect()
    conn.execute('UPDATE signal_rules SET last_state=? WHERE log_id=?',[json.dumps({'triggered':True,'case_ids':['A']}),log])
    conn.close()
    result=signals.evaluate(log)['signals'][0]
    assert result['new_cases_since_last_evaluation'] is None
    assert result['new_cases_comparison']['reason']=='previous_population_unknown'
    assert result['new_cases_comparison']['previous_matching_cases'] is None
    assert signals.evaluate(log)['signals'][0]['new_cases_since_last_evaluation']==0


@pytest.mark.parametrize('value',[float('inf'),float('-inf'),float('nan')])
def test_nonfinite_signal_thresholds_are_refused_before_storage(value):
    log=_log()
    with pytest.raises(workspace.EventLogWorkspaceError):
        signals.add_rule(log,_rule('Invalid threshold',kind='kpi',metric='case_count',operator='above',threshold=value))
    assert signals.list_rules(log)['rules']==[]
