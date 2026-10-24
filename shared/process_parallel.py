"""Explicit bounded fork/join regions and structural token alignment.

Supplied event order never proves simultaneity, business policy or outcomes.
"""
from collections import defaultdict
from heapq import heappop, heappush
from itertools import count

PARALLEL_KINDS = {'parallel_split', 'parallel_join'}
MAX_ALIGNMENT_STATES = 100_000
MAX_BATCH_ALIGNMENT_STATES = 200_000
MAX_FIRING_REPLAY_WORK = 100_000


def parallel_regions(model):
    """Require properly nested paired blocks with disjoint unconditional branches.

    A branch is a linear chain of activities/routing or complete nested blocks.
    Branch macros retain each nested split, all its branches and its join. No
    conditional, crossing, escaping or partially joined region is flattened.
    """
    nodes = {node.id: node for node in model.activities}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    conditional_sources = set()
    for edge in model.transitions:
        outgoing[edge.source].append(edge.target);incoming[edge.target].append(edge.source)
        if edge.rule_id:
            conditional_sources.add(edge.source)
            if nodes[edge.source].kind in PARALLEL_KINDS:
                raise ValueError('Parallel gateway transitions cannot have conditional rules')
    regions, paired, visiting = {}, {}, set()

    def parse(split):
        if split in visiting:
            raise ValueError('Parallel regions cannot contain cycles or enter themselves')
        if split in regions:
            return regions[split]
        if split == model.entry_activity_id and incoming[split]:
            raise ValueError('A parallel entry split requires no incoming transition; declare a separate process entry for loops')
        if len(incoming[split]) > 1:
            raise ValueError('Parallel splits require a single entry; merge exclusive arrivals explicitly first')
        if not 2 <= len(outgoing[split]) <= 8:
            raise ValueError('Parallel splits require 2–8 unconditional branches')
        visiting.add(split)
        paths, joins = [], []
        for first in outgoing[split]:
            path=[];cursor=first
            while nodes[cursor].kind != 'parallel_join':
                if cursor in path or cursor in conditional_sources or len(incoming[cursor]) != 1:
                    raise ValueError('Parallel branches must be disjoint unconditional paths or complete nested blocks')
                if nodes[cursor].kind == 'parallel_split':
                    child = parse(cursor)
                    block = [cursor] + [step for branch in child['branches'] for step in branch] + [child['join']]
                    if set(block) & set(path):
                        raise ValueError('Parallel branches cannot cross or re-enter a nested region')
                    path.extend(block)
                    cursor = child['join']
                elif nodes[cursor].kind in {'activity', 'routing'}:
                    path.append(cursor)
                else:
                    raise ValueError('Parallel branches require unconditional activities, routing or complete paired blocks')
                if len(outgoing[cursor]) != 1:
                    raise ValueError('Parallel branches cannot escape or terminate before the paired join')
                cursor=outgoing[cursor][0]
            paths.append(path);joins.append(cursor)
        if len(set(joins)) != 1 or joins[0] in paired:
            raise ValueError('Every parallel split requires its own common join')
        join=joins[0]
        ends={path[-1] if path else split for path in paths}
        if set(incoming[join]) != ends or len(incoming[join]) != len(paths):
            raise ValueError('Parallel joins must consume exactly the paired branch arrivals')
        flattened=[identifier for path in paths for identifier in path]
        if model.entry_activity_id in flattened:
            raise ValueError('A parallel process cannot enter a branch before its paired split; declare a separate process entry')
        if len(set(flattened)) != len(flattened) or split in flattened or join in flattened:
            raise ValueError('Parallel branches must not share nodes before their join')
        paired[join]=split
        regions[split]={'split':split,'join':join,'branches':paths}
        visiting.remove(split)
        return regions[split]

    for node in model.activities:
        if node.kind == 'parallel_split':parse(node.id)
    blocks = [{region['split'], region['join']} | {step for path in region['branches'] for step in path}
              for region in regions.values()]
    for index, block in enumerate(blocks):
        if any(block & other and not (block < other or other < block) for other in blocks[index + 1:]):
            raise ValueError('Parallel regions must be disjoint or completely nested; crossing blocks are unsupported')
    for node in model.activities:
        if node.kind == 'parallel_join' and (node.id not in paired or node.id==model.entry_activity_id or len(outgoing[node.id]) != 1
                                            or node.id in model.terminal_activity_ids):
            raise ValueError('Parallel joins require one paired split and one continuation')
        if node.kind == 'parallel_split' and node.id in model.terminal_activity_ids:
            raise ValueError('Parallel splits cannot be terminal')
    return [regions[node.id] for node in model.activities if node.kind == 'parallel_split']


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

    def firings(self, marking):
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
                    yield node,(marking-consumed)|emitted,emitted

    def moves(self, marking):
        for node, following, _emitted in self.firings(marking):
            yield node, following


def replay_node_ids(node_ids, model, *, max_work=MAX_FIRING_REPLAY_WORK):
    """Validate a full node-firing order, retaining every viable token history.

    Gateways are explicit firings. A join needs all branch tokens, and completion
    needs the sole terminal token. Returned edge IDs cover complete replays only.
    This structural schedule grants no tool or business execution permission.
    """
    if (not isinstance(node_ids, (list, tuple)) or not 1 <= len(node_ids) <= 80
            or any(not isinstance(identifier, str) for identifier in node_ids)
            or type(max_work) is not int or max_work < 0):
        raise ValueError('A bounded node-firing sequence and replay work limit are required')
    automaton = EdgeTokenModel(model)
    states = {(automaton.initial, frozenset())}
    work = 0
    for identifier in node_ids:
        following = set()
        for marking, used in states:
            for node, result, emitted in automaton.firings(marking):
                if work >= max_work:
                    raise ValueError('Node-firing replay work limit reached; no valid schedule established')
                work += 1
                if node.id == identifier:
                    following.add((result, used | frozenset(p for p in emitted if p >= 0)))
        if not following:
            raise ValueError('Node-firing sequence violates the reviewed process token flow')
        states = following
    complete = [used for marking, used in states if marking == automaton.done]
    if not complete:
        raise ValueError('Node-firing sequence leaves unfinished branches or no terminal completion')
    used = sorted(set().union(*complete))
    return {'transition_indices': used,
            'rule_ids': sorted({model.transitions[p].rule_id for p in used if model.transitions[p].rule_id}),
            'work_count': work, 'all_produced_branch_tokens_consumed': True}


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
