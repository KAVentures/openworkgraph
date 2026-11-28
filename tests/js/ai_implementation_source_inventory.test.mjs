import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inspectEvidenceSources,appendEvidenceSource} from '../../dashboard/ai_implementation_source_inventory.js';
const rid='execution:'+'a'.repeat(16),eid='event:'+'b'.repeat(16);
function source({run=rid,event=eid,limit=false,extra=0,marker=''}={}){return {format:'openworkgraph.workflow-evidence.v1',canonical_evidence_included:true,provenance:{source_limit_reached:limit,marker},executions:[{execution_id:run}],canonical_evidence:[{execution_id:run,events:[{event_ref:event,metadata:{marker}}],observed_event_count:1+extra,returned_event_count:1,events_truncated:!!extra,event_membership_verified:false}]};}
function file(value,name='source.json'){const b=new Blob([JSON.stringify(value)],{type:'application/json'});Object.defineProperty(b,'name',{value:name});return b;}
test('append preserves exact ordered original file objects and bytes',async()=>{
  const a=file(source()),b=file(source({marker:'å second',limit:true,extra:1}),'second.json'),original=[a];const next=await appendEvidenceSource(original,b);assert.deepEqual(next,[a,b]);assert.deepEqual(original,[a]);assert.equal(await next[1].text(),await b.text());const r=await inspectEvidenceSources(next);assert.equal(r.totalBytes,a.size+b.size);assert.equal(r.executionOccurrences,2);assert.equal(r.eventOccurrences,2);assert.equal(r.sources[1].sourceLimitReached,true);assert.equal(r.sources[1].truncated,1);assert.deepEqual(r.overlaps,[{left:1,right:2,repeatedExecutionIds:1,repeatedRunEventRefs:1}]);assert.equal(r.representativeCoverageVerified,false);
});
test('repeated run with different event remains a warning, not deduplication',async()=>{
  const r=await inspectEvidenceSources([file(source()),file(source({event:'event:'+'c'.repeat(16)}))]);assert.deepEqual(r.overlaps,[{left:1,right:2,repeatedExecutionIds:1,repeatedRunEventRefs:0}]);assert.equal(r.crossBundleMembershipVerified,false);assert.equal(r.eventOccurrences,2);
});
test('disjoint sources and empty inventory',async()=>{
  const r=await inspectEvidenceSources([file(source()),file(source({run:'execution:'+'d'.repeat(16)}))]);assert.deepEqual(r.overlaps,[]);assert.equal(r.sourceCount,2);assert.equal((await inspectEvidenceSources([])).sourceCount,0);
});
test('exact duplicates and malformed sources refuse without changing existing list',async()=>{
  const a=file(source()),list=[a];await assert.rejects(appendEvidenceSource(list,file(source())),/already selected/);await assert.rejects(appendEvidenceSource(list,file({})),/canonical workflow/);assert.deepEqual(list,[a]);
  const bad=source();bad.canonical_evidence[0].events.push({event_ref:eid});bad.canonical_evidence[0].observed_event_count=bad.canonical_evidence[0].returned_event_count=2;await assert.rejects(inspectEvidenceSources([file(bad)]),/Duplicate event/);
});
test('source and byte limits checked before file reads',async()=>{
  const a=file(source());await assert.rejects(appendEvidenceSource(Array(8).fill(a),a),/eight/);const huge={size:8*1024*1024,arrayBuffer(){throw Error('must not read');}};await assert.rejects(appendEvidenceSource([a],huge),/8 MiB/);await assert.rejects(inspectEvidenceSources([new Blob([new Uint8Array([0xff])])]),/encoded data|UTF-8/i);
});
