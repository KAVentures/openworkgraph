"""Descriptive structural metrics; no business-rule or outcome verification."""
from collections import defaultdict, deque
from heapq import heappop, heappush
from itertools import count

from .process_parallel import EdgeTokenModel, parallel_regions

MAX_PRECISION_WORK = 200_000


def minimum_route_activities(model):
    """Shortest visible route, consuming every branch of paired parallel regions."""
    nodes={node.id:node for node in model.activities}
    regions={r['split']:r for r in parallel_regions(model)}
    outgoing=defaultdict(list)
    for edge in model.transitions:outgoing[edge.source].append(edge.target)
    done=object()
    distances={model.entry_activity_id:0};serial=count()
    queue=[(0,next(serial),model.entry_activity_id)]
    while queue:
        distance,_,identifier=heappop(queue)
        if distance!=distances[identifier]:continue
        if identifier is done:return distance
        node=nodes[identifier]
        if identifier in model.terminal_activity_ids:
            targets=[(done,int(node.kind=='activity'))]
        elif identifier in regions:
            region=regions[identifier]
            cost=sum(nodes[a].kind=='activity' for branch in region['branches'] for a in branch)
            targets=[(region['join'],cost)]
        else:targets=[(target,int(node.kind=='activity')) for target in outgoing[identifier]]
        for target,cost in targets:
            candidate=distance+cost
            if candidate<distances.get(target,float('inf')):
                distances[target]=candidate;heappush(queue,(candidate,next(serial),target))
    raise ValueError('A complete model route is required')


def fitness_bounds(*, cases, events, analyzed_cases, analyzed_events, edit_cost, minimum_model_activities):
    """Cost-normalized log fitness; unknown cases contribute an interval, not a guess."""
    denominator=events+cases*minimum_model_activities
    known=analyzed_events+analyzed_cases*minimum_model_activities
    missing=denominator-known
    if not (minimum_model_activities>=0 and 0<=analyzed_cases<=cases and 0<=analyzed_events<=events and 0<=edit_cost<=known):
        raise ValueError('Fitness counts must describe a bounded analyzed population')
    return {
        'score':1-edit_cost/denominator if denominator and analyzed_cases==cases else None,
        'analyzed_score':1-edit_cost/known if known else None,
        'lower_bound':1-(edit_cost+missing)/denominator if denominator else None,
        'upper_bound':1-edit_cost/denominator if denominator else None,
        'minimum_model_activities':minimum_model_activities,
        'minimum_activity_edit_cost':edit_cost,'analyzed_events':analyzed_events,
        'unknown_events':events-analyzed_events,
        'normalization':'observed_events_plus_minimum_visible_model_route_per_case',
    }


class _BudgetReached(Exception):pass


def visible_prefix_precision(model, weighted_paths, *, max_work=None):
    """Weighted escaping-next-label precision over exact matching case prefixes.

    Replay retains all possible markings and distinct enabled visible labels.
    This is a visible-language metric, not transition identity or policy proof.
    Terminal states add no artificial end activity to the denominator.
    """
    limit=MAX_PRECISION_WORK if max_work is None else max_work
    if type(limit) is not int or limit<0:raise ValueError('A nonnegative precision work limit is required')
    paths=list(weighted_paths)
    if any(type(n) is not int or n<1 for _,n in paths):raise ValueError('Positive case support is required')
    support=sum(n for _,n in paths)
    base={'score':None,'matching_cases':support,'calculation_complete':False,
          'basis':'exact_matching_case_prefixes_visible_activity_labels','work_count':0,
          'enabled_label_visits':None,'escaping_label_visits':None}
    if not support:return base|{'unknown_reason':'no_exact_matching_cases'}
    work=0
    def charge():
        nonlocal work
        if work>=limit:raise _BudgetReached()
        work+=1
    try:
        # A trie shares observed prefixes but retains their full case weights.
        trie=[{'next':{},'visits':0,'ends':0}]
        for path,n in paths:
            cursor=0;trie[0]['visits']+=n
            for label in path:
                if label not in trie[cursor]['next']:
                    charge();trie[cursor]['next'][label]=len(trie)
                    trie.append({'next':{},'visits':0,'ends':0})
                cursor=trie[cursor]['next'][label];trie[cursor]['visits']+=n
            trie[cursor]['ends']+=n
        automaton=EdgeTokenModel(model)
        closures={};enabled_cache={}
        def closure(seeds):
            key=frozenset(seeds)
            if key in closures:return closures[key]
            reached=set();queue=deque()
            for state in key:
                charge();reached.add(state);queue.append(state)
            while queue:
                state=queue.popleft()
                for node,following in automaton.moves(state):
                    charge()
                    if node.kind!='activity' and following not in reached:
                        charge();reached.add(following);queue.append(following)
            result=frozenset(reached);closures[key]=result;return result
        def enabled(states):
            if states in enabled_cache:return enabled_cache[states]
            options=defaultdict(set)
            for state in states:
                for node,following in automaton.moves(state):
                    charge()
                    if node.kind=='activity':options[node.label].add(following)
            enabled_cache[states]=options;return options
        enabled_visits=escaping_visits=0
        queue=deque([(0,closure({automaton.initial}))])
        while queue:
            index,states=queue.popleft();observed=trie[index]
            if observed['ends'] and automaton.done not in states:
                return base|{'unknown_reason':'prefix_replay_mismatch','work_count':work}
            options=enabled(states);labels=set(options)
            if not set(observed['next']).issubset(labels):
                return base|{'unknown_reason':'prefix_replay_mismatch','work_count':work}
            enabled_visits+=observed['visits']*len(labels)
            escaping_visits+=observed['visits']*len(labels-set(observed['next']))
            for label,child in observed['next'].items():
                charge();queue.append((child,closure(options[label])))
        return base|{'score':1-escaping_visits/enabled_visits if enabled_visits else None,
                    'calculation_complete':True,'unknown_reason':None if enabled_visits else 'no_enabled_visible_activities',
                    'work_count':work,'enabled_label_visits':enabled_visits,'escaping_label_visits':escaping_visits}
    except _BudgetReached:
        return base|{'unknown_reason':'precision_work_budget','work_count':work}
