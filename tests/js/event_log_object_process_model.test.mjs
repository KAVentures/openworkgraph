import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {objectModelArcs,objectModelText}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('edited arcs keep, change or remove each discovered arc',()=>{
  const arcs=[{activity:'Ship',object_type:'item',variable:true},{activity:'Pay',object_type:'order',variable:false},{activity:'Pick item',object_type:'item',variable:false}];
  assert.deepEqual(plain(objectModelArcs(arcs,[])),arcs);
  assert.deepEqual(plain(objectModelArcs(arcs,['single','remove','variable'])),[
    {activity:'Ship',object_type:'item',variable:false},{activity:'Pick item',object_type:'item',variable:true}]);
});

test('a conformance result is read in plain language',()=>{
  const r={summary:{objects:12,replayed_objects:11,fitting_objects:8,events_checked:15,fitting_events:13,complete:false},
    lifecycles:[{object_type:'order',objects:3,replayed_objects:3,fitting_objects:1,token_fitness:0.8125,unknown_objects:0,missing_tokens_by_activity:{Pay:2}},
                {object_type:'item',objects:9,replayed_objects:8,fitting_objects:7,token_fitness:null,unknown_objects:1,missing_tokens_by_activity:{}}],
    arcs:{issues_by_arc:[{activity:'Pay',object_type:'order',issue:'missing_object',events:1},{activity:'Pay',object_type:'order',issue:'several_objects_on_single_arc',events:1}],
          examples:[{event_id:'orphan-pay',activity:'Pay',object_type:'order',issue:'missing_object',objects:0}]}};
  const v=objectModelText(r);
  assert.equal(v.summary,'8 of 11 replayed objects fit their type’s model; 13 of 15 events match their arcs. Some objects were not replayed (see Unknown).');
  assert.deepEqual(plain(v.lifecycles),[['order','3','1 of 3','0.813','0','Pay (2)'],['item','9','7 of 8','—','1','—']]);
  assert.deepEqual(plain(v.issues),[['Pay','order','No object of the type','1'],['Pay','order','Several objects where one is expected','1']]);
  assert.deepEqual(plain(v.examples),['orphan-pay (Pay, order: 0)']);
  assert.doesNotMatch(objectModelText({...r,summary:{...r.summary,complete:true}}).summary,/not replayed/);
});
