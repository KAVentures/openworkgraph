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
