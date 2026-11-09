import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {parseReport,toCsv,MAX_BYTES}=require('../../dashboard/simulation_report_compare.js');
const run=(p50=10)=>({cycle_time_seconds:{p50,p90:30,mean:15},completed_per_day:2,unstable:false});
const packet=()=>({format:'openworkgraph.simulation-report.v1',generated_at:'2026-11-06T09:00:00Z',analysis:{
  simulated_not_observed:true,execution_authorized:false,log:{name:'Tickets',log_id:'log_123'},case_notion:'case',
  scenario:{label:'Change',cases:100,distribution:'fixed',routing:'paths',activity_time_basis:'gap',activities:{A:{minutes:1}}},
  calibration:{observed_cases:10,calibrated_cases:8},baseline:run(10),result:run(8),assumptions:['Assumed times'],
}});
test('extracts scope, comparable run metrics and recorded inputs without claiming dataset equality',()=>{
 const p=packet(),r=parseReport(JSON.stringify(p),'one.json');
 assert.equal(r.log_id,'log_123');assert.equal(r.scenario.p50,8);assert.equal(r.baseline.p50,10);
 assert.equal(r.calibrated_cases,8);assert.equal(r.uncertainty,false);assert.match(r.model_inputs,/minutes/);
 p.analysis.result.cycle_time_seconds.p50=null;
 assert.equal(parseReport(JSON.stringify(p),'one').scenario.p50,null);
});
test('rejects malformed, excessive, non-modelled and invalid numeric reports',()=>{
 assert.throws(()=>parseReport('{','x'));assert.throws(()=>parseReport(' '.repeat(MAX_BYTES+1),'x'));
 for(const mutate of [p=>p.analysis.execution_authorized=true,p=>p.analysis.calibration.calibrated_cases=11,
   p=>p.analysis.result.cycle_time_seconds.p50='NaN',p=>p.analysis.assumptions=[{}],p=>p.analysis.scenario.cases=0]){
   const p=packet();mutate(p);assert.throws(()=>parseReport(JSON.stringify(p),'x'));
 }
});
test('CSV escapes formulas, commas and preserves units, unknown and zero',()=>{
 const p=packet();p.analysis.scenario.label=' =SUM(1,2)';p.analysis.result.cycle_time_seconds.p50=0;
 p.analysis.result.cycle_time_seconds.p90=null;
 const csv=toCsv([parseReport(JSON.stringify(p),'=formula.json')]);
 assert.match(csv,/'=formula\.json/);assert.match(csv,/"' =SUM\(1,2\)"/);
 assert.match(csv,/scenario,p50,0,seconds/);assert.match(csv,/scenario,p90,,seconds/);
});
