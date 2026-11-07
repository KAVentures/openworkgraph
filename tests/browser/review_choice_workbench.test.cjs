/* Executable browser-like review choice acceptance; run: node tests/browser/review_choice_workbench.test.cjs */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Node {
 constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.disabled=false;this.textContent='';}
 append(...xs){this.children.push(...xs);}
 replaceChildren(...xs){this.children=[...xs];}
 setAttribute(){}
 addEventListener(k,fn){this.listeners[k]=fn;}
}
const basis=()=>({format:'openworkgraph.review-choices.v1',idea:{idea_id:'idea-1',revision:3,state:'approved'},process_status:'current',process_reference:{revision:1},dataset_origins:[],dataset_origin_check_available:true,projects_truncated:false,projects:[{project_id:'p1',idea_revision:3,spec_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),blockers:[],execution_authorized:false,review_required:true}],execution_authorized:false,source_read_authorized:false,verified_business_outcomes:false});
async function scenario(title,responses,check){
 const panel=new Node('section'),doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:null,addEventListener(k,fn){this[k]=fn;}};
 let calls=0;
 const context={document:doc,window:{__owgAuthReady:Promise.resolve()},fetch:async(_url,options)=>{assert.equal(options.cache,'no-store');const x=responses[calls++];if(x instanceof Error)throw x;return {ok:x.ok!==false,status:x.status||200,json:async()=>x.data||x};}};
 vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../dashboard/review_choice_workbench.js'),'utf8'),context);
 doc.DOMContentLoaded();
 const card=panel.children[0];assert.equal(card.id,'owgReviewChoiceWorkbench');
 const [input,load,status,results,details]=card.children.slice(3);
 input.value='idea-1';await load.onclick();
 await check({input,load,status,results,details,calls:()=>calls});
 console.log('PASS '+title);
}
const getControls=results=>[results.children.find(x=>x.tagName==='select'),results.children.find(x=>x.tagName==='button')];
(async()=>{
 await scenario('successful read-only review',[basis(),basis()],async({results,details,status,calls})=>{const [select,review]=getControls(results);assert.ok(select&&review);select.value='p1';await review.onclick();assert.equal(calls(),2);assert.match(details.children.map(x=>x.textContent).join(' '),/Specification SHA-256/);assert.match(status.textContent,/Read-only choice inspected/);});
 await scenario('stale revision invalidates choice',[basis(),{...basis(),idea:{...basis().idea,revision:4}}],async({results,details,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(review.disabled,true);assert.equal(results.children.length,0);assert.equal(details.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('403 denies review and clears choice',[basis(),{ok:false,status:403}],async({results,details,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(review.disabled,true);assert.equal(results.children.length,0);assert.equal(details.children.length,0);assert.match(status.textContent,/Review failed/);});
 await scenario('duplicate project identity rejected',[{...basis(),projects:[basis().projects[0],{...basis().projects[0],archive_sha256:'c'.repeat(64)}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('malformed project digest rejected',[{...basis(),projects:[{...basis().projects[0],spec_sha256:'not-a-digest'}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('invalid revision rejected',[{...basis(),idea:{...basis().idea,revision:-1}}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('changed unselected project invalidates prior review',[{...basis(),projects:[basis().projects[0],{...basis().projects[0],project_id:'p2'}]},{...basis(),projects:[basis().projects[0],{...basis().projects[0],project_id:'p2',archive_sha256:'c'.repeat(64)}]}],async({results,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(results.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('changed opportunity state invalidates review',[basis(),{...basis(),idea:{...basis().idea,state:'rejected'}}],async({results,status})=>{const [select,review]=getControls(results);select.value='p1';await review.onclick();assert.equal(results.children.length,0);assert.match(status.textContent,/Review basis changed/);});
 await scenario('future project revision rejected',[{...basis(),projects:[{...basis().projects[0],idea_revision:4}]}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
 await scenario('inconsistent authorization rejected',[{...basis(),execution_authorized:true}],async({results,status})=>{assert.equal(results.children.length,0);assert.match(status.textContent,/Incomplete or inconsistent/);});
})().catch(e=>{console.error(e);process.exitCode=1;});
