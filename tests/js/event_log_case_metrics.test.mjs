import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {caseMetric,metricText,analysisSpec}=scope.window.__owgEventLogModel;

test('case metrics are typed from form values with readable errors',()=>{
  const wait=caseMetric({kind:'time_between',name:'to_pay',from:'Receive',to:'Pay',toOccurrence:'last'});
  assert.deepEqual({...wait},{name:'to_pay',kind:'time_between',from_activity:'Receive',to_activity:'Pay',from_occurrence:'first',to_occurrence:'last'});
  assert.equal(metricText(wait),'to_pay: time from the first Receive to the last Pay');
  const spend=caseMetric({kind:'event_attribute_sum',name:'spend',attribute:' amount ',unit:'EUR'});
  assert.equal(spend.attribute,'amount');assert.equal(metricText(spend),'spend: sum of event attribute amount (EUR)');
  assert.throws(()=>caseMetric({kind:'activity_count',name:'Fixes',activity:'Fix'}),/lower-case/);
  assert.throws(()=>caseMetric({kind:'time_between',name:'x',from:'A'}),/both activities/);
  assert.throws(()=>caseMetric({kind:'case_attribute_number',name:'x',attribute:'a',unit:'<b>'}),/unit/);
});

test('measures keep the metric reference intact and metrics apply to case analyses only',()=>{
  const metric={name:'to_pay',kind:'activity_count',activity:'Pay'};
  const spec=analysisSpec({level:'cases',measure1:'median:metric:to_pay',metrics:[metric]});
  assert.deepEqual(JSON.parse(JSON.stringify(spec.measures)),[{fn:'median',of:'metric:to_pay'}]);
  assert.equal(spec.metrics.length,1);
  assert.equal(analysisSpec({level:'events',measure1:'count',metrics:[metric]}).metrics,undefined);
});
