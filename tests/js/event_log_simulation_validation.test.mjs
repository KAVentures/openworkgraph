import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {validationText}=scope.window.__owgEventLogModel;

const result=(differ,ks)=>({cutoff:'2024-02-01T00:00:00Z',cases:{calibration:690,held_out:300,spanning_cutoff_left_out:10},
  cycle_time_seconds:{simulated:{p50:3600,p90:7200,mean:4000},observed_held_out:{p50:7200,p90:7200,mean:4000},
    relative_error:{p50:-0.5,p90:0,mean:null}},
  distribution_distance:{kolmogorov_smirnov:ks,critical_value_5pct:0.085,distributions_differ_at_5pct:differ}});

test('held-out validation is read in plain language',()=>{
  const flagged=validationText(result(true,0.4));
  assert.match(flagged.cases,/Calibrated on 690 cases finished before 2024-02-01 and compared with the 300 cases that started after it; 10 cases spanning/);
  assert.deepEqual(JSON.parse(JSON.stringify(flagged.rows.map(r=>r[3]))),['−50%','+0%','—']);
  assert.match(flagged.verdict,/differ more than chance explains \(Kolmogorov–Smirnov 0\.400 > 0\.085 at 5%\)/);
  assert.match(validationText(result(false,0.01)).verdict,/consistent at the 5% level.*not a forecast guarantee/);
  assert.match(validationText(result(null,null)).verdict,/Too few cases/);
});

test('stopped step-by-step routes are stated with the held-out fit',()=>{
  assert.equal(validationText(result(false,0.01)).stopped,null);
  const r={...result(false,0.01),simulated_routes:{cases_simulated:1000,stopped_at_path_limit:374,path_event_limit:100,stopped_routes_in_comparison:true}};
  assert.equal(validationText(r).stopped,'374 of 1,000 simulated cases reached the 100-event path limit and were stopped there. They are compared as if finished, so simulated times and the fit below are understated.');
  assert.equal(validationText({...r,simulated_routes:{...r.simulated_routes,stopped_at_path_limit:0}}).stopped,null);
});
