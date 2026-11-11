'use strict';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const __dirname=path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(__dirname, '../../dashboard/workflow_evidence.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve};}
function setup(){
  const controls=new Map();
  const element=id=>({id,disabled:false,textContent:'',innerHTML:'',onclick:null,replaceChildren(){this.innerHTML='';this.textContent='';}});
  let host=null;
  const card={querySelector(sel){return sel==='h2'?{textContent:'Repeated workflows'}:sel==='#workflowEvidenceDrafting'?host:null;},appendChild(node){host=node;}};
  const parent={querySelector(sel){return sel==='#workflowEvidenceDrafting'?host:null;},appendChild(node){host=node;}};
  const document={readyState:'complete',querySelectorAll(sel){return sel==='#panel-overview .card'?[card]:[];},querySelector(sel){if(sel==='#panel-overview')return parent;if(sel==='#patternList')return null;if(sel==='[role="tab"][aria-selected="true"]')return {dataset:{tab:'other'}};if(sel.startsWith('#'))return controls.get(sel.slice(1))||null;return null;},createElement(){const node=element('workflowEvidenceDrafting');node.style={};node.querySelector=sel=>{const id=sel.slice(1);if(!controls.has(id))controls.set(id,element(id));return controls.get(id);};return node;}};
  const pending=[];
  const fetch=(url)=>{if(url.includes('reconstruction-review')){const d=deferred();pending.push(d);return d.promise;}return Promise.resolve({ok:true,json:async()=>({families:[]})});};
  const window={__owgAuthReady:Promise.resolve()};
  vm.runInNewContext(source,{window,document,fetch,MutationObserver:class{observe(){}},setTimeout},{filename:'workflow_evidence.js'});
  return {controls,pending,click(id){const button=controls.get(id);assert.ok(button,'missing '+id);button.onclick();return button;}};
}
const permitted={derived:true,authoritative:false,review:{no_case_identity_inferred:true,event_reviews:[{window:'current',event_id:'sensitive-event',candidate_resource_pairs:[]}]}};
test('clear discards in-flight authorized reconstruction response',async()=>{
  const h=setup();h.click('loadReconstructionReview');await tick();assert.equal(h.pending.length,1);
  h.click('clearReconstructionReview');h.pending[0].resolve({ok:true,json:async()=>permitted});await tick();
  assert.equal(h.controls.get('reconstructionReviewResults').innerHTML,'');
  assert.equal(h.controls.get('loadReconstructionReview').disabled,false);
});
test('refresh invalidates pending response even when overview is inactive',async()=>{
  const h=setup();h.click('loadReconstructionReview');await tick();
  h.click('refreshWorkflowEvidence');h.pending[0].resolve({ok:true,json:async()=>permitted});await tick();
  assert.equal(h.controls.get('reconstructionReviewResults').innerHTML,'');
  assert.equal(h.controls.get('loadReconstructionReview').disabled,false);
});
test('stale earlier response cannot overwrite newer permitted inspection',async()=>{
  const h=setup();h.click('loadReconstructionReview');await tick();
  h.click('clearReconstructionReview');h.click('loadReconstructionReview');await tick();
  h.pending[1].resolve({ok:true,json:async()=>permitted});await tick();
  assert.match(h.controls.get('reconstructionReviewResults').innerHTML,/sensitive-event/);
  h.pending[0].resolve({ok:true,json:async()=>({...permitted,review:{...permitted.review,event_reviews:[{event_id:'stale-event'}]}})});await tick();
  assert.doesNotMatch(h.controls.get('reconstructionReviewResults').innerHTML,/stale-event/);
});

test('clear during delayed JSON decoding never displays revoked evidence',async()=>{
  const h=setup();h.click('loadReconstructionReview');await tick();
  const decoding=deferred();
  h.pending[0].resolve({ok:true,json:()=>decoding.promise});await tick();
  h.click('clearReconstructionReview');
  decoding.resolve(permitted);await tick();
  assert.equal(h.controls.get('reconstructionReviewResults').innerHTML,'');
  assert.equal(h.controls.get('reconstructionReviewResults').textContent,'');
  assert.equal(h.controls.get('loadReconstructionReview').disabled,false);
});
test('permission denial after an earlier successful inspection removes prior evidence',async()=>{
  const h=setup();h.click('loadReconstructionReview');await tick();
  h.pending[0].resolve({ok:true,json:async()=>permitted});await tick();
  assert.match(h.controls.get('reconstructionReviewResults').innerHTML,/sensitive-event/);
  h.click('loadReconstructionReview');await tick();
  h.pending[1].resolve({ok:false,status:403});await tick();
  const result=h.controls.get('reconstructionReviewResults');
  assert.doesNotMatch(result.innerHTML,/sensitive-event/);
  assert.match(result.textContent,/Access denied or grant expired/);
  assert.equal(h.controls.get('loadReconstructionReview').disabled,false);
});
