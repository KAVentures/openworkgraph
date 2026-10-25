import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {attributeChangeSummary}=scope.window.__owgEventLogModel;

const attribute=(over)=>({name:'region',objects_with_values:3,objects_whose_value_changes:0,value_changes:0,
  times_with_several_values:0,most_frequent_changes:[],event_links:{examined:5,before_first_value:0,
  at_a_time_with_several_values:0,where_stored_value_differs_from_value_then:0},...over});

test('unchanging attributes and missing types are stated plainly',()=>{
  assert.deepEqual([...attributeChangeSummary(null)],[]);
  assert.deepEqual([...attributeChangeSummary({object_type:'order',attributes:[attribute()]})],
    ['The recorded attribute values of order objects do not change over time.']);
  assert.deepEqual([...attributeChangeSummary({object_type:'order',attributes:[]})],[]);
});

test('changes and their effect on event links are stated in plain language',()=>{
  const lines=attributeChangeSummary({object_type:'order',attributes:[attribute(),attribute({name:'status',
    objects_whose_value_changes:1,value_changes:2,times_with_several_values:1,
    most_frequent_changes:[{from:'approved',to:'shipped',changes:1},{from:'created',to:'approved',changes:1}],
    event_links:{examined:5,before_first_value:1,at_a_time_with_several_values:1,where_stored_value_differs_from_value_then:2}})]});
  assert.deepEqual([...lines],['status changes on 1 of 3 order objects (2 changes; most often approved → shipped (1), created → approved (1)). '
    +'2 of 5 event links happened while it held another value than the one stored value analyses use. '
    +'1 happened before any value was recorded. 1 times have several different values.']);
});

test('without a comparison only the changes are reported',()=>{
  const [line]=attributeChangeSummary({object_type:'order',attributes:[attribute({name:'status',objects_whose_value_changes:2,
    value_changes:4,event_links:undefined})]});
  assert.equal(line,'status changes on 2 of 3 order objects (4 changes).');
});
