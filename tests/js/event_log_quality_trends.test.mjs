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


test('selected baseline and target compare all five metrics across contiguous identical transforms', () => {
  const digest='a'.repeat(64);
  const first={...report(0,'first_report',2,0.2,null),transform_sha256:digest};
  const middle={...report(1,'same_transform',3,0.3,10),transform_sha256:digest};
  const last={...report(2,'same_transform',4,0.4,10),transform_sha256:digest};
  const result=view.comparePair({batches:[first,middle,last]},0,2);
  assert.match(result.status,/Same verified transform across 3 contiguous/);
  assert.equal(result.baseline,'Import');
  assert.equal(result.target,'Append 2');
  assert.equal(result.rows.length,5);
  assert.equal(result.rows[0].baseline_count,'2');
  assert.equal(result.rows[0].target_count,'4');
  assert.equal(result.rows[0].baseline_denominator,'10');
  assert.equal(result.rows[0].target_rate,'40.00%');
  assert.equal(result.rows[0].change,'+20.00 pp');
  assert.equal(result.rows[4].change,'Not comparable');
  assert.match(result.rows[4].reason,/Different report reference times/);
  assert.equal(view.comparePair({batches:[first,middle,last]},1,2).rows[0].change,'+10.00 pp');
});

test('selected batch comparison refuses gaps, changed or unknown mappings and invalid selection', () => {
  const digest='a'.repeat(64);
  const first={...report(0,'first_report',2,0.2,null),transform_sha256:digest};
  const middle={...report(1,'same_transform',3,0.3,10),transform_sha256:digest};
  const last={...report(2,'same_transform',4,0.4,10),transform_sha256:digest};
  const compare=(batches,a=0,b=2)=>view.comparePair({batches},a,b);
  assert.match(compare([first,last]).status,/Missing intermediate/);
  assert.equal(compare([first,last]).rows.length,0);
  assert.match(compare([first,{...middle,transform_sha256:'b'.repeat(64)},last]).status,/Mapping changed/);
  assert.match(compare([first,{...middle,transform_sha256:null},last]).status,/Transform digest unknown/);
  assert.match(compare([first,middle,last],0,0).status,/different batches/);
  assert.match(compare([first,middle,last],2,0).status,/earlier baseline/);
  assert.match(compare([first,middle,last],-1,2).status,/valid stored/);
  assert.match(compare([first,middle,last],0,9).status,/outside the displayed/);
  assert.match(compare([first,middle,middle,last]).status,/Duplicate batch/);
  assert.match(compare([{...first,batch:'0'},middle,last]).status,/outside the displayed/);
  const large=Array.from({length:102},(_,batch)=>({...report(batch,'same_transform',2,0.2,0),transform_sha256:digest}));
  assert.match(compare(large,0,101).status,/exceeds the/);
  assert.match(compare(large,1,101).status,/exceeds the/);
  assert.equal(compare(large,2,101).rows.length,5);
});

test('unknown or invalid denominators suppress only affected selected-pair changes', () => {
  const digest='a'.repeat(64);
  const first={...report(0,'first_report',2,0.2,null),transform_sha256:digest};
  const last={...report(1,'same_transform',4,0.4,20),transform_sha256:digest};
  last.metrics.rejected_input_rows.denominator=null;
  last.metrics.events_without_resource.count=11; // cannot exceed denominator 10
  const result=view.comparePair({batches:[first,last]},0,1);
  assert.equal(result.rows.length,5);
  assert.equal(result.rows[0].target_rate,'Unknown');
  assert.equal(result.rows[0].change,'Not comparable');
  assert.match(result.rows[0].reason,/Missing or invalid/);
  assert.equal(result.rows[2].change,'Not comparable');
  assert.equal(result.rows[1].change,'0.00 pp');
  assert.equal(view.comparePair(null,0,1).rows.length,0);
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
    click(){this.clicked=true;}
    remove(){this.removed=true;}
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
  let resolveFetch,requested='',exportedCsv='';
  const source=readFileSync(new URL('../../dashboard/event_log_quality_trends.js',import.meta.url),'utf8');
  runInNewContext(source,{document,MutationObserver:class {observe(){}},
    Blob:class {constructor(parts){exportedCsv=parts.join('');}},
    URL:{createObjectURL:()=> 'blob:local',revokeObjectURL:()=>{}},setTimeout:()=>{},
    fetch:path=>{requested=path;return new Promise(resolve=>{resolveFetch=resolve;});}});
  const run=quality.querySelector('#owgQualityTrendsRun');
  const download=quality.querySelector('#owgQualityTrendsCsv');
  const output=quality.querySelector('#owgQualityTrendsTable');
  assert.ok(run && download && output);
  assert.equal(requested,''); // no implicit data fetch
  const first=run.listeners.click();
  assert.equal(requested,'/v1/event-logs/log_123/quality/batch-history/page');
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
    batch_trends:{batches:[
      {...report(0,'first_report',2,0.2,null),transform_sha256:'b'.repeat(64)},
      {...report(1,'same_transform',3,0.3,10),transform_sha256:'b'.repeat(64)}
    ],batches_without_report:[],
      review_queue:[{batch:0,status:'recorded_issue',flags:['rejected_input_rows'],unknown_fields:[]}]},
    reports:[{batch:0,created_at:'2026-11-01T18:00:00Z',
      transform:{transform_sha256:'b'.repeat(64)},
      rows:{accepted:8,rejected:2,rejected_by_reason:{'=unsafe':2},
        reason_counts_can_overlap:false}},
      {batch:1,created_at:'2026-11-02T18:00:00Z',
        transform:{transform_sha256:'b'.repeat(64)},
        rows:{accepted:7,rejected:3,rejected_by_reason:{'=unsafe':3},
          reason_counts_can_overlap:false}}]
  })});
  await second;
  assert.equal(download.disabled,false);
  const baseline=quality.querySelector('#owgQualityPairBaseline');
  const target=quality.querySelector('#owgQualityPairTarget');
  const pair=quality.querySelector('#owgQualityPairRows');
  assert.ok(baseline && target && pair);
  assert.equal(baseline.disabled,false);
  assert.equal(target.disabled,false);
  assert.equal(baseline.value,'0');
  assert.equal(target.value,'1');
  assert.match(pair.children[0].textContent,/Same verified transform/);
  assert.equal(pair.querySelector('#owgQualityPairTable').children[1].children[0].children[7].textContent,'+10.00 pp');
  const reasonPairTable=quality.querySelector('#owgQualityPairReasonTable');
  const reasonPairCsv=quality.querySelector('#owgQualityPairReasonCsv');
  const reasonPairRows=quality.querySelector('#owgQualityPairReasonRows');
  assert.ok(reasonPairTable && reasonPairCsv && reasonPairRows);
  assert.equal(reasonPairCsv.disabled,false);
  assert.equal(reasonPairTable.children[1].children[0].children[0].textContent,'=unsafe');
  assert.equal(reasonPairTable.children[1].children[0].children[7].textContent,'+10.00 pp');
  reasonPairCsv.listeners.click();
  assert.match(exportedCsv,/"'=unsafe"/);
  assert.match(exportedCsv,/baseline_supplied_rows/);
  assert.ok(!exportedCsv.includes('alice'));

  target.value='0';target.listeners.change();
  assert.match(pair.children[0].textContent,/different batches/);
  assert.equal(pair.querySelector('#owgQualityPairTable'),null);
  assert.equal(reasonPairCsv.disabled,true);
  assert.equal(reasonPairRows.querySelector('#owgQualityPairReasonTable'),null);
  target.value='1';target.listeners.change();
  assert.ok(pair.querySelector('#owgQualityPairTable'));
  assert.equal(reasonPairCsv.disabled,false);
  assert.ok(output.querySelector('#owgQualityTrendsRows'));
  assert.equal(output.querySelector('#owgQualityTrendsRows').children[1].children[0].children[1].textContent,'Rejected input');
  const increaseThreshold=quality.querySelector('#owgQualityIncreaseThreshold');
  const increaseRows=quality.querySelector('#owgQualityIncreaseRows');
  assert.ok(increaseThreshold && increaseRows);
  assert.equal(increaseThreshold.value,'5');
  const increaseTable=quality.querySelector('#owgQualityIncreaseTable');
  assert.ok(increaseTable);
  const increaseRow=increaseTable.children[1].children[0];
  assert.equal(increaseRow.children[1].textContent,'Rejected input');
  assert.equal(increaseRow.children[4].textContent,'+10.00 pp');
  increaseThreshold.value='20';increaseThreshold.listeners.change();
  assert.equal(quality.querySelector('#owgQualityIncreaseTable'),null);
  increaseThreshold.value='5';increaseThreshold.listeners.change();
  const increaseInspect=quality.querySelector('#owgQualityIncreaseTable')
    .children[1].children[0].children[5].children[0];
  increaseInspect.listeners.click();
  const reasonThreshold=quality.querySelector('#owgQualityReasonThreshold');
  const reasonRows=quality.querySelector('#owgQualityReasonRows');
  assert.equal(reasonThreshold.value,'1');
  const reasonTable=quality.querySelector('#owgQualityReasonTable');
  assert.ok(reasonTable);
  const reasonRow=reasonTable.children[1].children[0];
  assert.equal(reasonRow.children[1].textContent,'=unsafe'); // text, never HTML
  assert.equal(reasonRow.children[4].textContent,'+10.00 pp');
  const reasonInspect=reasonRow.children[5].children[0];
  reasonInspect.listeners.click();
  assert.equal(quality.querySelector('#owgQualityBatchSelect').value,'1');
  reasonThreshold.value='20';reasonThreshold.listeners.change();
  assert.equal(quality.querySelector('#owgQualityReasonTable'),null);
  reasonThreshold.value='1';reasonThreshold.listeners.change();
  assert.equal(quality.querySelector('#owgQualityBatchSelect').value,'1');
  assert.equal(quality.querySelector('#owgQualityBatchSelect').focused,true);
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
  assert.equal(baseline.disabled,true);
  assert.equal(target.disabled,true);
  assert.equal(pair.children.length,0);
  assert.equal(reasonPairCsv.disabled,true);
  assert.equal(reasonPairRows.children.length,0);
  assert.equal(increaseRows.children.length,0);
  assert.equal(reasonRows.children.length,0);
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

test('ranked quality rate increases are comparable, bounded and sorted without source values', () => {
  const digest='a'.repeat(64);
  const batches=[
    {...report(0,'first_report',2,0.2,null),transform_sha256:digest},
    {...report(1,'same_transform',4,0.4,20),transform_sha256:digest},
    {...report(2,'same_transform',5,0.5,10),transform_sha256:digest},
    {...report(3,'transform_changed',8,0.8,null),transform_sha256:'b'.repeat(64)},
    {...report(5,'missing_intermediate_report',9,0.9,null),transform_sha256:'b'.repeat(64)}
  ];
  const ranked=view.rankedIncreases({batches},5);
  assert.equal(ranked.examined,5);
  assert.equal(ranked.total,2);
  assert.deepEqual(ranked.rows.map(r=>[r.batch,r.metric,r.increase]),[
    ['Append 1','Rejected input','+20.00 pp'],
    ['Append 2','Rejected input','+10.00 pp']
  ]);
  assert.equal(view.rankedIncreases({batches},15).total,1);
  assert.equal(view.rankedIncreases({batches},21).total,0);
  assert.equal(view.rankedIncreases({batches},-1).total,0);
  assert.equal(view.rankedIncreases({batches},NaN).total,0);
  assert.equal(view.rankedIncreases({batches:[...batches,batches[0]]},5).total,0);
  assert.ok(ranked.rows.every(r=>!JSON.stringify(r).includes('transform_sha256')));
});

test('ranked increases suppress invalid denominators, unknown mappings and future-time comparisons', () => {
  const digest='a'.repeat(64);
  const a={...report(0,'first_report',2,0.2,null),transform_sha256:digest};
  const b={...report(1,'same_transform',5,0.5,30),transform_sha256:digest};
  b.metrics.events_without_resource.count=20; // greater than denominator
  b.metrics.duplicates_skipped_on_append.denominator=null;
  b.metrics.events_sharing_timestamp.count='9'; // hostile noninteger
  b.metrics.future_dated_events.count=9; // never comparable across report times
  const result=view.rankedIncreases({batches:[a,b]},1);
  assert.equal(result.total,1);
  assert.equal(result.rows[0].metric,'Rejected input');
  assert.equal(result.rows[0].increase,'+30.00 pp');
  assert.equal(view.rankedIncreases({batches:[a,{...b,transform_sha256:null}]},1).total,0);
  assert.equal(view.rankedIncreases({batches:[a,{...b,transform_sha256:'b'.repeat(64)}]},1).total,0);
  assert.equal(view.rankedIncreases({batches:[a,{...b,batch:2}]},1).total,0);
  const many=Array.from({length:120},(_,batch)=>({
    ...report(batch,'same_transform',batch%2?9:0,batch%2?0.9:0,0),
    transform_sha256:digest
  }));
  const bounded=view.rankedIncreases({batches:many},1);
  assert.equal(bounded.examined,100);
  assert.ok(bounded.total>0);
  assert.ok(bounded.rows.length<=view.MAX_INCREASE_ROWS);
  assert.ok(bounded.rows.every(row=>row.batchId>=20));
});

test('recorded rejection-category increases compare adjacent verified batches and show reason counts', () => {
  const digest='a'.repeat(64);
  const batch=(id,rejected,denominator=10)=>({
    batch:id,transform_sha256:digest,
    metrics:{rejected_input_rows:{count:rejected,denominator}}
  });
  const record=(id,rejected,reasons,sha=digest)=>({
    batch:id,transform:{transform_sha256:sha},
    rows:{rejected,rejected_by_reason:reasons,reason_counts_can_overlap:true}
  });
  const trends={batches:[batch(0,2),batch(1,4),batch(2,5)]};
  const reports=[record(0,2,{invalid_date:2}),record(1,4,{invalid_date:3,missing_case:1}),
    record(2,5,{invalid_date:1,missing_case:4})];
  const result=view.rejectionReasonIncreases(trends,reports,1);
  assert.equal(result.examined,3);
  assert.equal(result.comparablePairs,2);
  assert.equal(result.skippedPairs,0);
  assert.equal(result.total,3);
  assert.deepEqual(result.rows.map(r=>[r.batch,r.category,r.increase]),[
    ['Append 2','missing_case','+30.00 pp'],
    ['Append 1','missing_case','+10.00 pp'],
    ['Append 1','invalid_date','+10.00 pp']
  ]);
  assert.equal(result.rows[0].before,'1 / 10 (10.00%)');
  assert.equal(result.rows[0].after,'4 / 10 (40.00%)');
  assert.equal(view.rejectionReasonIncreases(trends,reports,15).total,1);
  assert.equal(view.rejectionReasonIncreases(trends,reports,31).total,0);
  assert.ok(result.rows.every(r=>!JSON.stringify(r).includes('transform_sha256')));
});

test('rejection-category comparisons refuse missing reports, mapping drift and invalid denominators', () => {
  const digest='a'.repeat(64),other='b'.repeat(64);
  const batch=(id,rejected=3,denominator=10,sha=digest)=>({
    batch:id,transform_sha256:sha,metrics:{rejected_input_rows:{count:rejected,denominator}}
  });
  const record=(id,rejected=3,reasons={bad:3},sha=digest)=>({
    batch:id,transform:{transform_sha256:sha},rows:{rejected,rejected_by_reason:reasons}
  });
  const good=[batch(0),batch(1)],reports=[record(0),record(1)];
  const check=(batches,rs)=>view.rejectionReasonIncreases({batches},rs,0.1);
  assert.equal(check(good,reports).comparablePairs,1);
  assert.equal(check(good,[reports[0]]).comparablePairs,0);
  assert.equal(check(good,[...reports,reports[1]]).comparablePairs,0);
  assert.equal(check([good[0],batch(1,3,10,other)],reports).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,3,{bad:3},other)]).comparablePairs,0);
  assert.equal(check([good[0],batch(1,3,null)],reports).comparablePairs,0);
  assert.equal(check([good[0],batch(1,3,0)],reports).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,3,null)]).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,3,{bad:Infinity})]).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,3,{bad:4})]).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,2,{bad:2})]).comparablePairs,0);
  assert.equal(check([batch(0),batch(2)],reports).comparablePairs,0);
  assert.equal(check([...good,good[0]],reports).comparablePairs,0);
  assert.equal(check(good,[reports[0],record(1,3,Object.fromEntries(
    Array.from({length:257},(_,i)=>['reason_'+i,0])))]).comparablePairs,0);
  assert.equal(view.rejectionReasonIncreases({batches:good},reports,-1).total,0);
  assert.equal(view.rejectionReasonIncreases({batches:good},reports,NaN).total,0);
  assert.equal(view.rejectionReasonIncreases({batches:good},null,1).total,0);
});

test('rejection-category increase rows remain bounded to the latest 100 reports', () => {
  const digest='a'.repeat(64);
  const batches=Array.from({length:120},(_,batch)=>({
    batch,transform_sha256:digest,
    metrics:{rejected_input_rows:{count:batch%2?4:1,denominator:10}}
  }));
  const reports=batches.map(({batch})=>({
    batch,transform:{transform_sha256:digest},
    rows:{rejected:batch%2?4:1,rejected_by_reason:{bad:batch%2?4:1}}
  }));
  const result=view.rejectionReasonIncreases({batches},reports,1);
  assert.equal(result.examined,100);
  assert.equal(result.comparablePairs,99);
  assert.ok(result.total>0);
  assert.ok(result.rows.length<=view.MAX_REASON_INCREASE_ROWS);
  assert.ok(result.rows.every(r=>r.batchId>=20));
});

test('selected baseline/target compares recorded rejection categories across intervening batches', () => {
  const sha='a'.repeat(64);
  const batches=[0,1,2].map((i)=>({
    ...report(i,i===0?'first_report':'same_transform',i+2,(i+2)/10,null),
    transform_sha256:sha
  }));
  const reports=[0,1,2].map((i)=>({
    batch:i,transform:{transform_sha256:sha},
    rows:{rejected:i+2,rejected_by_reason:i===0?{'=FORMULA()':2}:
      i===1?{'=FORMULA()':2}:{'=FORMULA()':1,'New reason':2}}
  }));
  const result=view.compareReasonsPair({batches},reports,0,2);
  assert.equal(result.comparable,true);
  assert.equal(result.baseline,'Import');
  assert.equal(result.target,'Append 2');
  assert.equal(result.total,2);
  assert.equal(result.omitted,0);
  assert.deepEqual(result.rows.map(r=>[r.category,r.change]),[
    ['New reason','+20.00 pp'],['=FORMULA()','-10.00 pp']
  ]);
  assert.equal(result.rows[0].baseline_count,'0');
  assert.equal(result.rows[0].target_denominator,'10');
  const csv=view.csvPairReasons(result);
  assert.match(csv,/baseline_batch,target_batch|baseline_batch/);
  assert.match(csv,/"'=FORMULA\(\)"/);
  assert.match(csv,/"New reason"/);
  assert.match(csv,/"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"/);
  assert.ok(csv.endsWith('\r\n'));
  assert.equal(csv.split('\r\n').length,4);
  assert.ok(!csv.includes('source_row'));
});

test('selected category comparison refuses missing or inconsistent evidence rather than treating it as zero', () => {
  const sha='b'.repeat(64),other='c'.repeat(64);
  const batches=[0,1,2].map(i=>({...report(i,'same_transform',2,0.2,null),transform_sha256:sha}));
  const reports=[0,1,2].map(i=>({batch:i,transform:{transform_sha256:sha},
    rows:{rejected:2,rejected_by_reason:{reason:1}}}));
  const compare=(b=batches,r=reports)=>view.compareReasonsPair({batches:b},r,0,2);
  assert.equal(compare().comparable,true);
  assert.match(compare(batches,reports.filter(r=>r.batch!==1)).status,/intermediate saved report/);
  assert.match(compare(batches,[...reports,reports[1]]).status,/Duplicate saved/);
  assert.match(compare(batches,reports.map(r=>r.batch===1?
    {...r,transform:{transform_sha256:other}}:r)).status,/inconsistent intermediate/);
  assert.match(compare(batches.map(b=>b.batch===1?
    {...b,transform_sha256:other}:b)).status,/Mapping changed/);
  assert.match(compare(batches.filter(b=>b.batch!==1)).status,/Missing intermediate/);
  assert.match(compare(batches,reports.map(r=>r.batch===2?
    {...r,rows:{rejected:2}}:r)).status,/endpoint rejection-category breakdown/);
  assert.match(compare(batches,reports.map(r=>r.batch===2?
    {...r,rows:{rejected:2,rejected_by_reason:{reason:3}}}:r)).status,/inconsistent endpoint/);
  assert.match(compare(batches.map(b=>b.batch===2?
    {...b,metrics:{...b.metrics,rejected_input_rows:{count:2,denominator:0}}}:b)).status,
    /inconsistent endpoint/);
  assert.match(compare(batches,reports.map(r=>r.batch===2?
    {...r,rows:{rejected:2,rejected_by_reason:{reason:-1}}}:r)).status,/inconsistent endpoint/);
  assert.equal(view.compareReasonsPair({batches},reports,2,0).comparable,false);
  assert.equal(view.compareReasonsPair({batches},reports,0,0).comparable,false);
});

test('selected category comparison is bounded and CSV always formula-safe', () => {
  const sha='d'.repeat(64);
  const batches=[0,1].map(i=>({...report(i,'same_transform',10,1,null),transform_sha256:sha}));
  const reasonMap=Object.fromEntries(Array.from({length:60},(_,i)=>[
    i===0?' =DANGEROUS()':'reason_'+i,1
  ]));
  const reports=[0,1].map(i=>({batch:i,transform:{transform_sha256:sha},
    rows:{rejected:10,rejected_by_reason:i===0?{}:reasonMap}}));
  const result=view.compareReasonsPair({batches},reports,0,1);
  assert.equal(result.comparable,true);
  assert.equal(result.total,60);
  assert.equal(result.rows.length,view.MAX_PAIR_REASON_ROWS);
  assert.equal(result.omitted,10);
  assert.match(view.csvPairReasons(result),/"' =DANGEROUS\(\)"/);
  assert.equal(view.compareReasonsPair({batches},reports.map(r=>r.batch===1?
    {...r,rows:{rejected:10,rejected_by_reason:Object.fromEntries(
      Array.from({length:257},(_,i)=>['reason_'+i,1]))}}:r),0,1).comparable,false);
  assert.equal(view.csvPairReasons(null).split('\r\n').length,2);
});
