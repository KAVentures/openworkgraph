import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {simUncertainty,uncertaintyRows,duration}=scope.window.__owgEventLogModel;
const plain=x=>JSON.parse(JSON.stringify(x));

test('replications are off when empty or zero and bounded otherwise',()=>{
  assert.equal(simUncertainty(''),null);assert.equal(simUncertainty('0'),null);assert.equal(simUncertainty(undefined),null);
  assert.deepEqual(plain(simUncertainty(' 20 ')),{replications:20});
  for(const bad of ['1','51','2.5','x'])assert.throws(()=>simUncertainty(bad),/2 to 50 replications/);
});

test('intervals read as median with the 95% range, and changes carry a sign',()=>{
  const i=(low,median,high)=>({low,median,high});
  const rows=uncertaintyRows({cycle_time_seconds:{baseline:{p50:i(3600,5400,7200),p90:i(7200,9000,10800)},
    result:{p50:i(1800,3600,5400),p90:null},change:{p50:i(-3600,-1800,600),p90:i(0,0,0)}}});
  assert.deepEqual(plain(rows[0]),['Baseline',duration(5400)+' ('+duration(3600)+' – '+duration(7200)+')',duration(9000)+' ('+duration(7200)+' – '+duration(10800)+')']);
  assert.equal(rows[1][2],'—');
  assert.equal(rows[2][1],'−'+duration(1800)+' (−'+duration(3600)+' to +'+duration(600)+')');
  assert.equal(rows[2][2],duration(0)+' ('+duration(0)+' to '+duration(0)+')');
});
