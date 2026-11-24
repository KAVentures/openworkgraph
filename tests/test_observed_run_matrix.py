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
const {observedRunMatrix,runDifference,projectedSurfaceRoutes,projectedProcessPaths,observedPathProposal,selectedRunTrace,automationReviewSignals}=scope.window.__owgWorkflowExplorerModel;
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
console.log('Observed run matrix, surface routes, trace and opportunity checks passed');
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
