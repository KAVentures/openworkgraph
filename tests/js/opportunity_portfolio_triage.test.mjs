import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const sandbox={window:{}};
vm.runInNewContext(readFileSync(new URL('../../dashboard/automation_pipeline.js',import.meta.url),'utf8'),sandbox);
const {portfolioView:view,shortlistRecord,shortlistCsv}=sandbox.window.__owgPipelineModel;
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

test('exact planning filters compose with search, preserving missing values as a separate choice',()=>{
 const planned=[{...ideas[0],planning:{owner:'Ada',team:'Finance',target_quarter:'2026-Q4'}},
   {...ideas[1],planning:{owner:'ada',team:'Finance',target_quarter:'2027-Q1'}},ideas[2]];
 const keys=options=>Array.from(view(planned,options),i=>i.idea_id);
 assert.deepEqual(keys({query:'finance',planning:{owner:'Ada',target_quarter:'2026-Q4'}}),['b']);
 assert.deepEqual(keys({planning:{team:'Finance'},sort:'impact'}),['a','b']);
 assert.deepEqual(keys({planning:{owner:'',target_quarter:''}}),['c']);
 assert.deepEqual(keys({query:'ada 2027-q1'}),['a']);
});

test('shortlist downloads keep displayed order, saved revisions, zero values and spreadsheet-safe labels',()=>{
 const scope={state:'idea',planning:{owner:'  =formula()'},sort:'title',query:'Renewals',quadrant:''};
 const displayed=[{...ideas[0],revision:4,state:'idea',description:'private detail',planning:{owner:scope.planning.owner,team:'A,B'}},
   {...ideas[2],revision:3,state:'idea',title:'\n@comment'}];
 const packet=shortlistRecord(displayed,scope,5,'2026-11-23T00:00:00Z');
 assert.equal(packet.scope,scope);
 assert.deepEqual(Array.from(packet.ideas,i=>[i.idea_id,i.revision,i.modeled_hours_per_month]),[['b',4,0],['c',3,null]]);
 assert.equal(packet.selected_state_ideas,5);assert.equal(packet.shortlist_ideas,2);
 assert.equal(packet.execution_authorized,false);assert.equal(packet.savings_claimed,false);
 assert.doesNotMatch(JSON.stringify(packet),/private detail/);
 const csv=shortlistCsv(packet);
 assert.match(csv,/b,4,Review renewals,idea,'  =formula\(\),"A,B",,80,0,quick_win/);
 assert.match(csv,/c,3,"'\n@comment",idea/);
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
