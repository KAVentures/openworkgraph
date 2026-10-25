import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {flatteningSummary}=scope.window.__owgEventLogModel;

const base={object_type:'order',deficiency:{events_left_out:0,share_of_log_events:0,by_activity:[]},
  convergence:{convergent_events:0,extra_event_rows:0,duplication_factor:1,by_activity:[]},
  divergence:{by_activity:[]},qualifiers:{objects_with_several_qualifiers:0}};

test('a lossless case notion says nothing is left out or copied',()=>{
  assert.deepEqual([...flatteningSummary(base)],['No events are left out.','No event is counted more than once.']);
  assert.deepEqual([...flatteningSummary(null)],[]);
});

test('flattening consequences are stated in plain language',()=>{
  const lines=flatteningSummary({...base,object_type:'item',
    deficiency:{events_left_out:2,share_of_log_events:0.142857,by_activity:[{activity:'Pay',events:2}]},
    convergence:{convergent_events:2,extra_event_rows:3,duplication_factor:1.25,by_activity:[{activity:'Place order'},{activity:'Ship'}]},
    divergence:{by_activity:[{activity:'Check item',repetitions_differ_by:[{object_type:'order',groups_with_different_objects:1}]},
                             {activity:'Fix',repetitions_differ_by:[]}]},
    qualifiers:{objects_with_several_qualifiers:1}});
  assert.equal(lines[0],'2 events (14.3% of the log) are linked to no item and are left out: Pay (2).');
  assert.equal(lines[1],'2 events are linked to several item objects and are counted once per object: 3 extra event rows, 1.25× the linked events (most from Place order, Ship).');
  assert.equal(lines[2],'Repeats that happen once per other object, not rework: Check item (per order).');
  assert.equal(lines[3],'1 item objects are linked under more than one qualifier.');
});
