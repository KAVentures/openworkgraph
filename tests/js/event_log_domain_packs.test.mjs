import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {packMapping,packGuess,packReportRows}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('a mapping keeps only roles mapped to a log name',()=>{
  const roles={object_types:[{role:'order'},{role:'payment'}],activities:[{role:'ship'},{role:'change_order'}]};
  const values={object_types:{order:'SalesOrder',payment:''},activities:{ship:'Post Goods Issue'}};
  assert.deepEqual(plain(packMapping(roles,values)),{object_types:{order:'SalesOrder'},activities:{ship:'Post Goods Issue'}});
});

test('the starting guess prefers the installed name, then an equal label',()=>{
  const names=['ship ','Post Goods Issue','Pick'];
  assert.equal(packGuess({label:'Ship'},names,'Post Goods Issue'),'Post Goods Issue');
  assert.equal(packGuess({label:'Ship'},names,'Gone'),'ship ');
  assert.equal(packGuess({label:'Receive payment'},names,undefined),'');
});

test('a pack report is read in plain rows',()=>{
  const r={metrics:[{name:'o2c_order_to_invoice',case_type:'SalesOrder',unit:'seconds',cases:20,median:259200,p90:406080,coverage:{cases_with_value:20}},
                    {name:'o2c_order_changes',case_type:'SalesOrder',unit:'events',cases:20,median:0,p90:1,coverage:{cases_with_value:20}}],
    compliance:[{case_type:'BillingDoc',results:[{description:'every VF01 Billing is followed by Clear Payment within 2592000 s',activated_cases:20,violating_cases:1,unknown_cases:0}]}],
    object_rules:{results:[{description:'every Post Goods Issue event is linked to exactly 1 Delivery objects',complete:true,activated:20,violating:0},
                           {description:'budgeted rule',complete:false}]},
    object_model:{summary:{fitting_objects:115,replayed_objects:118,fitting_events:127,events_checked:127}},
    not_applicable:[{section:'reference_model',index:4,unmapped_role:'payment'}]};
  const v=packReportRows(r);
  assert.equal(v.metrics[0][3],'3.0 d');
  assert.deepEqual(plain(v.metrics[1].slice(2)),['20','0','1','20 of 20']);
  assert.deepEqual(plain(v.compliance),[['BillingDoc','every VF01 Billing is followed by Clear Payment within 2592000 s','20','1','0']]);
  assert.deepEqual(plain(v.objectRules),[['every Post Goods Issue event is linked to exactly 1 Delivery objects','20','0'],['budgeted rule','—','Unknown']]);
  assert.equal(v.model,'115 of 118 objects fit the reference lifecycles; 127 of 127 events match its arcs.');
  assert.deepEqual(plain(v.notApplicable),['reference model 5 (needs payment)']);
  assert.equal(packReportRows({...r,object_model:null}).model,'No reference lifecycle applies with this mapping.');
});
