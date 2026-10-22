import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Minimal DOM exercises the installed production handlers, including rerenders.
class Element {
  constructor(tag, document) { this.tag=tag;this.document=document;this.children=[];this.attrs={};this.handlers={};this.textContent=''; }
  append(...children) {this.children.push(...children);}
  appendChild(child) {this.append(child);return child;}
  prepend(child) {this.children.unshift(child);}
  replaceChildren(...children) {this.children=children;}
  setAttribute(key,value) {this.attrs[key]=value;}
  addEventListener(name,handler) {this.handlers[name]=handler;}
  querySelector() {return null;}
  focus() {this.document.activeElement=this;}
  scrollIntoView() {}
  get text() {return this.textContent+this.children.map(child=>child.text).join(' ');}
  walk() {return [this,...this.children.flatMap(child=>child.walk())];}
}
function setup(report) {
  const document={readyState:'complete',activeElement:null};
  const root=new Element('main',document);root.id='panel-evidence';
  document.getElementById=id=>root.walk().find(element=>element.id===id);
  document.querySelector=()=>null;
  document.createElement=tag=>new Element(tag,document);
  document.createElementNS=(_,tag)=>new Element(tag,document);
  const calls=[];
  const window={__owgAuthReady:Promise.resolve()};
  vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/process_mining.js',import.meta.url),'utf8'),{
    document,window,URLSearchParams,fetch:async(...args)=>{calls.push(args);return {ok:true,json:async()=>typeof report==='function'?report(...args):report};},
  });
  const find=(tag,text)=>root.walk().find(element=>element.tag===tag&&element.textContent===text);
  return {document,root,calls,find,window};
}
const edge=(from,to,count,gap)=>({from,to,transition_count:count,case_count:1,median_elapsed_gap_seconds:gap,max_elapsed_gap_seconds:gap});
const example=(label,steps)=>({case:label,event_count:steps.length,duration_seconds:60,
  events:steps.map((activity,i)=>({position:i+1,activity,observed_at:'2026-10-20T10:00:00Z',elapsed_since_previous_seconds:i?10:null}))});

test('variant selection scopes transitions and case examples; repeated SVG nodes support keyboard inspection',async()=>{
  const report={format:'openworkgraph.process-mining-csv-preview.v1',case_count:2,input_rows:6,metrics:{},
    // Deliberate global-only edge must never be rendered in a selected variant.
    transitions:[edge('GLOBAL','OTHER',9,999)],variants:[
      {case_count:1,observed_path:['Open','Review','Review','Close'],median_case_elapsed_seconds:60,
        activities:[{activity:'Open',event_count:1,case_count:1},{activity:'Review',event_count:2,case_count:1}],
        transitions:[edge('Open','Review',1,10),edge('Review','Review',1,20),edge('Review','Close',1,30)],
        case_examples:[example('Case 1',['Open','Review','Review','Close'])]},
      {case_count:1,observed_path:['Open','Close'],median_case_elapsed_seconds:300,
        activities:[{activity:'Open',event_count:1,case_count:1}],transitions:[edge('Open','Close',1,300)],
        case_examples:[example('Case 2',['Open','Close'])]},
    ]};
  const {document,root,calls,find}=setup(report);
  const file={size:100};document.getElementById('owgProcessFile').files=[file];
  document.getElementById('owgProcessFile').handlers.change();
  await find('button','Analyze selected CSV').handlers.click();
  assert.equal(calls.length,1);assert.equal(calls[0][1].body,file);
  assert(!root.text.includes('GLOBAL'));
  assert(root.text.includes('Case 1'));assert(!root.text.includes('Case 2'));
  assert.equal(root.walk().filter(e=>e.tag==='g'&&e.attrs.role==='button').length,4);
  const node=root.walk().find(e=>e.attrs['aria-label']==='Inspect event 2: Review');
  let prevented=false;
  node.handlers.keydown({key:' ',preventDefault(){prevented=true;}});
  assert(prevented);
  assert.equal(document.activeElement.id,'owgProcessActivityDetails');
  assert(document.activeElement.text.includes('2 recorded events'));
  assert.equal(root.walk().filter(e=>e.tag==='strong'&&e.text.includes('selected activity')).length,2);
  find('button','Open · 1 events').handlers.click();
  assert.equal(root.walk().filter(e=>e.tag==='tbody')[0].children.length,1);
  find('button','All activities').handlers.click();
  assert.equal(root.walk().filter(e=>e.tag==='tbody')[0].children.length,3);
  find('button','Variant 2 · 1 cases').handlers.click();
  assert(!document.getElementById('owgProcessActivityDetails'));
  assert(!root.text.includes('Case 1'));assert(root.text.includes('Case 2'));
  assert(root.text.includes('5.0 min'));
  assert.equal(root.walk().filter(e=>e.tag==='tbody')[0].children.length,1);
  assert.equal(calls.length,1);
});

test('source projections reuse analysis without I/O or retention and draft with partial source provenance',async()=>{
  const projection={format:'openworkgraph.process-mining-csv-preview.v1',case_count:1,input_rows:2,activity_count:2,variant_count:1,
    metrics:{},transitions:[edge('Review','Merged',1,60)],variants:[{case_count:1,observed_path:['Review','Merged'],
    activities:[{activity:'Review',event_count:1,case_count:1}],transitions:[edge('Review','Merged',1,60)],
    case_examples:[example('Case 1',['Review','Merged'])]}]};
  let resolveCSV;const pendingCSV=new Promise(resolve=>resolveCSV=resolve);
  const {window,document,root,calls,find}=setup(()=>pendingCSV);
  document.getElementById('owgProcessFile').files=[{size:100}];
  document.getElementById('owgProcessFile').handlers.change();
  const pending=find('button','Analyze selected CSV').handlers.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,1);
  let drafted;
  window.owgProcessCatalog={draftObserved:value=>drafted=value};
  window.owgProcessDesigner={open:()=>assert.fail('Source histories must not acquire CSV provenance')};
  assert.equal(window.owgProcessMining.showSourcePreview(projection),true);
  assert(root.text.includes('Full supplied source projection'));
  assert(document.getElementById('owgLogSave').disabled);
  document.getElementById('owgLogConsent').checked=true;document.getElementById('owgLogConsent').handlers.change();
  assert(document.getElementById('owgLogSave').disabled);
  find('button','Design improved TO-BE workflow').handlers.click();
  assert.equal(drafted.basis,'selected_source_audit');assert.equal(drafted.partial,true);
  assert.equal(drafted.path.join(','),'Review,Merged');
  resolveCSV({...projection,case_count:999});await pending;
  assert(root.text.includes('Full supplied source projection'));
  assert(!root.text.includes('999'));
  assert.equal(calls.length,1);
});

test('drift review requires distinct snapshots and a declaration; reference changes discard pending results',async()=>{
  let finish;
  const pending=new Promise(resolve=>finish=resolve);
  const a='log_'+'a'.repeat(32),b='log_'+'b'.repeat(32);
  const {window,document,root,calls,find}=setup(url=>url.includes('/compare?')?pending:
    {datasets:[{dataset_id:a,case_count:35,created_at:'2026-10-20T00:00:00Z'},
      {dataset_id:b,case_count:36,created_at:'2026-10-21T00:00:00Z'}]});
  assert.equal(calls.length,0);
  await find('button','List saved local logs').handlers.click();
  const baseline=document.getElementById('owgDriftBaseline'),current=document.getElementById('owgDriftCurrent'),
    consent=document.getElementById('owgDriftConsent'),compare=document.getElementById('owgDriftCompare');
  baseline.value=a;baseline.handlers.change();current.value=a;current.handlers.change();
  consent.checked=true;consent.handlers.change();assert.equal(compare.disabled,true);
  current.value=b;current.handlers.change();assert.equal(consent.checked,false);
  assert.equal(compare.disabled,true);
  window.owgProcessMining.setReference({workflow_id:'reviewed_process',revision:3});
  consent.checked=true;consent.handlers.change();assert.equal(compare.disabled,false);
  const comparing=compare.handlers.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(compare.disabled,true);assert.equal(calls.length,2);
  const query=new URL(calls[1][0],'http://localhost').searchParams;
  assert.equal(query.get('baseline_id'),a);assert.equal(query.get('current_id'),b);
  assert.equal(query.get('comparability_confirmed'),'true');assert.equal(query.get('revision'),'3');
  assert.equal(query.get('workflow_id'),'reviewed_process');
  window.owgProcessMining.setReference({workflow_id:'reviewed_process',revision:4});
  assert.equal(consent.checked,false);
  finish({format:'openworkgraph.process-log-drift.v1',summary:{current_only_path_count:999}});
  await comparing;
  assert.equal(document.getElementById('owgDriftResults').children.length,0);
  assert(!root.text.includes('999'));
  assert.equal(compare.disabled,true);
  assert.equal(calls.length,2);
});

test('case cohort inputs are explicit; changing source discards pending cohort results and disables controls',async()=>{
  let finish;
  const pending=new Promise(resolve=>finish=resolve),id='log_'+'a'.repeat(32);
  const report={format:'openworkgraph.process-mining-csv-preview.v1',dataset_id:id,import_retained:true,
    case_count:35,input_rows:70,activity_count:2,variant_count:1,metrics:{},variants:[],activities:[],transitions:[]};
  const {document,root,calls,find}=setup(url=>url.includes('/cohort?')?pending:url.includes('/report?')?report:
    url.includes('/cases?')?{cases:[]}:{datasets:[{dataset_id:id,case_count:35,created_at:'2026-10-21T00:00:00Z'}]});
  const apply=document.getElementById('owgCohortApply');
  assert.equal(apply.disabled,true);assert.equal(calls.length,0);
  await find('button','List saved local logs').handlers.click();
  await find('button','Open '+id).handlers.click();
  assert.equal(apply.disabled,false);
  const start=document.getElementById('owgCohortSince'),activity=document.getElementById('owgCohortActivity');
  start.value='2026-10-21T00:00:00Z';start.handlers.input();
  activity.value='Review';activity.handlers.input();
  assert.equal(calls.length,3); // inventory, whole-log report, case page
  const filtering=apply.handlers.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,4);assert.equal(apply.disabled,true);
  const params=new URL(calls[3][0],'http://localhost').searchParams;
  assert.equal(params.get('dataset_id'),id);assert.equal(params.get('contains_activity'),'Review');
  assert.equal(params.get('started_since'),'2026-10-21T00:00:00Z');
  const file=document.getElementById('owgProcessFile');file.files=[{size:100}];file.handlers.change();
  finish({format:'openworkgraph.process-log-cohort.v1',report:{...report,case_count:999},selection:{selected_case_count:999}});
  await filtering;
  assert.equal(document.getElementById('owgProcessResults').children.length,0);
  assert.equal(document.getElementById('owgLogCases').children.length,0);
  assert(!root.text.includes('999'));
  assert.equal(apply.disabled,true);assert.equal(activity.disabled,true);assert.equal(activity.value,'');
  assert.equal(calls.length,4);
});
