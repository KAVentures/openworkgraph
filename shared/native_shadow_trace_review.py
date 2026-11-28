"""Review declared shadow attempts using existing exported OWG agent traces."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

FORMAT = 'openworkgraph.native-shadow-trace-review.v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
RUN = re.compile(r'execution:[a-f0-9]{16}\Z')
EVENT = re.compile(r'event:[a-f0-9]{16}\Z')
CASE = re.compile(r'case_[a-zA-Z0-9_-]{1,64}\Z')
MAX_BYTES = 16 * 1024 * 1024
FAILURES = {'error', 'cancelled', 'denied', 'failed', 'timeout'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def label(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and bool(value.strip())


def inventory(trace):
    require(isinstance(trace, dict) and trace.get('source') == 'canonical_agent_evidence'
            and trace.get('read_only') is True and trace.get('writes_performed') is False
            and trace.get('evidence_is_canonical') is True and trace.get('authoritative') is False,
            'Expected existing read-only agent-execution-traces response')
    runs = trace.get('executions')
    require(isinstance(runs, list) and 1 <= len(runs) <= 100 and type(trace.get('returned')) is int
            and trace['returned'] == len(runs) and type(trace.get('agent_execution_count_considered')) is int
            and trace['agent_execution_count_considered'] >= len(runs), 'Invalid run coverage')
    result = {}
    for run in runs:
        require(isinstance(run, dict), 'Invalid run')
        rid = run.get('execution_id')
        require(isinstance(rid, str) and RUN.fullmatch(rid) and rid not in result, 'Invalid or duplicate execution ID')
        children, parent = run.get('child_execution_ids'), run.get('parent_execution_id')
        require(isinstance(children, list) and len(children) <= 100 and all(isinstance(c, str) and RUN.fullmatch(c) for c in children)
                and len(set(children)) == len(children) and rid not in children
                and (parent is None or isinstance(parent, str) and RUN.fullmatch(parent) and parent != rid), 'Invalid child linkage')
        events = run.get('events')
        total, returned = run.get('event_count_total'), run.get('event_count_returned')
        require(isinstance(events, list) and 1 <= len(events) <= 500 and type(total) is int and type(returned) is int
                and returned == len(events) <= total and type(run.get('events_truncated')) is bool
                and run['events_truncated'] == (returned < total), 'Invalid event coverage')
        counts = run.get('operation_counts')
        require(isinstance(counts, dict) and all(label(k) and type(v) is int and v > 0 for k,v in counts.items())
                and sum(counts.values()) == total, 'Invalid operation totals')
        seen, excerpt = set(), Counter()
        for e in events:
            require(isinstance(e, dict) and isinstance(e.get('event_ref'), str) and EVENT.fullmatch(e['event_ref'])
                    and e['event_ref'] not in seen and label(e.get('operation')) and label(e.get('status')), 'Invalid or duplicate event reference')
            seen.add(e['event_ref']); excerpt[e['operation']] += 1
        require(all(v <= counts.get(k, 0) for k,v in excerpt.items()) and
                (run['events_truncated'] or excerpt == counts), 'Operation counts disagree with excerpt')
        for field, op in [('run_start_observed','run_started'),('run_finish_observed','run_finished')]:
            require(type(run.get(field)) is bool and run[field] == (counts.get(op,0)>0), 'Inconsistent run boundary')
        require(type(run.get('complete_boundary_observed')) is bool and
                run['complete_boundary_observed'] == (run['run_start_observed'] and run['run_finish_observed']), 'Inconsistent complete boundary')
        require(isinstance(run.get('agent'),dict), 'Invalid agent descriptor')
        result[rid] = run
    return result


def review(trace, trace_pin, declaration):
    require(isinstance(trace_pin,str) and HEX.fullmatch(trace_pin) and digest(trace)==trace_pin, 'Trace pin mismatch')
    runs = inventory(trace)
    require(isinstance(declaration,dict) and set(declaration)=={'package_sha256','cases'} and
            isinstance(declaration['package_sha256'],str) and HEX.fullmatch(declaration['package_sha256']), 'Invalid package declaration')
    cases = declaration['cases']
    require(isinstance(cases,list) and 1 <= len(cases) <= 50, 'Declare one to fifty shadow cases')
    assigned, case_ids, rows = set(), set(), []
    for case in cases:
        require(isinstance(case,dict) and set(case)=={'case_id','root_execution_id','expected_framework','reviewed_tool_labels'}
                and isinstance(case['case_id'],str) and CASE.fullmatch(case['case_id']) and case['case_id'] not in case_ids
                and isinstance(case['root_execution_id'],str) and RUN.fullmatch(case['root_execution_id'])
                and label(case['expected_framework']), 'Invalid case declaration')
        case_ids.add(case['case_id'])
        tools = case['reviewed_tool_labels']
        require(isinstance(tools,list) and len(tools)<=100 and all(label(t) for t in tools) and len(set(tools))==len(tools), 'Invalid reviewed tool labels')
        allowed = set(tools)
        root = case['root_execution_id']
        pending = [(root,None)]
        visited, missing, gaps, deviations, failures = set(), set(), [], [], []
        observed_tools = 0
        while pending:
            rid, expected_parent = pending.pop()
            require(rid not in visited, 'Cyclic or repeated declared child run')
            visited.add(rid)
            require(rid not in assigned, 'Execution assigned to multiple cases')
            assigned.add(rid)
            if rid not in runs:
                missing.add(rid)
                continue
            run = runs[rid]
            require(run['parent_execution_id']==expected_parent, 'Selected root or child parent linkage differs')
            if run['events_truncated']:
                gaps.append({'execution_id':rid,'code':'truncated_event_excerpt'})
            if not run['complete_boundary_observed']:
                gaps.append({'execution_id':rid,'code':'missing_run_boundary'})
            framework=run['agent'].get('framework')
            if not label(framework):
                gaps.append({'execution_id':rid,'code':'framework_not_observed'})
            elif framework != case['expected_framework']:
                deviations.append({'execution_id':rid,'code':'different_observed_framework'})
            for e in run['events']:
                ref={'execution_id':rid,'event_ref':e['event_ref']}
                if e['status'] in FAILURES or e['operation']=='error':
                    failures.append({**ref,'code':'observed_failure_signal'})
                if e['operation']=='tool_call':
                    observed_tools += 1
                    tool=e.get('tool')
                    name=tool.get('name') if isinstance(tool,dict) else None
                    if not label(name) or name.lower()=='unknown':
                        gaps.append({**ref,'code':'tool_label_not_observed'})
                    elif name not in allowed:
                        deviations.append({**ref,'code':'observed_tool_label_outside_reviewed_list'})
            pending.extend((child,rid) for child in reversed(run['child_execution_ids']))
        # A returned child whose parent is selected must be named by that parent.
        for rid,run in runs.items():
            if run['parent_execution_id'] in visited and rid not in visited:
                raise ValueError('Returned child omitted from parent linkage')
        gaps.extend({'execution_id':rid,'code':'declared_run_not_returned'} for rid in sorted(missing))
        status = 'observed_deviation_or_failure' if deviations or failures else 'incomplete_observation' if gaps else 'no_deviation_in_returned_events'
        rows.append({'case_id':case['case_id'],'root_execution_id':root,'returned_execution_ids':sorted(visited-missing),
                     'observed_tool_call_count':observed_tools,'coverage_gaps':gaps,'deviations':deviations,
                     'failure_signals':failures,'status':status})
    body={'format':FORMAT,'trace_sha256':trace_pin,'declaration_sha256':digest(declaration),
          'declared_package_sha256':declaration['package_sha256'],'cases':rows,
          'unassigned_returned_execution_ids':sorted(set(runs)-assigned),
          'response_run_limit_reached':trace['agent_execution_count_considered']>len(runs),
          'case_and_package_association_verified':False,'skill_invocation_verified':False,
          'live_source_authenticity_verified':False,'business_outcome_verified':False,
          'complete_tool_observation_verified':False,'client_permissions_verified':False,'execution_authorized':False,
          'interpretation':'Exact review of supplied structural event excerpts and customer-declared associations only. Absence of observed deviations is not permission compliance, a passed case, proof of skill invocation or a verified outcome.'}
    return {**body,'review_sha256':digest(body)}


def load(path):
    def pairs(items):
        d={}
        for k,v in items:
            require(k not in d,'Duplicate JSON key');d[k]=v
        return d
    with Path(path).open('rb') as f:
        raw=f.read(MAX_BYTES+1)
    require(0<len(raw)<=MAX_BYTES,'Input byte budget exceeded')
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs)
    canonical(value)
    return value


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('trace','trace-sha256','declaration','output'):
        p.add_argument('--'+arg,required=True)
    p.add_argument('--confirm-local-review',action='store_true',required=True)
    a=p.parse_args(argv)
    try:
        result=review(load(a.trace),a.trace_sha256,load(a.declaration));raw=canonical(result)
        require(len(raw)<=MAX_BYTES,'Output byte budget exceeded')
        fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw)
        print(result['review_sha256'])
    except (ValueError,TypeError,KeyError,OSError,RecursionError,OverflowError):
        p.exit(2,'Trace review refused; check exact input pins, declarations and new output location.\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
