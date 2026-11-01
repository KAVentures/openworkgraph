import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const view = require('../../dashboard/event_log_quality_trends.js');

const report = (batch, status, rejected, rate, delta) => ({
  batch, metrics: {
    rejected_input_rows: {count: rejected, denominator: 10, rate},
    duplicates_skipped_on_append: {count: 0, denominator: 10, rate: 0},
    events_without_resource: {count: 1, denominator: 10, rate: 0.1},
    events_sharing_timestamp: {count: 0, denominator: 10, rate: 0},
    future_dated_events: {count: 1, denominator: 10, rate: 0.1}
  }, comparison: {status, delta_percentage_points: {
    rejected_input_rows: delta, duplicates_skipped_on_append: 0,
    events_without_resource: 0, events_sharing_timestamp: 0, future_dated_events: 0
  }}
});

test('counts-only quality report shows known denominators and safe same-transform delta', () => {
  const data = {batches:[report(0,'first_report',2,0.2,null),report(1,'same_transform',3,0.3,10)]};
  const rows=view.rows(data);
  assert.equal(rows.length,10);
  assert.deepEqual(rows[0],{batch:'Import',metric:'Rejected input',count:'2',
    denominator:'10',rate:'20.00%',change:'Not comparable',comparison:'First report'});
  assert.deepEqual(rows[5],{batch:'Append 1',metric:'Rejected input',count:'3',
    denominator:'10',rate:'30.00%',change:'+10.00 pp',comparison:'Same transform (descriptive)'});
  assert.equal(rows[9].change,'Not comparable'); // future-time reference changes by batch
  assert.equal(rows[9].metric,'Future-dated events');
});

test('changed mapping, missing reports and unknown counts never become numeric changes', () => {
  const first=report(0,'first_report',0,0,null);
  const second=report(2,'missing_intermediate_report',null,null,null);
  second.metrics.rejected_input_rows.denominator=null;
  const third=report(3,'transform_changed',1,0.1,null);
  const rows=view.rows({batches:[first,second,third]});
  assert.equal(rows[5].rate,'Unknown');
  assert.equal(rows[5].count,'Unknown');
  assert.equal(rows[5].denominator,'Unknown');
  assert.equal(rows[5].change,'Not comparable');
  assert.equal(rows[5].comparison,'Missing intermediate report');
  assert.equal(rows[10].comparison,'Mapping changed');
  assert.equal(rows[10].change,'Not comparable');
  assert.equal(view.rows(null).length,0);
});

test('display is bounded and uses last 100 batches, never silently treating omissions as full history', () => {
  const data={batches:Array.from({length:120},(_,i)=>report(i,'same_transform',0,0,0))};
  const rows=view.rows(data);
  assert.equal(view.MAX_VISIBLE,100);
  assert.equal(rows.length,500);
  assert.equal(rows[0].batch,'Append 20');
  assert.equal(rows.at(-1).batch,'Append 119');
  assert.equal(view.csv(data).split('\r\n').length,502);
});

test('CSV includes only displayed counts, and escapes formula/quote injection', () => {
  const data={batches:[report(0,'first_report',2,0.2,null)]};
  const csv=view.csv(data);
  assert.match(csv,/"Import","Rejected input","2","10","20.00%"/);
  assert.ok(!csv.includes('alice'));
  assert.equal(view.csvCell('=HYPERLINK("https://evil")'),'\"\'=HYPERLINK(\"\"https://evil\"\")\"');
  assert.equal(view.csvCell(' +SUM(A1:A2)'),'\"\' +SUM(A1:A2)\"');
  assert.equal(view.csvCell('plain'),'\"plain\"');
});

test('malformed rates and counts cannot become misleading zeros or percentages', () => {
  for(const x of [null,undefined,NaN,Infinity,-1,1.1,'0.4']) assert.equal(view.rate(x),'Unknown');
  for(const x of [null,undefined,NaN,Infinity,-1,'2',2.2]) assert.equal(view.count(x),'Unknown');
  assert.equal(view.rate(0),'0.00%');
  assert.equal(view.rate(1),'100.00%');
  assert.equal(view.change(0),'0.00 pp');
  assert.equal(view.change(null),'Not comparable');
});

test('on-demand local read renders counts as text and rejects stale log responses', async () => {
  const {readFileSync} = await import('node:fs');
  const {runInNewContext} = await import('node:vm');
  class FakeElement {
    constructor(tag) {this.tag=tag;this.children=[];this.listeners={};this.isConnected=true;this.style={};this.textContent='';this.value='';}
    append(...items){this.children.push(...items);}
    appendChild(item){this.children.push(item);return item;}
    replaceChildren(...items){this.children=[...items];}
    addEventListener(type,callback){this.listeners[type]=callback;}
    focus(){this.focused=true;}
    setAttribute(key,value){this[key]=value;}
    querySelector(selector){
      const id=selector.startsWith('#')?selector.slice(1):selector;
      for(const child of this.children){if(child.id===id)return child;const found=child.querySelector?.(selector);if(found)return found;}
      return null;
    }
  }
  Object.defineProperty(FakeElement.prototype,'innerHTML',{set(){throw new Error('unsafe HTML rendering');}});
  const quality=new FakeElement('section');quality.id='owgQuality';
  const picker=new FakeElement('select');picker.value='log_123';
  const document={
    readyState:'complete',body:new FakeElement('body'),
    createElement:tag=>new FakeElement(tag),
    getElementById:id=>id==='owgQuality'?quality:id==='owgEventLogPick'?picker:null
  };
  let resolveFetch,requested='';
  const source=readFileSync(new URL('../../dashboard/event_log_quality_trends.js',import.meta.url),'utf8');
  runInNewContext(source,{document,MutationObserver:class {observe(){}},
    fetch:path=>{requested=path;return new Promise(resolve=>{resolveFetch=resolve;});}});
  const run=quality.querySelector('#owgQualityTrendsRun');
  const download=quality.querySelector('#owgQualityTrendsCsv');
  const output=quality.querySelector('#owgQualityTrendsTable');
  assert.ok(run && download && output);
  assert.equal(requested,''); // no implicit data fetch
  const first=run.listeners.click();
  assert.equal(requested,'/v1/event-logs/log_123/quality');
  assert.equal(download.disabled,true);
  picker.value='log_other';
  resolveFetch({ok:true,json:async()=>({batch_trends:{batches:[report(0,'first_report',2,0.2,null)]}})});
  await first;
  assert.equal(output.children.length,0); // stale data never rendered
  assert.equal(download.disabled,true);
  assert.equal(run.disabled,false); // a stale request cannot strand the control
  picker.value='log_123';
  const second=run.listeners.click();
  resolveFetch({ok:true,json:async()=>({
    batch_trends:{batches:[report(0,'first_report',2,0.2,null)],batches_without_report:[],
      review_queue:[{batch:0,status:'recorded_issue',flags:['rejected_input_rows'],unknown_fields:[]}]},
    reports:[{batch:0,created_at:'2026-11-01T18:00:00Z',
      transform:{transform_sha256:'b'.repeat(64)},
      rows:{accepted:8,rejected:2,rejected_by_reason:{'=unsafe':2},
        reason_counts_can_overlap:false}}]
  })});
  await second;
  assert.equal(download.disabled,false);
  assert.ok(output.querySelector('#owgQualityTrendsRows'));
  assert.equal(output.querySelector('#owgQualityTrendsRows').children[1].children[0].children[1].textContent,'Rejected input');
  const reviewTable=quality.querySelector('#owgQualityReviewTable');
  assert.ok(reviewTable);
  const reviewRow=reviewTable.children[1].children[0];
  assert.equal(reviewRow.children[1].textContent,'Recorded issue — inspect');
  assert.equal(reviewRow.children[2].textContent,'Rejected input');
  const inspect=reviewRow.children[4].children[0];
  assert.equal(inspect.disabled,false);
  inspect.listeners.click();
  const select=quality.querySelector('#owgQualityBatchSelect');
  const details=quality.querySelector('#owgQualityBatchDetails');
  assert.equal(select.disabled,false);
  assert.equal(select.focused,true);
  assert.equal(select.value,'0');
  assert.match(details.children[0].textContent,/Rejected 2/);
  assert.match(details.children[1].textContent,/recorded categories/);
  assert.equal(details.children[2].children[0].children[1].children[0].children[0].textContent,'=unsafe');
  picker.value='log_other';
  picker.listeners.change();
  assert.equal(details.children.length,0);
  assert.equal(select.disabled,true);
  assert.equal(download.disabled,true);
  assert.equal(output.children.length,0);
});

test('batch drill-down shows bounded, counts-only rejection categories with overlap warning', () => {
  const reasons = Object.fromEntries(Array.from({length:17},(_,i)=>['reason_'+String(i).padStart(2,'0'),i]));
  reasons['=FORMULA()']=99;
  const reports=[{batch:2,created_at:'2026-11-01T18:00:00Z',
    transform:{transform_sha256:'a'.repeat(64)},
    rows:{accepted:20,rejected:10,duplicates_skipped_on_append:3,
      rejected_by_reason:reasons,reason_counts_can_overlap:true}}];
  const d=view.detail(reports,2);
  assert.equal(d.batch,'Append 2');
  assert.equal(d.created_at,'2026-11-01T18:00:00Z');
  assert.equal(d.transform,'aaaaaaaaaaaa');
  assert.equal(d.accepted,'20');
  assert.equal(d.rejected,'10');
  assert.equal(d.skipped,'3');
  assert.equal(d.reasons.length,view.MAX_REASONS);
  assert.equal(d.omitted,6);
  assert.equal(d.reasons[0].name,'=FORMULA()');
  assert.equal(d.reasons[0].count,'99');
  assert.match(d.reason_status,/may overlap/);
  assert.equal(view.detail(reports,1),null);
  assert.equal(view.detail(reports,'2'),null);
  assert.equal(view.detail([{batch:0,rows:{accepted:1,rejected:0}}],0).reason_status,
    'Rejection reasons were not recorded for this batch.');
  assert.equal(view.detail([{batch:0,rows:{rejected_by_reason:{x:Infinity,y:-1,z:3}}}],0)
    .reasons.find(r=>r.name==='x').count,'Unknown');
});


test('quality review queue distinguishes observed problems, unknowns and missing reports', () => {
  const data={review_queue:[
    {batch:0,status:'no_recorded_issue',flags:[],unknown_fields:[]},
    {batch:1,status:'recorded_issue',flags:['rejected_input_rows','events_without_resource'],unknown_fields:[]},
    {batch:2,status:'missing_report',flags:[],unknown_fields:['quality_report']},
    {batch:3,status:'incomplete_evidence',flags:[],unknown_fields:['transform_unverified',
      'comparison_missing_intermediate_report']},
  ]};
  const rows=view.reviewRows(data);
  assert.equal(rows.length,4);
  assert.match(rows[0].status,/not source-complete/);
  assert.equal(rows[1].flags,'Rejected input, Missing resources');
  assert.equal(rows[2].status,'Missing quality report');
  assert.equal(rows[2].unknown,'Quality report missing');
  assert.equal(rows[3].flags,'None recorded');
  assert.match(rows[3].unknown,/Missing intermediate report/);
  assert.deepEqual(view.reviewRows({review_queue:[{batch:-1},{batch:'1'},null]}),[]);
  assert.equal(view.reviewRows({review_queue:Array.from({length:150},(_,batch)=>({
    batch,status:'incomplete_evidence',flags:[],unknown_fields:[]}))}).length,100);
  const hostile=view.reviewRows({review_queue:[{batch:5,status:'<script>',flags:['=HYPERLINK()'],
    unknown_fields:['<img>']}]})[0];
  assert.equal(hostile.status,'Unknown review status');
  assert.equal(hostile.flags,'None recorded');
  assert.equal(hostile.unknown,'None');
});
