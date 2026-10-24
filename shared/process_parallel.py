"""Explicit bounded fork/join regions and structural token alignment.

Supplied event order never proves simultaneity, business policy or outcomes.
"""
from collections import defaultdict
from heapq import heappop, heappush
from itertools import count

PARALLEL_KINDS = {'parallel_split', 'parallel_join'}
MAX_ALIGNMENT_STATES = 100_000
MAX_BATCH_ALIGNMENT_STATES = 200_000


def parallel_regions(model):
    """Require disjoint linear branches between explicit paired gateways.

    Nested, conditional, escaping and partially joined regions are unsupported;
    none may be silently interpreted as exclusive routing.
    """
    nodes = {node.id: node for node in model.activities}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in model.transitions:
        outgoing[edge.source].append(edge.target);incoming[edge.target].append(edge.source)
        if nodes[edge.source].kind in PARALLEL_KINDS and edge.rule_id:
            raise ValueError('Parallel gateway transitions cannot have conditional rules')
    regions, paired, used = [], set(), set()
    for node in model.activities:
        if node.kind != 'parallel_split':continue
        if node.id==model.entry_activity_id and incoming[node.id]:
            raise ValueError('A parallel entry split requires no incoming transition; declare a separate process entry for loops')
        if len(incoming[node.id]) > 1:
            raise ValueError('Parallel splits require a single entry; merge exclusive arrivals explicitly first')
        if not 2 <= len(outgoing[node.id]) <= 8:
            raise ValueError('Parallel splits require 2–8 unconditional branches')
        paths, joins = [], []
        for first in outgoing[node.id]:
            path=[];cursor=first
            while nodes[cursor].kind != 'parallel_join':
                if (cursor in path or cursor in used or nodes[cursor].kind not in {'activity','routing'}
                        or len(outgoing[cursor]) != 1 or len(incoming[cursor]) != 1):
                    raise ValueError('Parallel branches must be disjoint linear paths without nested or escaping routing')
                path.append(cursor);cursor=outgoing[cursor][0]
            paths.append(path);joins.append(cursor)
        if len(set(joins)) != 1 or joins[0] in paired:
            raise ValueError('Every parallel split requires its own common join')
        join=joins[0]
        ends={path[-1] if path else node.id for path in paths}
        if set(incoming[join]) != ends or len(incoming[join]) != len(paths):
            raise ValueError('Parallel joins must consume exactly the paired branch arrivals')
        flattened=[identifier for path in paths for identifier in path]
        if model.entry_activity_id in flattened:
            raise ValueError('A parallel process cannot enter a branch before its paired split; declare a separate process entry')
        if len(set(flattened)) != len(flattened):
            raise ValueError('Parallel branches must not share activities before their join')
        if any(edge.source in flattened and edge.rule_id for edge in model.transitions):
            raise ValueError('Parallel branch transitions cannot carry conditional rules')
        used.update(flattened);paired.add(join)
        regions.append({'split':node.id,'join':join,'branches':paths})
    for node in model.activities:
        if node.kind == 'parallel_join' and (node.id not in paired or node.id==model.entry_activity_id or len(outgoing[node.id]) != 1
                                            or node.id in model.terminal_activity_ids):
            raise ValueError('Parallel joins require one paired split and one continuation')
        if node.kind == 'parallel_split' and node.id in model.terminal_activity_ids:
            raise ValueError('Parallel splits cannot be terminal')
    return regions


class _BudgetReached(Exception):pass


class EdgeTokenModel:
    """Shared visible-activity semantics for alignment and prefix replay."""
    def __init__(self, model):
        parallel_regions(model)
        self.nodes={node.id:node for node in model.activities}
        self.incoming,self.outgoing=defaultdict(list),defaultdict(list)
        for place,edge in enumerate(model.transitions):
            self.incoming[edge.target].append(place)
            self.outgoing[edge.source].append(place)
        self.initial=frozenset({-1})
        self.done=frozenset({-2})
        self.incoming[model.entry_activity_id].append(-1)
        for terminal in model.terminal_activity_ids:self.outgoing[terminal]=[-2]

    def moves(self, marking):
        for identifier,node in self.nodes.items():
            arrived=[place for place in self.incoming[identifier] if place in marking]
            if not arrived:continue
            if node.kind=='parallel_join':
                if len(arrived)!=len(self.incoming[identifier]):continue
                consumptions=[frozenset(arrived)]
            else:consumptions=[frozenset({place}) for place in arrived]
            emissions=([frozenset(self.outgoing[identifier])] if node.kind=='parallel_split'
                       else [frozenset({place}) for place in self.outgoing[identifier]])
            for consumed in consumptions:
                for emitted in emissions:
                    yield node,(marking-consumed)|emitted


def compare_parallel_path(path, model, *, max_states=None):
    """Minimum insert/delete alignment over edge-token markings, with a budget.

    Markings preserve separate arrivals at joins. Disjoint paired regions cannot
    create duplicate tokens on an edge. Exclusive routing selects one successor;
    a parallel split emits all branch tokens and its join consumes all arrivals.
    """
    if len(path)>5000:raise ValueError('Conformance path exceeds the 5,000 event limit')
    if max_states is not None and (type(max_states) is not int or max_states<0):
        raise ValueError('A nonnegative bounded alignment budget is required')
    automaton=EdgeTokenModel(model)
    limit=MAX_ALIGNMENT_STATES if max_states is None else min(MAX_ALIGNMENT_STATES,max_states)
    if type(limit) is not int or limit<0:raise ValueError('A nonnegative bounded alignment budget is required')
    start=(0,automaton.initial);target=(len(path),automaton.done)
    distances={start:0};previous={};serial=count();queue=[(0,next(serial),start)]
    prefix=False
    common={'alignment_semantics':'explicit_structured_parallel_edge_tokens',
            'business_rules_evaluated':False,'policy_compliance_verified':False,'business_outcome_verified':False}
    def push(state,cost,before,operation):
        if cost >= distances.get(state,float('inf')):return
        if state not in distances and len(distances)>=limit:raise _BudgetReached()
        distances[state]=cost;previous[state]=(before,operation)
        heappush(queue,(cost,next(serial),state))
    try:
        if limit<1:
            distances.clear();raise _BudgetReached()
        while queue:
            cost,_,state=heappop(queue)
            if distances[state]!=cost:continue
            index,marking=state
            if state==target:break
            if index==len(path) and cost==0 and marking!=automaton.done:prefix=True
            if index<len(path):push((index+1,marking),cost+1,state,
                {'type':'log_only','log_position':index+1,'activity':path[index]})
            for node,following in automaton.moves(marking):
                if node.kind!='activity':push((index,following),cost,state,None)
                else:
                    if index<len(path) and node.label==path[index]:
                        push((index+1,following),cost,state,{'type':'matched','log_position':index+1,
                            'activity':path[index],'model_activity_id':node.id})
                    push((index,following),cost+1,state,
                        {'type':'model_only','model_activity_id':node.id,'activity':node.label})
    except _BudgetReached:
        return common|{'comparison_complete':False,'comparison_reason':'alignment_state_budget_reached',
            'alignment_state_count':len(distances),'exact_structural_match':None,'minimum_activity_edit_count':None,
            'could_be_incomplete_log_prefix':None,'alignment':[],'alignment_truncated':False,
            'alignment_basis':'incomplete_bounded_search_no_match_or_deviation_established'}
    if target not in distances:
        # Valid paired models have a complete route. Never fabricate a verdict
        # if an unexpected marking cannot reach completion.
        raise ValueError('Parallel alignment could not reach a complete process marking')
    operations=[];cursor=target
    while cursor!=start:
        cursor,operation=previous[cursor]
        if operation:operations.append(operation)
    operations.reverse()
    return common|{'comparison_complete':True,'alignment_state_count':len(distances),
        'exact_structural_match':distances[target]==0,'minimum_activity_edit_count':distances[target],
        'could_be_incomplete_log_prefix':distances[target]>0 and prefix,
        'alignment':operations[:80],'alignment_truncated':len(operations)>80,
        'alignment_basis':'one_minimum_edit_alignment_not_a_business_explanation'}
