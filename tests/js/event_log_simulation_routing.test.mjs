import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {visitRows}=scope.window.__owgEventLogModel;

test('visits per case list every activity of either run with a signed change',()=>{
  const rows=visitRows({visits_per_case:{Check:1.4286,Fix:0.4286,Close:1}},{visits_per_case:{Check:1.05,Fix:0.05,Close:1,Review:0.05}});
  assert.deepEqual(JSON.parse(JSON.stringify(rows)),[['Check','1.43','1.05','−0.38'],['Close','1.00','1.00','0.00'],
    ['Fix','0.43','0.05','−0.38'],['Review','0.00','0.05','+0.05']]);
  assert.deepEqual(JSON.parse(JSON.stringify(visitRows({},{}))),[]);
});
