import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../browser_extension/work_text_capture.js', import.meta.url), 'utf8');

async function sensor({enabled=true, nodes=[], host='docs.example.org', viewportNodes=null}={}) {
  const messages=[], timers=new Map(), listeners={};
  let seq=0, now=100000, changed;
  const context=vm.createContext({
    chrome: {runtime:{sendMessage(m){messages.push(m);return Promise.resolve();}},storage:{
      local:{get:async()=>({openworkgraph_work_text_capture_v1:enabled})},
      onChanged:{addListener(fn){changed=fn;}}
    }},
    location:{hostname:host,pathname:'/project'},
    document:{title:'Project',body:{},documentElement:{},visibilityState:'visible',
      elementFromPoint:viewportNodes?()=>({viewport:true,closest:()=>null,getAttribute:()=>null}):undefined,
      createTreeWalker(root){let i=0;const list=root.viewport?viewportNodes:nodes;return {nextNode:()=>list[i++]||null};}},
    NodeFilter:{SHOW_TEXT:4},MutationObserver:class {observe(){}},
    getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),
    Date:{now:()=>now},setTimeout(fn,delay){const id=++seq;timers.set(id,{fn,delay});return id;},
    clearTimeout(id){timers.delete(id);},
    addEventListener(name,fn){listeners[name]=fn;},
  });
  context.window=context;context.top=context;context.innerHeight=800;context.innerWidth=1200;
  vm.runInContext(source,context);await Promise.resolve();await Promise.resolve();
  return {messages,listeners,disable(){changed({openworkgraph_work_text_capture_v1:{newValue:false}},'local');},
    flush(){now+=30000;for(const [id,t] of [...timers]){timers.delete(id);t.fn();}}};
}
function node(text,top=0,blocked=false,rendered=true){return {nodeValue:text,parentElement:{
  closest:()=>blocked?{}:null,getAttribute:()=>null,parentElement:null,
  getClientRects:()=>rendered?[{}]:[],getBoundingClientRect:()=>({top,bottom:top+20})
}};}

test('actual script sends viewport before older page text and reports bounded coverage',async()=>{
  const s=await sensor({nodes:[node('Old history '.repeat(500),-500),node('Latest decision is to ship tomorrow',100)]});
  s.flush();assert.equal(s.messages.length,1);
  assert.ok(s.messages[0].text.startsWith('Latest decision'));
  assert.ok(s.messages[0].text.length<=4000 && s.messages[0].text.length>=3990);
  assert.equal(s.messages[0].context.capture_truncated,true);
});
test('single oversized text node is explicitly truncated',async()=>{
  const s=await sensor({nodes:[node('x'.repeat(5000))]});s.flush();
  assert.equal(s.messages[0].context.capture_truncated,true);
});
test('off, sensitive and hidden content are not sent by the actual script',async()=>{
  for(const options of [{enabled:false},{host:'accounts.google.com'},
      {nodes:[node('private',0,true),node('hidden',0,false,false)]}]){
    const s=await sensor({nodes:[node('visible')],...options});s.flush();assert.equal(s.messages.length,0);
  }
});
test('pending capture is cancelled on revocation',async()=>{
  const s=await sensor({nodes:[node('visible')]});s.disable();s.flush();assert.equal(s.messages.length,0);
});
test('draft sends final debounced text with independent coverage',async()=>{
  const s=await sensor();const draft={tagName:'TEXTAREA',value:'Draft '.repeat(1000),
    getAttribute:()=>null,closest(selector){return selector.startsWith('textarea')?this:null;}};
  s.listeners.input({target:draft});draft.value='Final draft';s.listeners.input({target:draft});s.flush();
  assert.equal(s.messages.length,1);assert.equal(s.messages[0].text,'Final draft');
  assert.equal(s.messages[0].kind,'draft');assert.equal(s.messages[0].context.capture_truncated,false);
});

test('visible tail is captured before a long old DOM prefix exhausts the budget',async()=>{
 const latest=node('Visible latest decision: reopen the review',100);
 const s=await sensor({nodes:[...Array.from({length:400},()=>node('Old history '.repeat(50),-500)),latest],viewportNodes:[latest]});
 s.flush();assert.ok(s.messages[0].text.startsWith('Visible latest decision'));
 assert.equal(s.messages[0].context.capture_truncated,true);
});
