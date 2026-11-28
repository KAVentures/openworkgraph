from copy import deepcopy
from collections import Counter
from itertools import product
import json
import pytest
from shared.native_shadow_trace_review import review, digest, main


def rid(n): return 'execution:'+f'{n:016x}'


def run(n, *, parent=None, children=(), states=(), truncated=False, framework='claude_code', finish=True):
    ops=[('run_started','success',None),*states]
    if finish: ops.append(('run_finished','success',None))
    counts=Counter(op for op,_,_ in ops)
    events=[{'event_ref':'event:'+f'{i:016x}','operation':op,'status':status,'tool':{'name':name},'private':'PRIVATE'}
            for i,(op,status,name) in enumerate(ops)]
    if truncated: events=events[:-1]
    return {'execution_id':rid(n),'parent_execution_id':rid(parent) if parent is not None else None,
            'child_execution_ids':[rid(i) for i in children],'agent':{'framework':framework},'events':events,
            'event_count_total':len(ops),'event_count_returned':len(events),'events_truncated':truncated,
            'operation_counts':dict(counts),'run_start_observed':True,'run_finish_observed':finish,
            'complete_boundary_observed':finish}


def trace(*runs):
    return {'source':'canonical_agent_evidence','read_only':True,'writes_performed':False,'evidence_is_canonical':True,
            'authoritative':False,'executions':list(runs),'returned':len(runs),'agent_execution_count_considered':len(runs)}


def declaration(*roots):
    return {'package_sha256':'a'*64,'cases':[{'case_id':f'case_{n}','root_execution_id':rid(n),
            'expected_framework':'claude_code','reviewed_tool_labels':['Read']} for n in roots]}


def inspect(t,d): return review(t,digest(t),d)


def test_exact_observed_status_oracle():
    options=[('Read','success'),('Write','success'),(None,'success'),('Read','error'),('unknown','success')]
    for (name,status),truncated,finish,framework in product(options,(False,True),(False,True),('claude_code','other','')):
        # Keep the tool event in the excerpt even when terminal signals are absent.
        states=[('tool_call',status,name),('model_call','success',None)]
        t=trace(run(1,states=states,truncated=truncated,finish=finish,framework=framework))
        r=inspect(t,declaration(1)); c=r['cases'][0]
        deviations=name=='Write' or framework=='other'; failure=status=='error'
        gaps=truncated or not finish or not framework or name in (None,'unknown')
        expected='observed_deviation_or_failure' if deviations or failure else 'incomplete_observation' if gaps else 'no_deviation_in_returned_events'
        assert c['status']==expected and c['observed_tool_call_count']==1
        assert bool(c['deviations'])==deviations and bool(c['failure_signals'])==failure
        assert bool(c['coverage_gaps'])==gaps
        assert 'PRIVATE' not in json.dumps(r) and not r['skill_invocation_verified']
        assert r['review_sha256']==digest({k:v for k,v in r.items() if k!='review_sha256'})


def test_child_closure_and_missing_runs():
    t=trace(run(1,children=(2,3)),run(2,parent=1,states=[('tool_call','success','Write')]),run(4))
    t['agent_execution_count_considered']=4
    r=inspect(t,declaration(1)); c=r['cases'][0]
    assert c['returned_execution_ids']==[rid(1),rid(2)]
    assert c['coverage_gaps']==[{'execution_id':rid(3),'code':'declared_run_not_returned'}]
    assert c['deviations'][0]['execution_id']==rid(2)
    assert r['unassigned_returned_execution_ids']==[rid(4)] and r['response_run_limit_reached']
    assert inspect(t,declaration(9))['cases'][0]['status']=='incomplete_observation'
    with pytest.raises(ValueError): inspect(t,declaration(1,2))
    with pytest.raises(ValueError): inspect(trace(run(1),run(2,parent=1)),declaration(1))
    with pytest.raises(ValueError): inspect(trace(run(1,children=(2,)),run(2)),declaration(1))


def test_invalid_counts_and_pins():
    t=trace(run(1)); d=declaration(1)
    with pytest.raises(ValueError,match='pin mismatch'): review(t,'f'*64,d)
    for key,value in [('event_count_returned',999),('operation_counts',{'run_started':2}),('complete_boundary_observed',False)]:
        b=deepcopy(t);b['executions'][0][key]=value
        with pytest.raises(ValueError): inspect(b,d)
    b=deepcopy(t);b['executions'][0]['events'][1]['event_ref']=b['executions'][0]['events'][0]['event_ref']
    with pytest.raises(ValueError,match='duplicate event'): inspect(b,d)


def test_cli_exclusive_and_duplicate_json(tmp_path):
    t=trace(run(1));d=declaration(1)
    for name,value in [('trace',t),('declaration',d)]: (tmp_path/name).write_text(json.dumps(value))
    args=['--trace',str(tmp_path/'trace'),'--trace-sha256',digest(t),'--declaration',str(tmp_path/'declaration'),'--output',str(tmp_path/'out'),'--confirm-local-review']
    assert main(args)==0
    assert json.loads((tmp_path/'out').read_text())==inspect(t,d)
    with pytest.raises(SystemExit) as exc: main(args)
    assert exc.value.code==2
