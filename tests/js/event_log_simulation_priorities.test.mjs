import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {simPriorities}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('priority classes are read as name, share and rank',()=>{
  assert.equal(simPriorities(''),null);
  assert.deepEqual(plain(simPriorities('urgent 30% 0, standard 70% 1')),[{name:'urgent',share:0.3,rank:0},{name:'standard',share:0.7,rank:1}]);
  assert.deepEqual(plain(simPriorities('VIP customers 12.5% 0, rest 87.5% 3')).map(c=>c.share),[0.125,0.875]);
  assert.throws(()=>simPriorities('urgent 30% 0'),/at least two priority classes/);
  assert.throws(()=>simPriorities('urgent 30 0, standard 70% 1'),/name, a share in percent and a rank/);
  assert.throws(()=>simPriorities('urgent 30% 10, standard 70% 1'),/rank from 0 to 9/);
});
