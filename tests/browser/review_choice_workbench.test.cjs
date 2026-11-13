/* Executable browser-like review choice acceptance; run: node tests/browser/review_choice_workbench.test.cjs */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Node {
 constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.disabled=false;this.textContent='';}
 append(...xs){this.children.push(...xs);}
 replaceChildren(...xs){this.children=[...xs];}
 setAttribute(){}
 addEventListener(k,fn){this.listeners[k]=fn;}
}
const basis=()=>({format:'openworkgraph.review-choices.v1',idea:{idea_id:'idea-1',revision:3,state:'approved'},process_status:'current',process_reference:{revision:1},dataset_origins:[],dataset_origin_check_available:true,project_set_sha256:'d'.repeat(64),projects_truncated:false,projects:[{project_id:'p1',idea_revision:3,spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],execution_authorized:false,review_required:true}],execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false});
async function scenario(title,responses,check,withPipeline=false,reviewRead=null){
 const panel=new Node('section'),pipeline=new Node('section');pipeline.scrollIntoView=()=>{};
 const doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:(withPipeline&&id==='owgPipelineCard'?pipeline:null),addEventListener(k,fn){this[k]=fn;}};
 let opened=0, lastEvent=null;
 let calls=0,reviewReads=0;
 const context={document:doc,CustomEvent:class {constructor(type,init){this.type=type;this.detail=init.detail;}},window:{__owgAuthReady:Promise.resolve(),dispatchEvent:e=>{opened++;lastEvent=e;}},fetch:async(url,options)=>{assert.equal(options.cache,'no-store');
   // The saved-review read is answered separately so scenarios keep counting their decision calls.
   if(!options.method&&/\/review\/[^/]+$/.test(String(url))){const result=reviewRead?reviewRead(++reviewReads):null;return result||{ok:true,status:200,json:async()=>({project_id:'p1',revision:0,decision:'draft',reviewer_notes:'',execution_authorized:false})};}
   if(options.method==='POST'&&/\/review$/.test(String(url))){const b=JSON.parse(options.body);const x=responses[calls++];if(x&&x.project_id)return {ok:true,status:200,json:async()=>x};calls--;return {ok:true,status:200,json:async()=>({project_id:'p1',revision:b.expected_review_revision+1,decision:b.decision,execution_authorized:false})};}
   const x=responses[calls++];if(x instanceof Error)throw x;return {ok:x.ok!==false,status:x.status||200,json:async()=>x.data||x};}};
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../dashboard/review_choice_workbench.js'),'utf8'),context);
 doc.DOMContentLoaded();
 const card=panel.children[0];assert.equal(card.id,'owgReviewChoiceWorkbench');
 const [input,load,status,results,details]=card.children.slice(3);
 input.value='idea-1';await load.onclick();
 await check({input,load,status,results,details,calls:()=>calls,opened:()=>opened,lastEvent:()=>lastEvent});
 console.log('PASS '+title);
}
const getControls=results=>[results.children.find(x=>x.tagName==='select'),results.children.find(x=>x.tagName==='button')];
(async()=>{
 await scenario('successful read-only review',[basis(),basis()],async({results,details,status,calls})=>{const [select,review]=getControls(results);assert.ok(select&&review);select.value='p1';await review.onclick();assert.equal(calls(),2);assert.match(details.children.map(x=>x.textContent).join(' '),/Specification SHA-256/);const provenance=details.children.find(x=>x.tagName==='section');assert.ok(provenance,'review must show provenance before consent');assert.equal(provenance.children[0].textContent,'Recorded workflow provenance');assert.ok(provenance.children.some(x=>x.textContent==='Workflow revision: 1'));assert.ok(provenance.children.some(x=>x.textContent==='Dataset origin records: 0'));assert.ok(provenance.children.some(x=>x.tagName==='ul'&&x.children[0].textContent==='No dataset origin linked'));assert.match(status.textContent,/Read-only choice inspected/);});
 await scenario('decision navigation rechecks server and fails closed on stale review',[basis(),basis(),
   {format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
    spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
    execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false},
   {ok:false,status:409}],async({results,details,status,calls,opened})=>{
   const [select,review]=getControls(results);select.value='p1';await review.onclick();
   const confirm=details.children.find(x=>x.textContent==='Revalidate review basis on server');
   assert.ok(confirm);await confirm.onclick();
   const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
   await Promise.resolve();await Promise.resolve();await details.children.find(x=>x.textContent==='Approve reviewed choice').onclick();
   assert.ok(proceed);const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input'))?.children.find(x=>x.tagName==='input');assert.ok(consent);assert.equal(proceed.disabled,true);consent.checked=true;consent.listeners.change();await proceed.onclick();
   assert.equal(calls(),4);assert.equal(opened(),0);
   assert.equal(results.children.length,0);assert.match(status.textContent,/Decision review expired/);
 },true);
 await scenario('reviewed choice reaches exact-project pipeline event after two server preflights',[basis(),basis(),
   {format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
    spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
    execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false},
   // The decision handoff now requires a durable human approval (reviewed-handoff preflight).
   {format:'openworkgraph.reviewed-handoff-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
    spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),review_revision:1,human_review_approved:true,
    execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false}],
   async({results,details,opened,lastEvent,calls})=>{
     const [select,review]=getControls(results);select.value='p1';await review.onclick();
     await details.children.find(x=>x.textContent==='Revalidate review basis on server').onclick();
     const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input'))?.children.find(x=>x.tagName==='input');
     const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
     assert.ok(consent,'explicit inspection consent must be visible');
     assert.equal(proceed.disabled,true,'navigation is disabled without consent');
     await proceed.onclick();assert.equal(opened(),0,'programmatic click cannot bypass consent');
     await Promise.resolve();await Promise.resolve();
     assert.equal(proceed.disabled,true,'consent alone cannot bypass durable approval');
     await details.children.find(x=>x.textContent==='Approve reviewed choice').onclick();
     consent.checked=true;consent.listeners.change();assert.equal(proceed.disabled,false);
     await proceed.onclick();
     assert.equal(calls(),4);assert.equal(opened(),1);
     assert.equal(lastEvent().type,'owg-pipeline-open');
     assert.equal(lastEvent().detail.idea_revision,3);
     assert.equal(lastEvent().detail.review_revision,1,'destination receives exact server-approved review revision');
     assert.equal(lastEvent().detail.idea_state,basis().idea.state,'handoff must pin inspected opportunity state');
     assert.equal(lastEvent().detail.project_id,'p1');
     assert.equal(lastEvent().detail.review_basis.project_set_sha256,'d'.repeat(64));
     assert.equal(lastEvent().detail.review_basis.idea_state,basis().idea.state);
     assert.equal(lastEvent().detail.review_basis.project_id,'p1');
     assert.deepEqual(Array.from(lastEvent().detail.review_basis.blockers),[]);
     assert.equal(lastEvent().detail.spec_sha256,'a'.repeat(64));
     assert.equal(lastEvent().detail.archive_sha256,'b'.repeat(64));
   },true);


 // Consent withdrawn during the authenticated handoff request must invalidate
 // that request even if the checkbox is reselected before the server replies.
 {
   let finish;
   const pending=new Promise(resolve=>{finish=resolve;});
   const preflight={format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
     spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
     execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false};
   const handoff={format:'openworkgraph.reviewed-handoff-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
     spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),review_revision:1,human_review_approved:true,
     execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false};
   await scenario('withdrawn and rechecked consent invalidates in-flight handoff',
     [basis(),basis(),preflight,{data:pending}],async({results,details,opened,status})=>{
       const [select,review]=getControls(results);select.value='p1';await review.onclick();
       await details.children.find(x=>x.textContent==='Revalidate review basis on server').onclick();
       await Promise.resolve();await Promise.resolve();
       await details.children.find(x=>x.textContent==='Approve reviewed choice').onclick();
       const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input')).children.find(x=>x.tagName==='input');
       const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
       consent.checked=true;consent.listeners.change();
       const inFlight=proceed.onclick();
       await Promise.resolve();await Promise.resolve();
       consent.checked=false;consent.listeners.change();
       consent.checked=true;consent.listeners.change();
       finish(handoff);await inFlight;
       assert.equal(opened(),0,'rechecked consent cannot revive an in-flight request');
       assert.match(status.textContent,/Decision review expired/);
     },true);
 }
 // A malformed save receipt cannot be treated as an approved, durable decision.
 // Reload the authoritative review revision instead of trusting a response
 // that echoes the wrong decision or a non-monotonic revision.
 for(const [label,receipt] of [
   ['wrong decision',{decision:'draft',revision:1}],
   ['non-monotonic revision',{decision:'approved',revision:0}],
   ['missing revision',{decision:'approved'}]
 ]){
   await scenario('reject '+label+' in saved approval receipt',[
     basis(),basis(),
     {format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
       spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
       execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false},
     {project_id:'p1',execution_authorized:false,...receipt}
   ],async({results,details,opened})=>{
     const [select,review]=getControls(results);select.value='p1';await review.onclick();
     await details.children.find(x=>x.textContent==='Revalidate review basis on server').onclick();
     // Saved-review GET is the authoritative draft (revision zero).
     await Promise.resolve();await Promise.resolve();
     const approve=details.children.find(x=>x.textContent==='Approve reviewed choice');
     const state=details.children.find(x=>x.tagName==='p'&&/Saved review:/.test(x.textContent));
     assert.ok(approve);assert.ok(state);assert.equal(approve.disabled,false);
     await approve.onclick();
     assert.match(state.textContent,/Saved review: draft/);
     assert.doesNotMatch(state.textContent,/Saved approved/);
     assert.equal(opened(),0);
   },true);
 }
 // A failed save followed by denied authoritative refresh must not reuse
 // an earlier approved state, even if the operator checks consent again.
 await scenario('denied review refresh cannot resurrect prior approval',[
   basis(),basis(),
   {format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
     spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
     execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false},
   {project_id:'p1',decision:'draft',revision:2,execution_authorized:false}
 ],async({results,details,opened})=>{
   const [select,review]=getControls(results);select.value='p1';await review.onclick();
   await details.children.find(x=>x.textContent==='Revalidate review basis on server').onclick();
   await Promise.resolve();await Promise.resolve();
   const approve=details.children.find(x=>x.textContent==='Approve reviewed choice');
   const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input'))?.children.find(x=>x.tagName==='input');
   const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
   assert.equal(proceed.disabled,true);
   await approve.onclick();
   consent.checked=true;consent.listeners.change();
   assert.equal(proceed.disabled,true,'unverified review must never enable handoff');
   await proceed.onclick();assert.equal(opened(),0);
 },true,(read)=>read===1?
   {ok:true,status:200,json:async()=>({project_id:'p1',revision:1,decision:'approved',reviewer_notes:'',execution_authorized:false})}:
   {ok:false,status:403,json:async()=>({})});
 // A server response approving another revision or export must never open a handoff.
 for(const [label,override] of [
   ['different review revision',{review_revision:1}],
   ['different specification digest',{spec_sha256:'f'.repeat(64)}],
   ['different archive digest',{archive_sha256:'f'.repeat(64)}],
   ['different opportunity revision',{idea_revision:4}],
   ['missing review revision',{review_revision:undefined}]
 ]){
   const preflight={format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
     spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],review_required:true,
     execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false};
   const handoff={format:'openworkgraph.reviewed-handoff-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',project_set_sha256:'d'.repeat(64),
     spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),review_revision:1,human_review_approved:true,
     execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false,...override};
   await scenario('handoff denies '+label,[basis(),basis(),preflight,handoff],async({results,details,opened,status})=>{
     const [select,review]=getControls(results);select.value='p1';await review.onclick();
     await details.children.find(x=>x.textContent==='Revalidate review basis on server').onclick();
     const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input'))?.children.find(x=>x.tagName==='input');
     const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
     assert.ok(consent,'explicit inspection consent must be visible');
     assert.equal(proceed.disabled,true,'navigation is disabled without consent');
     await proceed.onclick();assert.equal(opened(),0,'programmatic click cannot bypass consent');
     await Promise.resolve();await Promise.resolve();
     assert.equal(proceed.disabled,true,'consent alone cannot bypass durable approval');
     await details.children.find(x=>x.textContent==='Approve reviewed choice').onclick();
     consent.checked=true;consent.listeners.change();assert.equal(proceed.disabled,false);
     await proceed.onclick();
     assert.equal(opened(),0,'stale review or export must not navigate');
     assert.match(status.textContent,/Decision review expired/);
   },true);
 }
 // An in-flight inspection invalidated by editing the ID must not strand the load button.
 {
   const panel=new Node('section');
   const doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:null,
     addEventListener(k,fn){this[k]=fn;}};
   let finish;
   const pending=new Promise(resolve=>{finish=resolve;});
   const context={document:doc,window:{__owgAuthReady:Promise.resolve()},
     fetch:async()=>pending};
   vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../dashboard/review_choice_workbench.js'),'utf8'),context);
   doc.DOMContentLoaded();
   const [input,load,status]=panel.children[0].children.slice(3);
   input.value='idea-1';
   const inspection=load.onclick();
   await Promise.resolve();await Promise.resolve();
   assert.equal(load.disabled,true);
   input.value='idea-2';input.listeners.input();
   finish({ok:true,json:async()=>basis()});
   await inspection;
   assert.equal(load.disabled,false);
   assert.match(status.textContent,/Selection changed/);
   console.log('PASS invalidated inspection remains retryable');
 }
 // Editing an opportunity permits a fresh inspection while the old request is pending.
 // Completion of the obsolete request must not reset the newer request's busy state.
 {
   const panel=new Node('section');
   const doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:null,
     addEventListener(k,fn){this[k]=fn;}};
   const finishes=[];
   const context={document:doc,window:{__owgAuthReady:Promise.resolve()},
     fetch:()=>new Promise(resolve=>finishes.push(resolve))};
   vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../dashboard/review_choice_workbench.js'),'utf8'),context);
   doc.DOMContentLoaded();
   const [input,load,status,results]=panel.children[0].children.slice(3);
   input.value='idea-1';
   const first=load.onclick();
   await Promise.resolve();await Promise.resolve();
   assert.equal(load.disabled,true);
   input.value='idea-2';input.listeners.input();
   assert.equal(load.disabled,false);
   const second=load.onclick();
   await Promise.resolve();await Promise.resolve();
   assert.equal(load.disabled,true);
   finishes[0]({ok:true,json:async()=>basis()});
   await first;
   assert.equal(load.disabled,true,'obsolete inspection must not unlock newer request');
   finishes[1]({ok:true,json:async()=>({...basis(),idea:{...basis().idea,idea_id:'idea-2'}})});
   await second;
   assert.equal(load.disabled,false);
   assert.equal(results.children.length>0,true);
   assert.match(status.textContent,/Choose an exact export/);
   console.log('PASS overlapping opportunity inspections retain current busy state');
 }
 // Recovery ID changes during a pending revoke must unlock loading a new identity.
 {
   const panel=new Node('section');
   const doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:null,
     addEventListener(k,fn){this[k]=fn;}};
   let finishRevoke;
   const context={document:doc,window:{__owgAuthReady:Promise.resolve()},fetch:async(url,options)=>{
     if(options.method==='POST')return new Promise(resolve=>{finishRevoke=resolve;});
     return {ok:true,json:async()=>({format:'openworkgraph.human-review.v1',
       idea_id:'idea-1',project_id:'p1',revision:2,decision:'approved',execution_authorized:false})};
   }};
   vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../dashboard/review_choice_workbench.js'),'utf8'),context);
   doc.DOMContentLoaded();
   const recovery=panel.children[0].children.at(-1);
   const [ideaLabel,projectLabel,load,revoke,status]=recovery.children.slice(2);
   const idea=ideaLabel.children[0],project=projectLabel.children[0];
   idea.value='idea-1';project.value='p1';
   await load.onclick();
   assert.equal(revoke.disabled,false);
   const pending=revoke.onclick();
   await Promise.resolve();await Promise.resolve();
   assert.equal(load.disabled,true);
   project.value='p2';project.listeners.input();
   assert.equal(load.disabled,false,'new identity must remain recoverable');
   assert.equal(revoke.disabled,true,'old approval must not apply to new identity');
   finishRevoke({ok:true,json:async()=>({idea_id:'idea-1',project_id:'p1',revision:3,
     decision:'revoked',execution_authorized:false})});
   await pending;
   assert.match(status.textContent,/IDs changed/);
   console.log('PASS recovery identity change during revoke');
 }
 await scenario('stale revision invalidates choice',[basis(),{...basis(),idea:{...basis().idea,revision:4}}],async({results,details,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(review.disabled,true);assert.equal(results.children.length,0);assert.equal(details.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('403 denies review and clears choice',[basis(),{ok:false,status:403}],async({results,details,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(review.disabled,true);assert.equal(results.children.length,0);assert.equal(details.children.length,0);assert.match(status.textContent,/Review failed/);});
 await scenario('duplicate project identity rejected',[{...basis(),projects:[basis().projects[0],{...basis().projects[0],archive_sha256:'c'.repeat(64)}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('oversized supposedly complete project history rejected',[{...basis(),projects:Array.from({length:65},(_,i)=>({...basis().projects[0],project_id:'p'+i}))}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('truncated history rejected despite valid selected export',[{...basis(),projects_truncated:true}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('malformed project digest rejected',[{...basis(),projects:[{...basis().projects[0],spec_sha256:'not-a-digest'}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('invalid revision rejected',[{...basis(),idea:{...basis().idea,revision:-1}}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('changed unselected project invalidates prior review',[{...basis(),projects:[basis().projects[0],{...basis().projects[0],project_id:'p2'}]},{...basis(),projects:[basis().projects[0],{...basis().projects[0],project_id:'p2',archive_sha256:'c'.repeat(64)}]}],async({results,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(results.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('changed opportunity state invalidates review',[basis(),{...basis(),idea:{...basis().idea,state:'rejected'}}],async({results,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(results.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('future project revision rejected',[{...basis(),projects:[{...basis().projects[0],idea_revision:4}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('inconsistent authorization rejected',[{...basis(),execution_authorized:true}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
})().catch(e=>{console.error(e);process.exitCode=1;});
