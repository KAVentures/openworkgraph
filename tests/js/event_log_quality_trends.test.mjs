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
