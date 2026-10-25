import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {measuredMinutes}=scope.window.__owgEventLogModel;

test('the measured minutes follow the chosen basis',()=>{
  assert.equal(measuredMinutes({minutes:83,mean_minutes:120},'median'),83);
  assert.equal(measuredMinutes({minutes:83,mean_minutes:120},'mean'),120);
  assert.equal(measuredMinutes({minutes:83},'mean'),83);   // older calibrations without a mean keep the median
});
