import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {followSpec,followText,tokenAt}=scope.window.__owgEventLogModel;

test('follower rules map editor modes to direct or eventual, optionally negated',()=>{
  const rules=followSpec([{from:'A',to:'B',mode:'direct'},{from:'B',to:'C',mode:'not-eventual'}]);
  assert.deepEqual(JSON.parse(JSON.stringify(rules)),[{from:'A',to:'B',mode:'direct',negate:false},{from:'B',to:'C',mode:'eventual',negate:true}]);
  assert.equal(followText(rules[1]),'B is never eventually followed by C');
  assert.throws(()=>followSpec([{from:'A',to:'',mode:'direct'}]),/both activities/);
  assert.throws(()=>followSpec([{from:'A',to:'B',mode:'sometimes'}]),/follow each other/);
  assert.throws(()=>followSpec(Array.from({length:6},()=>({from:'A',to:'B',mode:'direct'}))),/at most 5/);
});

test('a token is between recorded events, jumps across shared timestamps and is absent outside the case',()=>{
  const events=[[10,0],[20,1],[20,2],[40,3]];
  assert.equal(tokenAt(events,5),null);
  assert.equal(tokenAt(events,41),null);
  assert.deepEqual({...tokenAt(events,15)},{i:0,f:0.5});
  assert.deepEqual({...tokenAt(events,20)},{i:2,f:0},'tied steps resolve to the last event at that time');
  assert.deepEqual({...tokenAt(events,40)},{i:3,f:0});
  assert.equal(tokenAt([],0),null);
});
