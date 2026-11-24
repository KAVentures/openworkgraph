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
const {observedRunMatrix,runDifference}=scope.window.__owgWorkflowExplorerModel;
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
console.log('Observed run matrix regression checks passed');
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
