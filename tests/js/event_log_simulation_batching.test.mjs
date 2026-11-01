import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {simBatch}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('a batch is read from the activity, its size and an optional longest wait',()=>{
  assert.equal(simBatch({activity:'',size:'10'}),null);
  assert.deepEqual(plain(simBatch({activity:'Pay',size:'10',maxWaitHours:''})),{activity:'Pay',batch:{size:10}});
  assert.deepEqual(plain(simBatch({activity:'Pay',size:'25',maxWaitHours:'24'})),{activity:'Pay',batch:{size:25,max_wait_minutes:1440}});
  assert.throws(()=>simBatch({activity:'Pay',size:'1'}),/at least 2 cases/);
  assert.throws(()=>simBatch({activity:'Pay',size:'3',maxWaitHours:'-2'}),/positive number of hours/);
});
