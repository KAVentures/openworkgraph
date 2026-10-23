"""Independent language oracles and bounded structural metric semantics."""
from itertools import combinations

import pytest
from shared.process_model import ProcessModel
from shared.process_metrics import minimum_route_activities, fitness_bounds, visible_prefix_precision
from test_process_catalog import model, branched_model
from test_parallel_process_model import parallel_model


def language_precision(language, observations):
    # Independent finite-word oracle, with no graph marking/token operations.
    visits={};observed={}
    for word,n in observations:
        for length in range(len(word)+1):
            prefix=word[:length]
            visits[prefix]=visits.get(prefix,0)+n
            observed.setdefault(prefix,set())
            if length<len(word):observed[prefix].add(word[length])
    denominator=escaping=0
    for prefix,n in visits.items():
        enabled={word[len(prefix)] for word in language if len(word)>len(prefix) and word[:len(prefix)]==prefix}
        denominator+=n*len(enabled);escaping+=n*len(enabled-observed[prefix])
    return 1-escaping/denominator if denominator else None


def test_shortest_routes_include_every_parallel_branch_and_terminal_activity():
    assert minimum_route_activities(model())==3
    assert minimum_route_activities(branched_model())==3
    assert minimum_route_activities(parallel_model())==5
    # A zero-cost end must beat an activity terminal even when the latter is
    # visited first. Costs include consumption of the terminal activity.
    optional=ProcessModel(goal='Perform the optional action',baseline_source='user_declared',baseline_path=['A'],
        activities=[{'id':'d','label':'Choose','kind':'decision'},{'id':'a','label':'A'},
                    {'id':'end','label':'Done','kind':'end'}],
        transitions=[{'source':'d','target':'a','rule_id':'act'},{'source':'d','target':'end','rule_id':'stop'}],
        business_rules=[{'id':'act','text':'Act'},{'id':'stop','text':'Stop'}],
        entry_activity_id='d',terminal_activity_ids=['a','end'])
    assert minimum_route_activities(optional)==0


def test_fitness_is_cost_normalized_and_unknown_cases_give_bounds():
    full=fitness_bounds(cases=2,events=5,analyzed_cases=2,analyzed_events=5,edit_cost=1,minimum_model_activities=3)
    assert full['score']==full['lower_bound']==full['upper_bound']==1-1/11
    partial=fitness_bounds(cases=3,events=9,analyzed_cases=2,analyzed_events=5,edit_cost=1,minimum_model_activities=3)
    assert partial['score'] is None and partial['analyzed_score']==1-1/11
    assert partial['lower_bound']==1-8/18 and partial['upper_bound']==1-1/18
    assert partial['unknown_events']==4
    empty=fitness_bounds(cases=0,events=0,analyzed_cases=0,analyzed_events=0,edit_cost=0,minimum_model_activities=3)
    assert empty['score'] is empty['lower_bound'] is empty['upper_bound'] is None
    with pytest.raises(ValueError):fitness_bounds(cases=1,events=1,analyzed_cases=1,analyzed_events=1,edit_cost=5,minimum_model_activities=1)


def test_exclusive_precision_penalizes_unobserved_alternatives_without_policy_claims():
    graph=branched_model()
    language={('Open','Review','Close'),('Open','Review','Reject')}
    for observations in [[(('Open','Review','Close'),7)],[(word,i+1) for i,word in enumerate(sorted(language))]]:
        actual=visible_prefix_precision(graph,observations)
        assert actual['calculation_complete'] and actual['unknown_reason'] is None
        assert actual['score']==pytest.approx(language_precision(language,observations))
    assert visible_prefix_precision(graph,[(('Open','Review','Close'),7)])['score']<1
    assert visible_prefix_precision(model(),[(('Open','Review','Close'),7)])['score']==1


def test_parallel_precision_matches_all_interleavings_of_a_finite_language():
    language=set()
    for positions in combinations(range(4),2):
        a=iter(('A1','A2'));b=iter(('B1','B2'))
        language.add(tuple(next(a) if i in positions else next(b) for i in range(4))+('Close',))
    words=sorted(language)
    for size in range(1,len(words)+1):
        for chosen in combinations(words,size):
            observations=[(word,i+1) for i,word in enumerate(chosen)]
            actual=visible_prefix_precision(parallel_model(),observations)
            assert actual['calculation_complete']
            assert actual['score']==pytest.approx(language_precision(language,observations))
    assert visible_prefix_precision(parallel_model(),[(word,1) for word in words])['score']==1


def test_duplicate_labels_keep_possible_markings_without_assigning_node_identity():
    value=parallel_model().model_dump()
    for node in value['activities']:
        if node['kind']=='activity' and node['id']!='close':node['label']='Check'
    graph=ProcessModel.model_validate(value)
    assert minimum_route_activities(graph)==5
    result=visible_prefix_precision(graph,[(('Check','Check','Check','Check','Close'),9)])
    assert result['score']==1 and result['matching_cases']==9
    assert result['basis']=='exact_matching_case_prefixes_visible_activity_labels'


def test_budget_exhaustion_never_returns_optimistic_partial_precision():
    path=('Open','Review','Close')
    complete=visible_prefix_precision(model(),[(path,3)])
    for budget in [0,1,complete['work_count']-1]:
        result=visible_prefix_precision(model(),[(path,3)],max_work=budget)
        assert not result['calculation_complete'] and result['score'] is None
        assert result['unknown_reason']=='precision_work_budget'
        assert result['matching_cases']==3 and result['work_count']<=budget
    assert visible_prefix_precision(model(),[(path,3)],max_work=complete['work_count'])['score']==1


def test_nonmatching_paths_and_missing_population_have_no_precision_verdict():
    assert visible_prefix_precision(model(),[])['unknown_reason']=='no_exact_matching_cases'
    wrong=visible_prefix_precision(model(),[(('Open','Close'),1)])
    assert wrong['score'] is None and wrong['unknown_reason']=='prefix_replay_mismatch'


def test_exclusive_loop_preserves_visible_language_prefixes():
    graph=ProcessModel(goal='Repeat a declared check',baseline_source='user_declared',baseline_path=['A','Close'],
        activities=[{'id':'a','label':'A'},{'id':'d','label':'Again','kind':'decision'},{'id':'end','label':'Close'}],
        transitions=[{'source':'a','target':'d'},{'source':'d','target':'a','rule_id':'again'},{'source':'d','target':'end','rule_id':'finish'}],
        business_rules=[{'id':'again','text':'Repeat'},{'id':'finish','text':'Finish'}],entry_activity_id='a',terminal_activity_ids=['end'])
    assert minimum_route_activities(graph)==2
    observations=[(('A','Close'),4),(('A','A','Close'),2)]
    # Longer language words establish the enabled repeat at the longest observed
    # prefix; unseen future prefixes do not enter the observed denominator.
    language={('A',)*n+('Close',) for n in range(1,5)}
    assert visible_prefix_precision(graph,observations)['score']==pytest.approx(language_precision(language,observations))
