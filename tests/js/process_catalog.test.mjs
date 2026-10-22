import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../../dashboard/process_catalog.js',import.meta.url),'utf8');
class Element {
  constructor(tag,document){this.tag=tag;this.document=document;this.children=[];this.handlers={};this.attrs={};this.style={};this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.append(node);return node;}
  prepend(node){this.children.unshift(node);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,fn){this.handlers[k]=fn;}
  scrollIntoView(){}
  walk(){return [this,...this.children.flatMap(n=>n.walk())];}
}
function fixture({shadowResponse,bpmnResponse}={}){
  const document={readyState:'complete'};
  const root=new Element('main',document);root.id='panel-evidence';
  document.querySelector=()=>null;
  document.getElementById=id=>root.walk().find(n=>n.id===id);
  document.createElement=tag=>new Element(tag,document);document.createElementNS=(_,tag)=>new Element(tag,document);
  const draft={as_is:{path:['Open','Review','Review','Close'],supporting_case_count:2},
    to_be:{proposed_path:['Open','Review','Close']},business_goal_declared_by_user:'Resolve case safely'};
  const requests=[];let saved=null;
  const window={__owgAuthReady:Promise.resolve(true),confirm:()=>true,owgProcessDesigner:{reviewedDraft:()=>draft}};
  const fetch=async(path,options)=>{
    requests.push({path,options});
    if(path==='/v1/process-catalog/import-bpmn')return {ok:true,json:async()=>bpmnResponse()};
    const body=JSON.parse(options.body);
    if(path==='/v1/process-catalog/shadow-review')return {ok:true,json:async()=>shadowResponse(body)};
    if(body.expected_revision!==(saved?.revision||0))return {ok:false,json:async()=>({detail:'Revision changed'})};
    saved={...body,workflow_id:'process_test',revision:(saved?.revision||0)+1};
    return {ok:true,json:async()=>saved};
  };
  vm.runInNewContext(source,{document,window,fetch,URLSearchParams});
  return {document,root,window,requests,button:text=>root.walk().find(n=>n.tag==='button'&&n.textContent===text)};
}

test('designer bridge preserves duplicate AS-IS activities and exact TO-BE positions',()=>{
  const {window}=fixture();
  const m=window.__owgProcessCatalogModel.linearModel({as_is:{path:['A','A','B'],supporting_case_count:2},
    to_be:{proposed_path:['A','B','A']},business_goal_declared_by_user:'Resolve case'});
  assert.deepEqual(Array.from(m.baseline_path),['A','A','B']);
  assert.deepEqual(Array.from(m.activities,a=>a.id),['step_1','step_2','step_3']);
  assert.equal(m.activities[0].label,m.activities[2].label);
  assert.equal(m.execution_authorized,false);assert.equal(m.outcomes_verified,false);
  assert.equal(m.transitions[1].target,'step_3');
});

test('shadow report inspection is explicit, temporary and ignores responses after editing',async()=>{
  let release;const gate=new Promise(resolve=>release=resolve);
  const {window,root,document,requests,button}=fixture({shadowResponse:async body=>{
    await gate;
    return {process_reference:{workflow_id:body.workflow_id,revision:body.revision},
      references_latest_revision_at_inspection:true,report:{matching_case_count:0,case_count:1,review_required:true,
      cases:[{case_id:'case_1',category:'normal',reason:'unexpected_clarification'}]}};
  }});
  await window.owgProcessCatalog.saveDesigner();
  const file=root.walk().find(node=>node.attrs['aria-label']==='Choose shadow evaluation report');
  file.files=[{size:20,text:async()=>JSON.stringify({format:'selected_minimized_report'})}];file.handlers.change();
  assert.equal(requests.length,1);
  const pending=button('Inspect report against saved revision').handlers.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,2);assert.equal(JSON.parse(requests[1].options.body).revision,1);
  const goal=root.walk().find(node=>node.attrs['aria-label']==='Business goal');goal.value='Changed draft';goal.handlers.input();
  release();await pending;
  assert.equal(document.getElementById('owgCatalogShadowResult').children.length,0);
  await button('Inspect report against saved revision').handlers.click();
  assert(document.getElementById('owgCatalogShadowResult').walk().some(node=>node.textContent==='0 of 1 cases match customer expectations'));
  file.handlers.change();assert.equal(document.getElementById('owgCatalogShadowResult').children.length,0);
  file.files=[{size:30,text:async()=>'{PRIVATE_CASE_VALUE'}];
  await button('Inspect report against saved revision').handlers.click();
  assert.equal(requests.length,3);
  assert(!document.getElementById('owgCatalogStatus').textContent.includes('PRIVATE_CASE_VALUE'));
});

test('no catalog requests until explicit action; edits invalidate review and saved revisions use optimistic checks',async()=>{
  const {window,root,document,requests,button}=fixture();
  assert.equal(requests.length,0);
  await window.owgProcessCatalog.saveDesigner();
  assert.equal(requests.length,1);
  assert.equal(JSON.parse(requests[0].options.body).user_confirmed,true);
  const check=document.getElementById('owgCatalogReviewed');check.checked=true;check.handlers.change();
  const owner=root.walk().find(n=>n.attrs['aria-label']==='Process owner');owner.value='Operations';owner.handlers.input();
  assert.equal(check.checked,false);
  await button('Save new revision').handlers.click();
  assert.equal(requests.length,1);
  assert.match(document.getElementById('owgCatalogStatus').textContent,/Review this exact/);
  check.checked=true;check.handlers.change();
  await button('Save new revision').handlers.click();
  assert.equal(requests.length,2);
  const second=JSON.parse(requests[1].options.body);
  assert.equal(second.workflow_id,'process_test');assert.equal(second.expected_revision,1);
  assert.equal(second.process_model.owner_label,'Operations');
  assert(root.walk().some(n=>n.textContent==='Review process · revision 2'));
  assert.equal(document.getElementById('owgCatalogReviewed').checked,false);
});

test('BPMN preview is explicit and an imported draft still requires separate exact review and save',async()=>{
 let value;
 const {window,root,document,requests,button}=fixture({bpmnResponse:()=>value});
 const model=window.__owgProcessCatalogModel.linearModel({as_is:{path:['Imported design'],supporting_case_count:0},
   to_be:{proposed_path:['Review']},business_goal_declared_by_user:'Review imported design'});
 model.baseline_source='user_declared';model.baseline_coverage='user_declared_design';
 value={format:'openworkgraph.bpmn-draft-import.v1',process_model:model,source_sha256:'a'.repeat(64),
   warnings:['rules_require_human_confirmation'],label_corrections:[],documentation_notes:[{source_node_id:'PRIVATE_XML',text:'<script>data_only()</script>'}]};
 const file=root.walk().find(node=>node.attrs['aria-label']==='Choose BPMN process design');
 file.files=[{size:100}];file.handlers.change();assert.equal(requests.length,0);
 await button('Preview selected BPMN').handlers.click();assert.equal(requests.length,1);
 assert.equal(requests[0].options.body,file.files[0]);assert.equal(requests[0].options.headers['Content-Type'],'application/bpmn+xml');
 assert(root.walk().some(node=>node.textContent.includes('<script>data_only()</script>')));
 await button('Open imported draft in editor').handlers.click();assert.equal(requests.length,1);
 assert(root.walk().some(node=>node.textContent==='Imported declared design · no observed baseline'));
 assert.equal(document.getElementById('owgCatalogReviewed').checked,false);
 await button('Save new revision').handlers.click();assert.equal(requests.length,1);
 document.getElementById('owgCatalogReviewed').checked=true;document.getElementById('owgCatalogReviewed').handlers.change();
 await button('Save new revision').handlers.click();assert.equal(requests.length,2);
 const body=JSON.parse(requests[1].options.body);
 assert.equal(body.process_model.baseline_coverage,'user_declared_design');assert.equal(body.user_confirmed,true);
 assert.equal(body.process_model.execution_authorized,false);
 assert(!JSON.stringify(body).includes('PRIVATE_XML')&&!JSON.stringify(body).includes('<script>data_only()'));
});

test('BPMN preview arriving after file replacement cannot restore or open the earlier draft',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);
 const {root,document,requests,button}=fixture({bpmnResponse:()=>gate});
 const file=root.walk().find(node=>node.attrs['aria-label']==='Choose BPMN process design');
 file.files=[{size:100}];file.handlers.change();
 const preview=button('Preview selected BPMN').handlers.click();await new Promise(resolve=>setImmediate(resolve));
 file.files=[];file.handlers.change();release({format:'openworkgraph.bpmn-draft-import.v1',PRIVATE_OLD:'must not render'});
 await preview;
 assert.equal(document.getElementById('owgBPMNImportResult').children.length,0);
 assert.equal(button('Open imported draft in editor'),undefined);assert.equal(requests.length,1);
 assert.equal(button('Preview selected BPMN').disabled,true);
});


test('observed drafts preserve baseline labels and evidence references without persisting until review',async()=>{
  const {window,root,document,requests,button}=fixture();
  window.owgProcessCatalog.draftObserved({path:['Mail','Agent tool · inspect','Mail'],count:3,
    basis:'selected_observed_variant',evidenceRefs:['execution:abc','not evidence']});
  assert.equal(requests.length,0);
  assert(root.walk().some(n=>n.textContent==='Review process · unsaved observed proposal'));
  assert(root.walk().some(n=>n.attrs['aria-label']==='Activity step_2'&&n.value==='Activity 2'));
  assert(!button('Download OpenAI Agents project'));
  const goal=root.walk().find(n=>n.attrs['aria-label']==='Business goal');goal.value='Review a selected case';goal.handlers.input();
  const review=document.getElementById('owgCatalogReviewed');review.checked=true;review.handlers.change();
  await button('Save new revision').handlers.click();
  const body=JSON.parse(requests[0].options.body);
  assert.deepEqual(body.process_model.baseline_path,['Mail','Agent tool · inspect','Mail']);
  assert.equal(body.process_model.baseline_coverage,'bounded_candidate_sequence');
  assert.equal(body.process_model.baseline_support_unit,'candidate_executions');
  assert.deepEqual(body.evidence_refs,['execution:abc']);
});

test('designer transfer retains manual conditions by stable IDs when activity labels repeat',()=>{
  const {window}=fixture();
  const m=window.__owgProcessCatalogModel.linearModel({
    as_is:{path:['Review','Review'],supporting_case_count:1},
    to_be:{proposed_path:['Review','Review'],decision_review_ledger:{entries:[{
      step_index:1,at_activity:'Review',kind:'exception_condition',condition_label:'Evidence missing',
      response:'pause_for_review',source:'user_declared_unverified',evaluated_by_owg:false,
      automatic_branch_authorized:false,executable:false
    }]}},business_goal_declared_by_user:'Resolve a selected case'});
  const condition=m.manual_review_conditions[0];
  assert.equal(condition.activity_id,'step_2');
  assert.equal(condition.condition_label,'Evidence missing');
  assert.equal(condition.evaluated_by_owg,false);
  assert.equal(condition.automatic_branch_authorized,false);
  assert.equal(condition.executable,false);
  assert.equal(m.business_rules.length,0);
});
