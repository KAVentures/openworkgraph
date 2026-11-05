import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const sandbox={window:{}};
vm.runInNewContext(readFileSync(new URL('../../dashboard/automation_pipeline.js',import.meta.url),'utf8'),sandbox);
const view=sandbox.window.__owgPipelineModel.portfolioView;
const ideas=[
 {idea_id:'b',title:'Review renewals',description:'Manual check',source:{reference:'Finance r3'},score:{quadrant:'quick_win',modeled_hours_per_month:0,ease_score:80},updated_at:'2026-01-01'},
 {idea_id:'a',title:'Approve invoices',source:{kind:'event_log'},score:{quadrant:'major_project',modeled_hours_per_month:40,ease_score:20},updated_at:'2026-02-01'},
 {idea_id:'c',title:'Unassessed',source:{},score:null,updated_at:'2026-03-01'}];
const ids=opts=>Array.from(view(ideas,opts),x=>x.idea_id);
test('literal search spans title, details and source without regex semantics',()=>{
 assert.deepEqual(ids({query:'RENEWALS finance'}),['b']);
 assert.deepEqual(ids({query:'[a-z]'}),[]);
 assert.deepEqual(ids({query:'event_log'}),['a']);
});
test('quadrant and search compose; not assessed remains distinct',()=>{
 assert.deepEqual(ids({quadrant:'unassessed'}),['c']);
 assert.deepEqual(ids({quadrant:'quick_win',query:'finance'}),['b']);
 assert.deepEqual(ids({quadrant:'major_project',query:'finance'}),[]);
});
test('sorts keep zero assessments before unknown and leave input unchanged',()=>{
 const before=JSON.stringify(ideas);
 assert.deepEqual(ids({sort:'impact'}),['a','b','c']);
 assert.deepEqual(ids({sort:'ease'}),['b','a','c']);
 assert.deepEqual(ids({sort:'recent'}),['c','a','b']);
 assert.deepEqual(ids({sort:'title'}),['a','b','c']);
 assert.deepEqual(ids({}),['b','a','c']);
 assert.equal(JSON.stringify(ideas),before);
});

test('comparison preserves zero, unknown and declared assessment basis without totals',()=>{
 const rows=sandbox.window.__owgPipelineModel.comparisonRows([
  {...ideas[0],revision:2,assessment:{cases_per_month:0,minutes_per_case:0,eligible_share:0,compliance_review_needed:false,ease:{rules_based:1}}},ideas[2]]);
 const row=name=>Array.from(rows.find(r=>r[0]===name));
 assert.deepEqual(row('Modeled hours / month'),['Modeled hours / month',0,'Not assessed']);
 assert.deepEqual(row('Eligible share % (entered)'),['Eligible share % (entered)',0,'Not assessed']);
 assert.deepEqual(row('Compliance review declared needed'),['Compliance review declared needed','No','Not assessed']);
 assert.equal(rows.some(r=>/total/i.test(r[0])),false);
});
