import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {predictionSpec}=scope.window.__owgEventLogModel;

test('the censoring-aware estimator is opt-in and validated',()=>{
  assert.equal(predictionSpec({ends:'Close'}).estimator,undefined);
  assert.equal(predictionSpec({ends:'Close',estimator:'completed_cases'}).estimator,undefined);
  assert.equal(predictionSpec({ends:'Close, Cancel',estimator:'survival'}).estimator,'survival');
  assert.throws(()=>predictionSpec({ends:'Close',estimator:'neural'}),/what to learn from/);
});
