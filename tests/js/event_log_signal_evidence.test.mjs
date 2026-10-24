import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8'),scope);
const {newCaseChange}=scope.window.__owgEventLogModel;

test('signal history distinguishes zero new cases from a first check',()=>{
  assert.equal(newCaseChange({new_cases_since_last_evaluation:0}),'0');
  assert.equal(newCaseChange({new_cases_since_last_evaluation:null,new_cases_comparison:{reason:'first_check'}}),'first check');
});

test('partially retained signal history displays bounds instead of a point or first check',()=>{
  assert.equal(newCaseChange({new_cases_since_last_evaluation:null,new_cases_comparison:{
    reason:'retained_case_limit',lower_bound:0,upper_bound:2}}),'0–2 (bounded history)');
  assert.equal(newCaseChange({new_cases_since_last_evaluation:null,new_cases_comparison:{
    reason:'previous_population_unknown',lower_bound:0,upper_bound:4}}),'0–4 (bounded history)');
});
