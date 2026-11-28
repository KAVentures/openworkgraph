import {test} from 'node:test';
import assert from 'node:assert/strict';
import {recentWindow,nextStep,authoringFile} from '../../dashboard/ai_implementation_first_workflow.js';
const pin='a'.repeat(64),compactPin='b'.repeat(64);
const original=JSON.stringify({format:'openworkgraph.ai-implementation-evidence.v1',packet_sha256:pin,text:'Unicode å \n --- BEGIN EXACT EVIDENCE JSON (to end of file) ---'});
const compact=JSON.stringify({format:'openworkgraph.ai-implementation-evidence-compact.v1',original_packet_sha256:pin,compact_sha256:compactPin});
function fixture(){return {packet_sha256:pin,execution_authorized:false,external_model_invoked:false,external_sharing_authorized:false,packet_json:original,packet_bytes:Buffer.byteLength(original),instructions:'ORIGINAL instructions',compaction:{available:true,compact_sha256:compactPin,compact_bytes:Buffer.byteLength(compact)},compact_download:{packet_json:compact,instructions:'COMPACT instructions',external_sharing_authorized:false,token_savings_verified:false,model_quality_equivalence_verified:false}};}
test('recent presets are bounded and use the explicit click time',()=>{
  const now=Date.parse('2026-11-28T15:00:00Z');assert.deepEqual(recentWindow(4,now),{since:'2026-11-28T11:00:00.000Z',until:'2026-11-28T15:00:00.000Z'});assert.equal(Date.parse(recentWindow(168,now).until)-Date.parse(recentWindow(168,now).since),7*86400000);assert.throws(()=>recentWindow(169,now));assert.throws(()=>recentWindow(1,NaN));
});
test('actual state, no inferred progress',()=>{
  const s={sourceCount:0,packet:null,hasProposal:false,hasTitle:false,reviewed:null,createdIdea:null};assert.equal(nextStep(s),'select');s.sourceCount=1;assert.equal(nextStep(s),'prepare');s.packet=fixture();assert.equal(nextStep(s),'author');s.hasProposal=true;assert.equal(nextStep(s),'title');s.hasTitle=true;assert.equal(nextStep(s),'review');s.reviewed={};assert.equal(nextStep(s),'reviewed');s.createdIdea='idea_example';assert.equal(nextStep(s),'created');s.createdIdea=s.reviewed=s.packet=null;assert.equal(nextStep(s),'prepare');
});
test('single-file handoff uses only the exact matching instructions and bytes',()=>{
  const p=fixture();for(const [mode,raw,guide] of [['original',original,'ORIGINAL'],['compact',compact,'COMPACT']]){const f=authoringFile(p,mode);assert.ok(f.text.endsWith(raw));assert.ok(f.text.includes('Evidence JSON UTF-8 bytes: '+Buffer.byteLength(raw)+'\n'));assert.ok(f.text.includes(guide+' instructions\n'));assert.ok(!f.text.includes((guide==='ORIGINAL'?'COMPACT':'ORIGINAL')+' instructions'));assert.equal(f.name,'owg-ai-authoring-'+mode+'.txt');}
});
test('wrong pins, bytes, format or authorization refused',()=>{
  for(const mutate of [p=>p.packet_sha256='0'.repeat(64),p=>p.packet_bytes++,p=>p.external_model_invoked=true,p=>p.external_sharing_authorized=true,p=>p.instructions='']){const p=fixture();mutate(p);assert.throws(()=>authoringFile(p));}for(const mutate of [p=>p.compaction.compact_sha256='0'.repeat(64),p=>p.compact_download.token_savings_verified=true,p=>p.compact_download=null,p=>p.compaction.compact_bytes++]){const p=fixture();mutate(p);assert.throws(()=>authoringFile(p,'compact'));}
});
