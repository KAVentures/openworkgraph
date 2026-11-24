from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_selected_execution_workbench():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scope = {window:{},document:{readyState:'loading',addEventListener(){}}};
vm.runInNewContext(fs.readFileSync('dashboard/workflow_explorer.js','utf8'),scope);
const {observedRunMatrix,runDifference,projectedSurfaceRoutes,projectedProcessPaths,observedPathProposal,projectedPathAgentBrief,selectedRunTrace,automationReviewSignals,foregroundCoverageRows,referenceOccurrenceAlignment,observedReferenceReview}=scope.window.__owgWorkflowExplorerModel;
const a='execution:aaaaaaaaaaaaaaaa',b='execution:bbbbbbbbbbbbbbbb';
const ids=[a,b];
const fixture=()=>({
  selector:{execution_ids:[b,a],selected_execution_count:2},
  executions:[
    {execution_id:b,actor_kind:'human',duration_seconds:14,steps:['action:open','action:close'],outcome_status:'unknown'},
    {execution_id:a,actor_kind:'human',duration_seconds:9,steps:['action:open','action:review','action:open','action:close'],outcome_status:'observed'}
  ],provenance:{source_limit_reached:false}
});
let model=observedRunMatrix(fixture(),ids);
assert.equal(model.rows[0].id,a);
assert.equal(model.rows[1].id,b);
assert.equal(model.sourceLimited,false);
let open=model.steps.find(s=>s.token==='action:open');
assert.equal(open.support,2);
assert.deepEqual(Array.from(open.occurrences),[2,1]);
assert.equal(model.steps.find(s=>s.token==='action:review').support,1);
let diff=runDifference(model,a,b);
assert.equal(diff.shared,2);
assert.equal(diff.onlyA,2);
assert.equal(diff.onlyB,0);
assert.equal(diff.elapsedDifference,5);
assert.equal(runDifference(model,a,a),null);
assert.equal(runDifference(model,a,'unknown'),null);
for (const edit of [
  f=>f.executions.pop(),
  f=>f.executions[0].execution_id=a,
  f=>f.selector.execution_ids=[a,a],
  f=>f.selector.selected_execution_count=3,
  f=>f.selector.execution_ids=[a,'execution:other']
]) {
  const f=fixture();edit(f);assert.equal(observedRunMatrix(f,ids),null);
}
assert.equal(observedRunMatrix(fixture(),[a,a]),null);
let f=fixture();f.executions[0].steps=[];
model=observedRunMatrix(f,ids);
assert.equal(model.completeProjection,false);
assert.equal(model.steps.length,0);
assert.equal(runDifference(model,a,b),null);
f=fixture();f.executions[0].actor_kind='agent';
assert.equal(runDifference(observedRunMatrix(f,ids),a,b).elapsedDifference,null);
f=fixture();f.executions[0].duration_seconds=null;
assert.equal(runDifference(observedRunMatrix(f,ids),a,b).elapsedDifference,null);
f=fixture();f.executions[0].duration_seconds=0;
assert.equal(runDifference(observedRunMatrix(f,ids),a,b).elapsedDifference,-9);
f=fixture();f.provenance={};
assert.equal(observedRunMatrix(f,ids).sourceLimited,true);
f=fixture();f.executions[0].steps=Array.from({length:45},(_,i)=>'step'+i);
model=observedRunMatrix(f,ids);
assert.equal(model.steps.length,40);
assert.equal(model.omitted,8);
f=fixture();f.executions[0].steps=Array(161).fill('step');
assert.equal(observedRunMatrix(f,ids).completeProjection,false);

f=fixture();
f.executions[0].steps=['surface:gmail','action:open','surface:salesforce','surface:gmail','surface:salesforce'];
f.executions[1].steps=['surface:gmail','surface:gmail','surface:salesforce','surface:browser','surface:slack','surface:0123456789ab','surface:github','action:click','action:click'];
model=observedRunMatrix(f,ids);
const routes=projectedSurfaceRoutes(model);
assert.equal(routes.surfaceCount,4);
assert.equal(routes.edges.length,2);
assert.equal(routes.edges[0].from,'surface:gmail');
assert.equal(routes.edges[0].to,'surface:salesforce');
assert.equal(routes.edges[0].support,2);
assert.equal(routes.edges[0].occurrences,3);
assert(!routes.edges.some(e=>e.from==='surface:salesforce'&&e.to==='surface:slack'));
assert(!routes.edges.some(e=>e.from==='surface:slack'&&e.to==='surface:github'));
f.executions[0].steps.push('action:click','action:click');
f.data_movement={clipboard_transfers:[
  {cross_surface:true,clipboard_contents_observed:false,support_runs:2,source_surface:'Gmail',destination_surface:'Salesforce'},
  {cross_surface:true,clipboard_contents_observed:true,support_runs:2,source_surface:'private',destination_surface:'private'},
  {cross_surface:true,clipboard_contents_observed:false,support_runs:3,source_surface:'wrong',destination_surface:'wrong'}
]};
const signals=automationReviewSignals(observedRunMatrix(f,ids),f);
assert.equal(signals.transfers.length,1);
assert.equal(signals.repeated.length,1);
assert.equal(signals.repeated[0].support,2);
assert.equal(signals.variants,2);
const traceFixture=()=>({selector:{execution_ids:[a],selected_execution_count:1},
  provenance:{source_limit_reached:false},canonical_evidence_included:true,
  canonical_evidence:[{execution_id:a,observed_event_count:2,returned_event_count:1,events_truncated:true,
    event_membership_basis:'inferred_time_and_session_window',nearby_events_may_be_unrelated:true,
    events:[{event_ref:'event:aaaaaaaaaaaaaaaa',observed_at:'2026-11-24T00:00:00Z',event_type:'browser_click',
      app:'Browser',window_title:'private title',metadata:{secret:'do not expose'}}]}]});
const trace=selectedRunTrace(traceFixture(),a);
assert.equal(trace.truncated,true);
assert.equal(trace.nearby,true);
assert.equal(trace.membership,'Inferred time/session window');
assert.deepEqual(Object.keys(trace.rows[0]).sort(),['app','ref','time','type']);
assert.equal(selectedRunTrace(traceFixture(),b),null);
for(const edit of [
  t=>t.canonical_evidence_included=false,
  t=>t.canonical_evidence[0].events_truncated=false,
  t=>t.canonical_evidence[0].returned_event_count=2,
  t=>t.canonical_evidence[0].events[0].observed_at='invalid',
  t=>t.canonical_evidence[0].events[0].event_ref='private'
]){
  const t=traceFixture();edit(t);assert.equal(selectedRunTrace(t,a),null);
}

f=fixture();
f.executions[0].steps=['action:open','action:review','action:open','action:close'];
f.executions[1].steps=f.executions[0].steps.slice();
model=observedRunMatrix(f,ids);
let paths=projectedProcessPaths(model);
assert.equal(paths.paths.length,1);
assert.deepEqual(Array.from(paths.paths[0].ids),ids);
const edges=paths.edges.filter(e=>e.from==='action:open');
assert.equal(edges.length,2);
assert.equal(paths.branchingTokens[0].targets,2);
assert.equal(paths.edges.find(e=>e.from==='action:open'&&e.to==='action:review').occurrences,2);
let proposal=observedPathProposal(paths.paths[0],model,f);
assert.equal(proposal.count,2);
assert.deepEqual(Array.from(proposal.evidenceRefs),ids);
assert.deepEqual(Array.from(proposal.path),['open','review','open','close']);
f.executions[0].actor_kind='agent';
model=observedRunMatrix(f,ids);
paths=projectedProcessPaths(model);
assert.equal(paths.paths.length,2);
assert.deepEqual(Array.from(paths.paths[0].ids),[a]);
assert.deepEqual(Array.from(observedPathProposal(paths.paths[1],model,f).evidenceRefs),[b]);
f.provenance.source_limit_reached=true;
model=observedRunMatrix(f,ids);
assert.equal(observedPathProposal(projectedProcessPaths(model).paths[0],model,f),null);
f=fixture();
f.executions[0].steps=Array(48).fill('action:open');
model=observedRunMatrix(f,ids);
assert.equal(model.projectionMayBeCapped,true);
paths=projectedProcessPaths(model);
assert.equal(observedPathProposal(paths.paths.find(p=>p.ids.includes(b)),model,f),null);
f=fixture();
f.executions[0].steps=['action:private-captured-label'];
model=observedRunMatrix(f,ids);
paths=projectedProcessPaths(model);
const selected=paths.paths.find(p=>p.ids.includes(b));
const criteria={goal:'Resolve the case',source:'Approved case API',rules:'Ask about exceptions',verification:'Independent readback'};
const brief=projectedPathAgentBrief(selected,model,f,{since:'2026-10-01T00:00:00Z'},criteria);
assert(brief.includes(b));
assert(!brief.includes(a));
assert(!brief.includes('private-captured-label'));
assert(brief.includes('Independent readback'));
assert(brief.includes('Do not run a workflow'));
assert.equal(projectedPathAgentBrief(selected,model,f,{since:'invalid'},criteria),null);
assert.equal(projectedPathAgentBrief(selected,model,f,{},{...criteria,goal:''}),null);
assert.equal(projectedPathAgentBrief(selected,model,f,{},{...criteria,goal:'a'.repeat(321)}),null);
f=fixture();
model=observedRunMatrix(f,ids);
const coverage={format:'openworkgraph.execution-foreground-coverage.v1',
  uncovered_time_is_unknown:true,distinct_surface_overlaps_are_unattributed:true,active_labor_or_waiting_measured:false,
  runs:[
    {execution_id:b,actor_kind:'human',status:'no_retained_foreground_spans',observed_event_count:0,window_seconds:14,
      foreground_union_seconds:null,uncovered_window_seconds:null},
    {execution_id:a,actor_kind:'human',status:'observed_bounded_coverage',observed_event_count:8,
      window_seconds:9,foreground_union_seconds:7,overlapping_distinct_surface_seconds:2,uncovered_window_seconds:2,
      surface_rows:[{surface:'surface:gmail',exclusive_foreground_seconds:5}],surface_rows_omitted:0,
      exclusive_other_surface_seconds:0,foreground_span_count:4,invalid_span_count:0}
  ]};
f.timing={per_execution_foreground_coverage:coverage};
let coverageRows=foregroundCoverageRows(model,f);
assert.equal(coverageRows[0].covered,7);
assert.equal(coverageRows[1].covered,null);
coverage.runs[1].uncovered_window_seconds=9;
assert.equal(foregroundCoverageRows(model,f)[0],null);
coverage.runs[1].uncovered_window_seconds=2;
coverage.runs[1].surface_rows[0].exclusive_foreground_seconds=9;
assert.equal(foregroundCoverageRows(model,f)[0],null);
coverage.runs[1].execution_id=b;
assert.equal(foregroundCoverageRows(model,f),null);
// Brute-force every ordered match for short binary sequences. Verify all
// occurrence classifications against the entire set of optimal alignments.
const sequences=[];
for(let length=1;length<=4;length++)for(let code=0;code<2**length;code++)
  sequences.push(Array.from({length},(_,i)=>((code>>i)&1)?'A':'B'));
for(const left of sequences)for(const right of sequences){
  let best=-1,optimal=[];
  const visit=(i,j,pairs)=>{
    if(pairs.length>best){best=pairs.length;optimal=[pairs];}
    else if(pairs.length===best)optimal.push(pairs);
    for(let x=i;x<left.length;x++)for(let y=j;y<right.length;y++)
      if(left[x]===right[y])visit(x+1,y+1,[...pairs,[x+1,y+1]]);
  };
  visit(0,0,[]);
  const actual=referenceOccurrenceAlignment(left,right);
  assert.equal(actual.shared,best);
  for(const [key,index] of [['reference',0],['comparison',1]]){
    const sequence=index===0?left:right;
    assert.equal(actual[key].length,sequence.length);
    actual[key].forEach((row,offset)=>{
      const matches=optimal.map(pairs=>pairs.find(pair=>pair[index]===offset+1));
      const positions=[...new Set(matches.filter(Boolean).map(pair=>pair[1-index]))].sort((a,b)=>a-b);
      const status=!positions.length?'not_aligned':matches.every(Boolean)?'aligned':'ambiguous';
      assert.equal(row.status,status);
      assert.deepEqual(Array.from(row.positions),positions);
    });
  }
}
assert.equal(referenceOccurrenceAlignment([],['A']),null);
assert.equal(referenceOccurrenceAlignment(Array(48).fill('A'),['A']),null);
f=fixture();model=observedRunMatrix(f,ids);
let reference=observedReferenceReview(model,a);
assert.equal(reference.comparable,1);
assert.equal(reference.exact,0);
assert.equal(reference.occurrenceSupport[0].ambiguous,1);
assert.equal(reference.occurrenceSupport[1].not_aligned,1);
assert.equal(reference.occurrenceSupport[2].ambiguous,1);
assert.equal(reference.occurrenceSupport[3].aligned,1);
assert.equal(reference.rows[0].id,b);
f.executions[0].actor_kind='agent';
reference=observedReferenceReview(observedRunMatrix(f,ids),a);
assert.equal(reference.comparable,0);
assert.equal(reference.rows[0].status,'unlike_actor');
f.executions[0].actor_kind='human';
f.executions[0].steps=Array(48).fill('action:open');
reference=observedReferenceReview(observedRunMatrix(f,ids),a);
assert.equal(reference.rows[0].status,'projection_unavailable');
f.provenance.source_limit_reached=true;
assert.equal(observedReferenceReview(observedRunMatrix(f,ids),a),null);
console.log('Observed run matrix, surface routes, trace and opportunity checks passed');
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
