import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {impactSpec,impactRows}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('the impact form becomes a request with only the fields that apply',()=>{
  assert.deepEqual(plain(impactSpec({date:'2024-03-01',metric:'cycle_time'})),{intervention_at:'2024-03-01T00:00:00Z',metric:'cycle_time'});
  assert.deepEqual(plain(impactSpec({date:'2024-03-01',metric:'sla_breach',slaHours:'36',window:'30',recent:'7',treatedActivity:'Auto check'})),
    {intervention_at:'2024-03-01T00:00:00Z',metric:'sla_breach',sla_seconds:129600,window_days:30,exclude_recent_days:7,treated:{activity:'Auto check'}});
  assert.deepEqual(plain(impactSpec({date:'2024-03-01',metric:'activity_count',activity:'Fix',treatedResource:'bot'})).treated,{resource:'bot'});
  assert.throws(()=>impactSpec({date:'',metric:'cycle_time'}),/date the change went live/);
  assert.throws(()=>impactSpec({date:'2024-03-01',metric:'sla_breach',slaHours:''}),/SLA in hours/);
  assert.throws(()=>impactSpec({date:'2024-03-01',metric:'cycle_time',window:'2.5'}),/whole number of days/);
});

test('an impact result is read in plain rows',()=>{
  const r={unit:'seconds',groups:{treated:{baseline:{cases:240,mean:172800},follow_up:{cases:240,mean:86400}},control:{baseline:{cases:240,mean:172800},follow_up:{cases:240,mean:151200}}},
    change:{estimated:true,mean_change:-86400,mean_change_ci95:[-90000,-82800],median_change:-86400,median_change_ci95:[-90000,-80000]},
    control_adjusted:{estimated:true,difference_in_differences:-64800,ci95:[-70000,-60000]},
    placebo:{estimated:true,placebo_at:'2024-01-30T12:00:00Z',mean_change:0,mean_change_ci95:[-3600,3600],pre_trend_detected:false}};
  const v=impactRows(r);
  assert.deepEqual(plain(v.rows),[['Treated','240','2.0 d','240','24.0 h'],['Control','240','2.0 d','240','42.0 h']]);
  assert.match(v.change,/^Mean change −24\.0 h \(95% −25\.0 h to −23\.0 h\); median change −24\.0 h/);
  assert.match(v.control,/^Against the control cohort: −18\.0 h/);
  assert.equal(v.placebo,'Placebo check at 2024-01-30: 0 s (95% −60 min to +60 min), no earlier change.');
  const shares=impactRows({unit:'share of cases',groups:{all:{baseline:{cases:20,mean:0.5},follow_up:{cases:20,mean:0}}},
    change:{estimated:false,reason:'each period needs at least 10 cases'},placebo:{estimated:false,reason:'no baseline cases'}});
  assert.deepEqual(plain(shares.rows[0]),['All cases','20','50.0%','20','0.0%']);
  assert.equal(shares.control,null);assert.equal(shares.placebo,'Placebo check: no baseline cases.');
});
