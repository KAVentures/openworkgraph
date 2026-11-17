import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const source=fs.readFileSync(path.join(root,'dashboard/agent_preflight.js'),'utf8');

test('agent preflight parses and keeps explicit authenticated local-only preflight',()=>{
  assert.doesNotThrow(()=>new Function(source));
  assert.match(source,/window\.__owgAuthReady/);
  assert.match(source,/\/v1\/process-design\/preflight/);
  assert.match(source,/No agent, tool, or business action was executed/);
});

test('in-flight preflight cannot overwrite a changed file selection',()=>{
  assert.match(source,/selectionGeneration\+\+/);
  assert.match(source,/if\(generation!==selectionGeneration\|\|file\.files\?\.\[0\]!==selected\|\|requestId!==requestGeneration\)return;[\s\S]*?const response=await fetch/);
  assert.match(source,/generation!==selectionGeneration\|\|file\.files\?\.\[0\]!==selected/);
  assert.match(source,/if\(generation===selectionGeneration&&requestId===requestGeneration\)run\.disabled=false/);
  assert.match(source,/if\(generation===selectionGeneration&&requestId===requestGeneration && !controller\.signal\.aborted\)[\s\S]*?status\.textContent='Preflight unavailable/);
});

import vm from 'node:vm';

function deferred(){
  let resolve,reject;
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
  return {promise,resolve,reject};
}
function harness(auth){
  const nodes=new Map();
  function element(tag){
    const handlers={};
    const node={
      tag,style:{},children:[],files:[],disabled:false,textContent:'',
      set id(value){this._id=value;nodes.set(value,this);},
      get id(){return this._id;},
      setAttribute(){},
      appendChild(child){this.children.push(child);return child;},
      append(...children){this.children.push(...children);},
      replaceChildren(...children){this.children=[...children];},
      addEventListener(name,fn){handlers[name]=fn;},
      dispatch(name){return handlers[name]?.();},
    };
    return node;
  }
  const panel=element('main');nodes.set('panel-evidence',panel);
  const document={
    readyState:'complete',
    createElement:element,
    getElementById:id=>nodes.get(id)||null,
    body:element('body'),
  };
  const calls=[];
  const pending=[];
  const fetch=(url,options)=>{
    calls.push({url,options});
    const d=deferred();pending.push(d);return d.promise;
  };
  vm.runInNewContext(source,{document,window:{__owgAuthReady:auth},fetch,Blob,JSON,URL,AbortController,setTimeout});
  const file=nodes.get('owgAgentPreflightFile');
  const run=nodes.get('owgAgentPreflightRun');
  const status=nodes.get('owgAgentPreflightStatus');
  const result=nodes.get('owgAgentPreflightReport');
  const select=selected=>{file.files=selected?[selected]:[];file.dispatch('change');};
  return {file,run,status,result,select,calls,pending};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const validReport={
  format:'openworkgraph.agent-preflight-report.v1',
  structural_review_passed:true,
  target_step_count:1,candidate_step_count:1,synthetic_case_count:0,
  plan_blockers:[],synthetic_cases:[],
};
const reply=(report=validReport)=>({ok:true,json:async()=>report});

test('authenticated preflight never submits a superseded selection',async()=>{
  const auth=deferred();
  const ui=harness(auth.promise);
  const old={size:12,name:'old.json'},newer={size:12,name:'new.json'};
  ui.select(old);
  const running=ui.run.dispatch('click');
  ui.select(newer);
  auth.resolve(true);
  await running;
  assert.equal(ui.calls.length,0);
  assert.equal(ui.run.disabled,false);
  assert.equal(ui.result.children.length,0);
  assert.match(ui.status.textContent,/File selected/);
});

test('delayed old success and failure cannot overwrite a newer selection',async()=>{
  const ui=harness(Promise.resolve(true));
  const old={size:12,name:'old.json'},newer={size:12,name:'new.json'};
  ui.select(old);
  const first=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls.length,1);
  ui.select(newer);
  const second=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls.length,2);
  ui.pending[1].resolve(reply());
  await second;
  assert.match(ui.status.textContent,/complete/);
  const current=ui.result.children.length;
  ui.pending[0].resolve({ok:false,status:403,json:async()=>({detail:'old denied'})});
  await first;
  assert.match(ui.status.textContent,/complete/);
  assert.equal(ui.result.children.length,current);
  assert.equal(ui.run.disabled,false);
});

test('current denied request is recoverable with a new explicit click',async()=>{
  const ui=harness(Promise.resolve(true));
  ui.select({size:12,name:'plan.json'});
  const first=ui.run.dispatch('click');
  await flush();
  ui.pending[0].resolve({ok:false,status:403,json:async()=>({detail:'Denied'})});
  await first;
  assert.match(ui.status.textContent,/Preflight unavailable: Denied/);
  assert.equal(ui.run.disabled,false);
  const retry=ui.run.dispatch('click');
  await flush();
  ui.pending[1].resolve(reply());
  await retry;
  assert.match(ui.status.textContent,/complete/);
  assert.ok(ui.result.children.length>0);
});

test('two explicit requests for the same selected plan only display the latest result',async()=>{
  const ui=harness(Promise.resolve(true));
  ui.select({size:12,name:'plan.json'});
  const first=ui.run.dispatch('click');
  await flush();
  // A second click can occur after a change event re-enables the button,
  // even when the selected File object remains identical.
  ui.file.dispatch('change');
  const second=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls.length,2);
  ui.pending[1].resolve(reply());
  await second;
  assert.match(ui.status.textContent,/complete/);
  const current=ui.result.children.length;
  ui.pending[0].resolve({ok:false,status:403,json:async()=>({detail:'stale denial'})});
  await first;
  assert.match(ui.status.textContent,/complete/);
  assert.equal(ui.result.children.length,current);
  assert.equal(ui.run.disabled,false);
});


test('changing selected file cancels the superseded in-flight request without hiding the new selection',async()=>{
  const ui=harness(Promise.resolve(true));
  ui.select({size:12,name:'old.json'});
  const first=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls.length,1);
  assert.equal(ui.calls[0].options.signal.aborted,false);
  ui.select({size:12,name:'new.json'});
  assert.equal(ui.calls[0].options.signal.aborted,true);
  assert.equal(ui.run.disabled,false);
  ui.pending[0].reject(Object.assign(new Error('aborted'),{name:'AbortError'}));
  await first;
  assert.match(ui.status.textContent,/File selected/);
  assert.equal(ui.result.children.length,0);
  const second=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls[1].options.signal.aborted,false);
  ui.pending[1].resolve(reply());
  await second;
  assert.match(ui.status.textContent,/complete/);
});

test('explicit retry cancels an obsolete request for the same file',async()=>{
  const ui=harness(Promise.resolve(true));
  const selected={size:12,name:'plan.json'};
  ui.select(selected);
  const first=ui.run.dispatch('click');
  await flush();
  ui.file.dispatch('change');
  const second=ui.run.dispatch('click');
  await flush();
  assert.equal(ui.calls[0].options.signal.aborted,true);
  assert.equal(ui.calls[1].options.signal.aborted,false);
  ui.pending[1].resolve(reply());
  await second;
  ui.pending[0].reject(new Error('aborted'));
  await first;
  assert.match(ui.status.textContent,/complete/);
});
