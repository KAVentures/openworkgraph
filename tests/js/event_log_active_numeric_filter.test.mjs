import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {mergeActiveCohort}=scope.window.__owgEventLogModel;
const plain=value=>JSON.parse(JSON.stringify(value));
test('workspace numeric filters intersect safely with per-operation ranges',()=>{
 const active={attribute_range:{name:'amount',min:0,max:100},follows:[{from:'A',to:'B',mode:'direct',negate:false}]};
 const original={attribute_range:{name:'amount',min:25,max:150},activity:'Review'};
 assert.deepEqual(plain(mergeActiveCohort(original,active)),{activity:'Review',follows:active.follows,attribute_range:{name:'amount',min:25,max:100}});
 assert.deepEqual(original.attribute_range,{name:'amount',min:25,max:150});
 assert.throws(()=>mergeActiveCohort({attribute_range:{name:'other',min:1}},active),/different numeric attribute/);
 assert.throws(()=>mergeActiveCohort({attribute_range:{name:'amount',min:100}},active),/do not overlap/);
 assert.deepEqual(plain(mergeActiveCohort({},active)),active);
});

test('workspace activity filters keep literal labels and refuse unrepresentable intersections',()=>{
 const active={activity:'  Review 雪  '};
 assert.deepEqual(plain(mergeActiveCohort({resource:'team'},active)),{resource:'team',activity:'  Review 雪  '});
 assert.deepEqual(plain(mergeActiveCohort({activity:'  Review 雪  '},active)),active);
 assert.throws(()=>mergeActiveCohort({activity:'Review 雪'},active),/different activity/);
});

test('start periods intersect without rounding away subsecond bounds',()=>{
 const active={started_from:'2024-02-01T00:00:00.000001Z',started_before:'2024-03-01T00:00:00Z'};
 const operation={started_from:'2024-02-01T00:00:00Z',started_before:'2024-02-01T00:00:00.000002Z'};
 assert.deepEqual(plain(mergeActiveCohort(operation,active)),{
   started_from:active.started_from,started_before:operation.started_before
 });
 assert.throws(()=>mergeActiveCohort({started_before:'2024-02-01T00:00:00Z'},active),/start date/);
});
