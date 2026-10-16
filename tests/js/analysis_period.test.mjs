import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../dashboard/analysis_handoff.js',import.meta.url),'utf8');
function periodAt(instant,zone) {
  const previous=process.env.TZ;process.env.TZ=zone;
  try {
    class FixedDate extends Date { constructor(...args) {super(...(args.length?args:[instant]));} }
    const window={};
    vm.runInNewContext(source,{window,document:{readyState:'loading',addEventListener(){}},Date:FixedDate,Intl});
    return JSON.parse(JSON.stringify(window.owgEvidenceSelection()));
  } finally {if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
}
test('last seven local calendar days span 169 hours across the New York DST change',()=>{
  const p=periodAt('2026-11-01T12:00:00Z','America/New_York');
  assert.equal(p.since,'2026-10-26T04:00:00.000Z');
  assert.equal(p.until,'2026-11-02T05:00:00.000Z');
  assert.equal((Date.parse(p.until)-Date.parse(p.since))/3600000,169);
});
test('fractional-offset timezones retain local calendar boundaries',()=>{
  const p=periodAt('2026-10-16T12:00:00Z','Asia/Kathmandu');
  assert.equal(p.since,'2026-10-09T18:15:00.000Z');
  assert.equal(p.until,'2026-10-16T18:15:00.000Z');
});
test('UTC period includes today and six previous days without requesting all history',()=>{
  const p=periodAt('2026-10-16T12:00:00Z','UTC');
  assert.equal(p.scope,'range');
  assert.equal(p.since,'2026-10-10T00:00:00.000Z');
  assert.equal(p.until,'2026-10-17T00:00:00.000Z');
});
