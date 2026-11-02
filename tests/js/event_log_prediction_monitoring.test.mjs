import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {monitoringRows}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('prediction monitoring is read window by window with flags and the proposal',()=>{
  const r={windows:[
      {window:1,to:'2024-03-01T00:00:00Z',scored:true,scored_cases:170,mean_absolute_error_seconds:36000,naive_mean_absolute_error_seconds:43200,p10_p90_coverage:0.81,p10_p90_coverage_ci95:[0.76,0.86],coverage_flag:null,brier_score:0.01},
      {window:2,to:'2024-04-01T00:00:00Z',scored:false,reason:'fewer than 10 cases could be predicted'},
      {window:3,to:'2024-05-01T00:00:00Z',scored:true,scored_cases:171,mean_absolute_error_seconds:120000,naive_mean_absolute_error_seconds:130000,p10_p90_coverage:0.4,p10_p90_coverage_ci95:[0.33,0.46],coverage_flag:'ranges_too_narrow',brier_score:0.3}],
    flags:[{flag:'ranges_too_narrow',window:3},{flag:'error_rising',window:3}],
    recalibration_proposal:{range_scaling_factor:1.6,latest_window_coverage_before:0.4,latest_window_coverage_after:0.8,previous_window_coverage_after:null}};
  const v=monitoringRows(r);
  assert.deepEqual(plain(v.rows[0]),['#1 to 2024-03-01','170','10.0 h','12.0 h','81.0% [76.0%, 86.0%]','0.010']);
  assert.deepEqual(plain(v.rows[1]),['#2 to 2024-04-01','—','—','—','fewer than 10 cases could be predicted','—']);
  assert.match(v.rows[2][4],/⚠$/);
  assert.equal(v.summary,'In the latest window ranges have become too narrow and typical error has risen. Review the predictions before relying on them.');
  assert.equal(v.proposal,'Proposal, not applied: scale each range around its median by 1.60×. That would have held 80.0% of the latest window (now 40.0%).');
  assert.equal(monitoringRows({...r,flags:[],recalibration_proposal:null}).summary,'No drift flagged in the latest window.');
});
